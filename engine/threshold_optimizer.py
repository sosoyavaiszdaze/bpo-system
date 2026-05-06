"""閾値最適化エンジン (ADR-006 + ADR-009 連携)

責務: fraud_score × CV 発生率の相関分析を行い、
      「黒 (不正高 × CV 低)」「灰 (不正高 × CV 高)」「白 (不正低 × CV 高)」「無 (両方低)」
      の 4 象限分類で最適 fraud_score 閾値を媒体別 / キャンペーン別に提案する。

主要関数:
    - analyze_fraud_cv_correlation(client_id, media, period_days=30)
    - classify_segments(correlation_data) -> {black, grey, white, unknown}
    - optimize_threshold(client_id, media, campaign_id=None) -> float
    - suggest_threshold_update(client_id) -> list[dict]

データソース (Phase B Week 2-3 で接続):
    - adapters/{meta,google,tiktok}_adapter.py の最新データ
    - outputs/client_state/{client}.yaml の履歴
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("bpo")


# ========== Public API ==========

def analyze_fraud_cv_correlation(
    client_id: str, media: str, period_days: int = 30,
    sample_data: Optional[list[dict]] = None,
) -> dict:
    """過去 N 日の fraud_score 分布と CV 発生率を突合

    Args:
        client_id: クライアント ID
        media: meta | google | tiktok
        period_days: 分析期間 (デフォルト 30 日)
        sample_data: テスト用注入データ (本番では adapters 経由で取得)

    Returns:
        {
            "client_id": ...,
            "media": ...,
            "period_days": 30,
            "samples": [{"fraud_score": 0.85, "cv_count": 2, "ad_cost": 5000}, ...],
            "summary": {
                "total_samples": int,
                "fraud_score_mean": float,
                "cv_rate_mean": float,
                "correlation_coefficient": float,
            },
        }
    """
    samples = sample_data if sample_data is not None else _fetch_samples(client_id, media, period_days)

    if not samples:
        return {
            "client_id": client_id,
            "media": media,
            "period_days": period_days,
            "samples": [],
            "summary": {
                "total_samples": 0,
                "fraud_score_mean": 0.0,
                "cv_rate_mean": 0.0,
                "correlation_coefficient": 0.0,
            },
        }

    fraud_scores = [s.get("fraud_score", 0) for s in samples]
    cv_rates = [s.get("cv_rate", 0) for s in samples]

    return {
        "client_id": client_id,
        "media": media,
        "period_days": period_days,
        "samples": samples,
        "summary": {
            "total_samples": len(samples),
            "fraud_score_mean": _mean(fraud_scores),
            "cv_rate_mean": _mean(cv_rates),
            "correlation_coefficient": _correlation(fraud_scores, cv_rates),
        },
    }


def classify_segments(correlation_data: dict, fraud_threshold: float = 0.6,
                      cv_threshold_pct: float = 5.0) -> dict:
    """4 象限分類

    Args:
        correlation_data: analyze_fraud_cv_correlation の出力
        fraud_threshold: fraud_score の高低判定閾値 (default 0.6)
        cv_threshold_pct: CV 発生率の高低判定閾値 (default 5%)

    Returns:
        {
            "black":   [...],  # fraud 高 × CV 低 → 即ブロック
            "grey":    [...],  # fraud 高 × CV 高 → 灰ゾーン (ADR-009 都度判断)
            "white":   [...],  # fraud 低 × CV 高 → 優良トラフィック (保護)
            "unknown": [...],  # fraud 低 × CV 低 → 判断保留
        }
    """
    samples = correlation_data.get("samples", [])
    result = {"black": [], "grey": [], "white": [], "unknown": []}

    for s in samples:
        fs = s.get("fraud_score", 0)
        cvr = s.get("cv_rate", 0)
        if fs >= fraud_threshold and cvr < cv_threshold_pct:
            result["black"].append(s)
        elif fs >= fraud_threshold and cvr >= cv_threshold_pct:
            result["grey"].append(s)
        elif fs < fraud_threshold and cvr >= cv_threshold_pct:
            result["white"].append(s)
        else:
            result["unknown"].append(s)
    return result


def optimize_threshold(
    client_id: str, media: str, campaign_id: Optional[str] = None,
    correlation_data: Optional[dict] = None,
) -> float:
    """黒セグメントだけを切る最適 fraud_score 閾値を算出

    アルゴリズム:
        - 黒 (fraud 高 × CV 低) と灰 (fraud 高 × CV 高) の境界線探索
        - 黒のサンプルの fraud_score 最小値、灰のサンプルの fraud_score 最大値を考慮
        - 安全側 (CV 保全寄り) に振った値を返す

    Returns:
        最適 fraud_score 閾値 (0.0-1.0)
    """
    if correlation_data is None:
        correlation_data = analyze_fraud_cv_correlation(client_id, media)

    segmented = classify_segments(correlation_data)
    black_scores = [s["fraud_score"] for s in segmented["black"]]
    grey_scores = [s["fraud_score"] for s in segmented["grey"]]

    if not black_scores:
        # 黒サンプルなし → デフォルト保守値
        return 0.85

    if not grey_scores:
        # 灰サンプルなし → 黒の最小値 - マージン
        return max(0.5, min(black_scores) - 0.05)

    # 黒の最小値と灰の最大値の中間 (CV 保全側に若干寄せ)
    black_min = min(black_scores)
    grey_max = max(grey_scores)
    midpoint = (black_min + grey_max) / 2
    # CV 保全のため midpoint よりやや上 (= より厳しい閾値) に
    return min(0.95, max(0.5, midpoint + 0.02))


def suggest_threshold_update(client_id: str, current_thresholds: Optional[dict] = None) -> list[dict]:
    """媒体別 / キャンペーン別の推奨閾値変更を提案

    Args:
        client_id: クライアント ID
        current_thresholds: 現状の閾値 (例: {"meta": 0.7, "google": 0.75})

    Returns:
        [
            {
                "media": "meta",
                "current_threshold": 0.70,
                "suggested_threshold": 0.78,
                "expected_impact": "黒セグメント排除精度 +12%、CV 保全 99.5%",
                "confidence": 0.7,
            },
            ...
        ]
    """
    suggestions = []
    current_thresholds = current_thresholds or {}
    for media in ["meta", "google", "tiktok"]:
        current = current_thresholds.get(media, 0.7)
        try:
            optimal = optimize_threshold(client_id, media)
        except Exception as e:
            log.warning(f"threshold_optimizer: {media} failed: {e}")
            continue
        if abs(optimal - current) > 0.05:
            suggestions.append({
                "media": media,
                "current_threshold": current,
                "suggested_threshold": round(optimal, 2),
                "delta": round(optimal - current, 2),
                "expected_impact": _build_impact_text(current, optimal),
                "confidence": 0.7,
            })
    return suggestions


# ========== Private Helpers ==========

SAMPLE_MIN_FOR_OPTIMIZE = 5  # サンプル不足判定 (campaign が 5 件未満なら学習保留)


def _fetch_samples(client_id: str, media: str, period_days: int) -> list[dict]:
    """campaign 粒度で adapter から実データを取得し、fraud_score を算出して返す (ADR-014)

    Phase A:
      - meta のみ対応
      - サンプル数 < SAMPLE_MIN_FOR_OPTIMIZE なら「学習保留」ログを残し空配列で返す
        (= optimize_threshold は black サンプル無しと判定 → デフォルト保守値 0.85 を返す)

    返却スキーマ:
      classify_segments / optimize_threshold が参照する
      {"fraud_score": float, "cv_rate": float} 互換 + 詳細フィールド (campaign 等)
    """
    if media != "meta":
        log.info(f"[threshold_optimizer] {client_id}/{media}: only meta supported in Phase A")
        return []

    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        clients_yaml = _Path(__file__).resolve().parent.parent / "config" / "clients.yaml"
        cfg = _yaml.safe_load(clients_yaml.read_text(encoding="utf-8")) or {}
        client_cfg = cfg.get("clients", {}).get(client_id, {})
        meta_cfg = (client_cfg.get("ads") or {}).get("meta") or client_cfg.get("meta") or {}
        if not meta_cfg.get("account_id"):
            log.warning(f"[threshold_optimizer] {client_id}: meta config missing")
            return []

        from adapters.meta_adapter import fetch_meta_ads
        from analyzers.fraud_score import compute_fraud_score, get_industry_baseline

        # campaign 粒度: meta_adapter の lookback_days をそのまま尊重 (clients.yaml で 30d 等)
        meta_data = fetch_meta_ads(meta_cfg)
        campaigns = (meta_data or {}).get("campaigns", []) or []

        # 学習保留: サンプル数 < 閾値 のとき compute_fraud_score を呼ばずに即 return。
        # ad_platform_data の不足下で fraud_score を出すと信頼性のないシグナルが
        # threshold_optimizer / adtruth_runner に流れ、デフォルト閾値で十分な状態を
        # 「灰ゾーン候補」と誤分類するリスクがある。フェイルセーフで処理停止。
        if len(campaigns) < SAMPLE_MIN_FOR_OPTIMIZE:
            log.info(
                f"[threshold_optimizer] {client_id}/{media}: campaign sample {len(campaigns)} "
                f"< {SAMPLE_MIN_FOR_OPTIMIZE}, 学習保留 (デフォルト閾値継続、fraud_score 算出スキップ)"
            )
            return []

        industry = (client_cfg.get("company") or {}).get("industry", "default")
        base = get_industry_baseline(industry)

        scored = compute_fraud_score(client_id, "meta", campaigns, baselines=base)
        samples = scored["samples"]

        # classify_segments 互換 (cv_rate キーを追加)
        for s in samples:
            s["cv_rate"] = s.get("cv_rate_pct", 0)
        return samples

    except Exception as e:
        log.warning(f"[threshold_optimizer] sample fetch failed: {e}")
        return []


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _correlation(xs: list[float], ys: list[float]) -> float:
    """単純な Pearson 相関係数"""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs)
    deny = sum((y - my) ** 2 for y in ys)
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx ** 0.5 * deny ** 0.5)


def _build_impact_text(current: float, optimal: float) -> str:
    delta = optimal - current
    if delta > 0:
        return f"閾値を {current:.2f} → {optimal:.2f} に厳格化、CV 保全率向上 + 不正排除精度改善"
    return f"閾値を {current:.2f} → {optimal:.2f} に緩和、CV 取りこぼし軽減"
