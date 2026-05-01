"""v3 想定効果（インパクト）算出モジュール — 層2: 計算ロジック。

設計: docs/report_design/v3_content_strategy.md
入力ソース:
    - rules/*.yaml の expected_impact フィールド
    - 検出データ（現状値）
    - benchmarks.yaml の業界推奨値

主要な責務:
    1. ルールに紐づく月次削減額/改善%の試算
    2. 全アクションを集計した「Top5 全件実行時の改善見込み」算出
    3. expected_impact 未設定ルールは「効果未試算」として返す
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("bpo")

# benchmark_compare と同じスケール係数
METRIC_TO_YEN_MULTIPLIER = {
    "cpa_change_pct": 1.0,
    "spend_efficiency_pct": 1.0,
    "roas_change_pct": 1.0,
    "cv_count_change_pct": 0.7,
    "ctr_change_pct": 0.5,
    "cvr_change_pct": 0.5,
    "impression_share_recovery_pct": 0.5,
    "frequency_reduction_pct": 0.4,
    "ad_rank_improvement_pct": 0.5,
    "learning_signal_quality_pct": 0.3,
    "match_rate_improvement_pct": 0.4,
}

METRIC_LABELS = {
    "cpa_change_pct": "CPA",
    "spend_efficiency_pct": "広告費効率",
    "roas_change_pct": "ROAS",
    "cv_count_change_pct": "CV数",
    "ctr_change_pct": "CTR",
    "cvr_change_pct": "CVR",
    "impression_share_recovery_pct": "インプレッションシェア",
    "frequency_reduction_pct": "フリークエンシー",
    "ad_rank_improvement_pct": "広告ランク",
    "learning_signal_quality_pct": "学習シグナル品質",
    "match_rate_improvement_pct": "計測マッチ率",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}


def estimate_for_rule(
    rule: dict,
    monthly_spend_yen: float = 750000.0,
    current_metrics: dict | None = None,
) -> dict[str, Any]:
    """1ルール分の想定効果を算出する。

    Args:
        rule: ルール定義（rules/*.yaml の1エントリ）
        monthly_spend_yen: 月次広告費（クライアント実データ）
        current_metrics: 現状値辞書。以下のキーをサポート（任意）:
            { "cpa": 6048, "roas": 2.7, "cv_count": 124,
              "ctr": 1.3, "cvr": 2.5, "cost": 750000,
              "campaign_cost": 72000, "campaign_cpa": 4500 }
            キャンペーン単位の試算が可能な場合は campaign_* を渡す。

    Returns:
        dict {
          rule_id, has_estimate,
          primary_metric, primary_value, primary_label,
          confidence, confidence_label,
          impact_horizon_weeks,
          estimated_savings_yen,
          estimated_savings_display,
          rationale,
          fallback_text,
          calc_basis: "monthly_spend" | "campaign_specific" | "current_metric_based",
        }
    """
    rid = rule.get("id", "")
    ei = rule.get("expected_impact")

    if not ei:
        return {
            "rule_id": rid,
            "has_estimate": False,
            "primary_metric": None,
            "primary_value": None,
            "primary_label": None,
            "confidence": None,
            "confidence_label": None,
            "impact_horizon_weeks": None,
            "estimated_savings_yen": None,
            "estimated_savings_display": "効果未試算",
            "rationale": rule.get("redesign_note", "") or "",
            "fallback_text": "本ルールは v3 で効果試算未対応のため、定性的な影響として記載しています。",
            "calc_basis": "none",
        }

    metric = ei.get("primary_metric", "")
    raw_value = ei.get("primary_value")
    confidence = ei.get("confidence", "medium")
    horizon = ei.get("impact_horizon_weeks", 4)
    rationale = ei.get("rationale", "")

    # === 数値化（current_metrics に応じて精緻化） ===
    if not isinstance(raw_value, (int, float)):
        savings_yen = 0
        calc_basis = "none"
    else:
        savings_yen, calc_basis = _compute_savings_yen(
            metric, float(raw_value), monthly_spend_yen, current_metrics
        )

    # 表示文（raw_value が数値でない場合はラベルのみで表示）
    primary_label = METRIC_LABELS.get(metric, metric)
    if not isinstance(raw_value, (int, float)):
        primary_display = primary_label
    elif metric == "cpa_change_pct":
        sign = "削減" if raw_value < 0 else "増加"
        primary_display = f"{primary_label} 約 {abs(raw_value)}% {sign}"
    elif metric == "roas_change_pct":
        primary_display = f"{primary_label} 約 +{abs(raw_value)}%"
    elif metric == "spend_efficiency_pct":
        primary_display = f"{primary_label} 約 {abs(raw_value)}% の無駄削減"
    else:
        primary_display = f"{primary_label} 約 +{abs(raw_value)}%"

    if savings_yen > 0:
        basis_note = ""
        if calc_basis == "campaign_specific":
            basis_note = "（対象キャンペーン基準）"
        elif calc_basis == "current_metric_based":
            basis_note = "（現状値ベース試算）"
        savings_display = f"月額 約 ¥{savings_yen:,} 改善見込み{basis_note}（{primary_display}）"
    else:
        savings_display = primary_display

    return {
        "rule_id": rid,
        "has_estimate": True,
        "primary_metric": metric,
        "primary_value": raw_value,
        "primary_label": primary_label,
        "primary_display": primary_display,
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS.get(confidence, confidence),
        "impact_horizon_weeks": horizon,
        "estimated_savings_yen": savings_yen,
        "estimated_savings_display": savings_display,
        "rationale": rationale,
        "fallback_text": None,
        "calc_basis": calc_basis,
    }


def _compute_savings_yen(
    metric: str,
    raw_value: float,
    monthly_spend_yen: float,
    current_metrics: dict | None,
) -> tuple[int, str]:
    """current_metrics に応じてキャンペーン別または現状値ベース試算を行う。

    Returns:
        (savings_yen, calc_basis)
        calc_basis: "campaign_specific" | "current_metric_based" | "monthly_spend"
    """
    pct = abs(raw_value) / 100.0
    cm = current_metrics or {}

    # === 1. campaign_cost が渡されればキャンペーン単位で精緻化 ===
    campaign_cost = cm.get("campaign_cost")
    if isinstance(campaign_cost, (int, float)) and campaign_cost > 0:
        # CPA 改善 / Spend 効率は campaign_cost を起点に試算
        if metric in ("cpa_change_pct", "spend_efficiency_pct"):
            return int(round(pct * float(campaign_cost))), "campaign_specific"
        # CV 数増は CPA で割って金額換算
        if metric == "cv_count_change_pct":
            campaign_cpa = cm.get("campaign_cpa") or cm.get("cpa") or 0
            campaign_cv = cm.get("campaign_cv") or 0
            if campaign_cpa > 0 and campaign_cv > 0:
                extra_cv = pct * campaign_cv
                return int(round(extra_cv * campaign_cpa * 0.7)), "campaign_specific"
        # ROAS 改善
        if metric == "roas_change_pct":
            return int(round(pct * float(campaign_cost))), "campaign_specific"

    # === 2. CPA / ROAS / CV が渡されれば現状値ベースで精緻化 ===
    cpa = cm.get("cpa") or cm.get("avg_cpa")
    cv = cm.get("cv_count") or cm.get("conversions")
    roas = cm.get("roas") or cm.get("avg_roas")

    if metric == "cpa_change_pct" and isinstance(cpa, (int, float)) and isinstance(cv, (int, float)) and cpa > 0 and cv > 0:
        # 月次削減 = 現状CPA × CV × 改善率
        return int(round(pct * cpa * cv)), "current_metric_based"

    if metric == "cv_count_change_pct" and isinstance(cpa, (int, float)) and isinstance(cv, (int, float)) and cpa > 0 and cv > 0:
        # CV増分の経済価値 = 増CV数 × CPA × 0.7
        extra_cv = pct * cv
        return int(round(extra_cv * cpa * 0.7)), "current_metric_based"

    if metric == "roas_change_pct" and isinstance(roas, (int, float)) and roas > 0:
        # 売上増 = monthly_spend × roas × 改善率
        return int(round(pct * monthly_spend_yen * roas)), "current_metric_based"

    # === 3. 全体予算ベースのフォールバック（従来ロジック） ===
    mult = METRIC_TO_YEN_MULTIPLIER.get(metric, 0.4)
    return int(round(pct * monthly_spend_yen * mult)), "monthly_spend"


def estimate_for_rules(
    rules: list[dict],
    monthly_spend_yen: float = 750000.0,
    current_metrics: dict | None = None,
) -> list[dict]:
    """複数ルールに対する一括試算。"""
    return [estimate_for_rule(r, monthly_spend_yen, current_metrics) for r in rules]


def aggregate_top5_impact(estimates: list[dict]) -> dict[str, Any]:
    """Top5（または任意件数）のインパクトを集計し、エグゼクティブサマリ用に整形する。

    Returns:
        dict {
          total_savings_yen,
          total_savings_display: "月額 ¥185,000",
          confidence_mix: { high: 2, medium: 2, low: 1 },
          horizon_weeks_max: 6,
          confidence_summary: "高 2件 / 中 2件 / 低 1件",
          rules_with_estimate: 4,
          rules_without_estimate: 1,
        }
    """
    total = 0
    conf_mix = {"high": 0, "medium": 0, "low": 0}
    horizon_max = 0
    with_est = 0
    without_est = 0

    for e in estimates:
        if e.get("has_estimate"):
            with_est += 1
            total += int(e.get("estimated_savings_yen") or 0)
            c = e.get("confidence")
            if c in conf_mix:
                conf_mix[c] += 1
            h = e.get("impact_horizon_weeks") or 0
            if h > horizon_max:
                horizon_max = h
        else:
            without_est += 1

    parts = []
    if conf_mix["high"]:
        parts.append(f"高 {conf_mix['high']}件")
    if conf_mix["medium"]:
        parts.append(f"中 {conf_mix['medium']}件")
    if conf_mix["low"]:
        parts.append(f"低 {conf_mix['low']}件")
    confidence_summary = " / ".join(parts) if parts else "試算対象なし"

    return {
        "total_savings_yen": total,
        "total_savings_display": f"月額 ¥{total:,}" if total > 0 else "試算未実施",
        "confidence_mix": conf_mix,
        "horizon_weeks_max": horizon_max,
        "confidence_summary": confidence_summary,
        "rules_with_estimate": with_est,
        "rules_without_estimate": without_est,
    }


def build_kpi_projection(
    audit: dict,
    aggregate: dict,
) -> dict[str, Any]:
    """エグゼクティブサマリの「推定改善インパクト」KPI 表を構築する。

    現状値（audit）→ 改善後（試算）の差分を整形。
    """
    current_cost = float(audit.get("total_cost", 0) or 0)
    current_cv = float(audit.get("total_conversions", 0) or 0)
    current_cpa = float(audit.get("avg_cpa", 0) or 0)

    savings = aggregate.get("total_savings_yen", 0)
    # ざっくり試算: 削減額の半分は cost 削減、半分は CV 増（実装は単純化）
    cost_delta = -int(savings * 0.4)
    cv_delta = 0
    if current_cpa > 0:
        # cost 削減はCV維持と仮定、別途増加分はCV増換算
        extra_cv = int(savings * 0.6 / current_cpa)
        cv_delta = extra_cv

    new_cost = max(0, int(current_cost + cost_delta))
    new_cv = max(0, current_cv + cv_delta)
    new_cpa = (new_cost / new_cv) if new_cv > 0 else current_cpa
    cpa_delta = int(new_cpa - current_cpa)

    return {
        "current": {
            "monthly_cost": int(current_cost),
            "monthly_cv": int(current_cv),
            "avg_cpa": int(current_cpa),
        },
        "projected": {
            "monthly_cost": new_cost,
            "monthly_cv": int(new_cv),
            "avg_cpa": int(new_cpa),
        },
        "delta": {
            "monthly_cost": cost_delta,
            "monthly_cv": cv_delta,
            "avg_cpa": cpa_delta,
        },
        "confidence_note": aggregate.get("confidence_summary", ""),
        "horizon_max_weeks": aggregate.get("horizon_weeks_max", 4),
    }
