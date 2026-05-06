"""Phase A fraud_score ヒューリスティック合成 (ADR-014 準拠)

責務: 媒体 API から取得済の指標を合成して campaign-level の fraud_score (0.0-1.0) を算出。
      4 象限分類 (black/gray/white/unknown) を行い、threshold_optimizer / grey_zone 通知に
      連携する出力を生成する。

Phase A は媒体公式 KPI のみを入力としたヒューリスティック。Phase B Week 2-3 で
papa-torb/adtruth fork + 独自カスタムタグの fingerprint / isolation_forest 出力に
段階置換される (ADR-014 §3)。

主要関数:
    - compute_fraud_score(client_id, media, campaigns, baselines=None) -> dict
    - classify_quadrant(fraud_score, cv_rate_pct) -> str
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("bpo")


# ========== ADR-014 §2.4 閾値定義 ==========

FRAUD_THRESHOLD_DEFAULT = 0.60
CV_RATE_THRESHOLD_DEFAULT_PCT = 5.0

# ADR-014 §2.3 加重 (合計 1.00)
WEIGHTS = {
    "freq_anom": 0.25,
    "cvr_low":   0.20,
    "ctr_low":   0.20,
    "cpa_high":  0.15,
    "ctr_high":  0.15,
    "mfa_share": 0.05,    # Phase A は常に 0
}

# 業界フォールバック baseline (Phase A 暫定、ADR-014 §2.2)
INDUSTRY_BASELINES = {
    "beauty_d2c":  {"cvr_pct": 2.0, "cpa_jpy": 6000},
    "ec_d2c":      {"cvr_pct": 2.0, "cpa_jpy": 6000},
    "ec_retail":   {"cvr_pct": 1.5, "cpa_jpy": 4000},
    "default":     {"cvr_pct": 2.0, "cpa_jpy": 6000},
}

# 個別スコア算出時のエッジ条件 (ADR-014 §2.2)
LOW_COST_FLOOR_JPY = 30000.0   # cost が 30000 未満なら cvr_low / cpa_high を 0 に


# ========== Public API ==========

def compute_fraud_score(
    client_id: str,
    media: str,
    campaigns: list[dict],
    baselines: Optional[dict] = None,
    fraud_threshold: float = FRAUD_THRESHOLD_DEFAULT,
    cv_rate_threshold_pct: float = CV_RATE_THRESHOLD_DEFAULT_PCT,
) -> dict:
    """campaign 単位で fraud_score を算出 + 4 象限分類

    Args:
        client_id: クライアント ID
        media: meta | google | tiktok
        campaigns: meta_adapter / google_adapter 等の出力 (cost / ctr / frequency / cpa /
                   conversions / clicks / impressions を含むこと)
        baselines: {"cvr_pct": 2.0, "cpa_jpy": 6000} 等。None なら INDUSTRY_BASELINES["default"]
        fraud_threshold: ADR-014 §2.4 既定 0.60
        cv_rate_threshold_pct: ADR-014 §2.4 既定 5.0

    Returns:
        ADR-014 §2.5 のスキーマ
    """
    base = baselines or INDUSTRY_BASELINES["default"]
    samples = [_score_campaign(c, base) for c in campaigns]

    # 4 象限分類
    counts = {"black": 0, "gray": 0, "white": 0, "unknown": 0}
    for s in samples:
        s["quadrant"] = classify_quadrant(
            s["fraud_score"], s["cv_rate_pct"],
            fraud_threshold=fraud_threshold,
            cv_rate_threshold_pct=cv_rate_threshold_pct,
        )
        counts[s["quadrant"]] += 1

    return {
        "client_id": client_id,
        "media": media,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "fraud_threshold": fraud_threshold,
        "cv_rate_threshold": cv_rate_threshold_pct,
        "baselines": base,
        "samples": samples,
        "summary": {
            "total_samples": len(samples),
            "black_count":   counts["black"],
            "gray_count":    counts["gray"],
            "white_count":   counts["white"],
            "unknown_count": counts["unknown"],
        },
    }


def classify_quadrant(
    fraud_score: float,
    cv_rate_pct: float,
    fraud_threshold: float = FRAUD_THRESHOLD_DEFAULT,
    cv_rate_threshold_pct: float = CV_RATE_THRESHOLD_DEFAULT_PCT,
) -> str:
    """ADR-014 §2.4 4 象限分類"""
    high_fraud = fraud_score >= fraud_threshold
    high_cv = cv_rate_pct >= cv_rate_threshold_pct
    if high_fraud and not high_cv:
        return "black"
    if high_fraud and high_cv:
        return "gray"
    if not high_fraud and high_cv:
        return "white"
    return "unknown"


def get_industry_baseline(industry_key: str) -> dict:
    """clients.yaml の company.industry から baseline 取得"""
    return INDUSTRY_BASELINES.get(industry_key, INDUSTRY_BASELINES["default"])


# ========== Private: 個別スコア算出 (ADR-014 §2.2) ==========

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _freq_anom_score(frequency: float) -> float:
    """frequency=3 で 0、freq=8 で 1.0"""
    return _clamp((frequency - 3.0) / 5.0)


def _ctr_low_score(ctr_pct: float) -> float:
    """ctr 0.5% 以上で 0、ctr=0% で 1.0"""
    return _clamp((0.5 - ctr_pct) / 0.5)


def _ctr_high_score(ctr_pct: float) -> float:
    """ctr 5% 未満で 0、ctr=10% で 1.0 (click-farm 疑義)"""
    return _clamp((ctr_pct - 5.0) / 5.0)


def _cvr_low_score(cvr_pct: float, baseline_cvr_pct: float, cost_jpy: float) -> float:
    """baseline 比 100% で 0、低消費 (cost < 30000) は 0 に抑制"""
    if cost_jpy < LOW_COST_FLOOR_JPY or baseline_cvr_pct <= 0:
        return 0.0
    ratio = cvr_pct / baseline_cvr_pct
    return _clamp(1.0 - ratio)


def _cpa_high_score(cpa_jpy: float, baseline_cpa_jpy: float, cost_jpy: float) -> float:
    """1.5x baseline で 0、3.5x baseline で 1.0。cpa=0 (cv 0) は除外、低消費は 0"""
    if cpa_jpy <= 0 or baseline_cpa_jpy <= 0 or cost_jpy < LOW_COST_FLOOR_JPY:
        return 0.0
    if cpa_jpy < baseline_cpa_jpy * 1.5:
        return 0.0
    return _clamp((cpa_jpy - baseline_cpa_jpy * 1.5) / (baseline_cpa_jpy * 2))


def _mfa_share_score() -> float:
    """Phase A は固定 0、Phase B で Meta Placement audit log を反映"""
    return 0.0


def _score_campaign(campaign: dict, baselines: dict) -> dict:
    """1 件の campaign に対して各個別スコア + 加重合成 fraud_score を算出"""
    cost = float(campaign.get("cost") or 0)
    clicks = float(campaign.get("clicks") or 0)
    conversions = float(campaign.get("conversions") or 0)
    ctr_pct = float(campaign.get("ctr") or 0)            # meta_adapter は % で返す
    frequency = float(campaign.get("frequency") or 0)
    cpa_jpy = float(campaign.get("cpa") or 0)

    cvr_pct = (conversions / clicks * 100) if clicks > 0 else 0.0

    components = {
        "freq_anom": _freq_anom_score(frequency),
        "ctr_low":   _ctr_low_score(ctr_pct),
        "ctr_high":  _ctr_high_score(ctr_pct),
        "cvr_low":   _cvr_low_score(cvr_pct, baselines.get("cvr_pct", 2.0), cost),
        "cpa_high":  _cpa_high_score(cpa_jpy, baselines.get("cpa_jpy", 6000), cost),
        "mfa_share": _mfa_share_score(),
    }
    fraud_score = round(sum(WEIGHTS[k] * v for k, v in components.items()), 4)

    return {
        "campaign":      campaign.get("campaign", "unknown"),
        "campaign_id":   str(campaign.get("campaign_id") or ""),
        "fraud_score":   fraud_score,
        "cv_rate_pct":   round(cvr_pct, 3),
        "ad_cost":       cost,
        "cv_count":      conversions,
        "ctr_pct":       ctr_pct,
        "frequency":     frequency,
        "cpa_jpy":       cpa_jpy,
        "components":    {k: round(v, 4) for k, v in components.items()},
    }
