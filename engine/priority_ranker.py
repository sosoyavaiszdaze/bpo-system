"""v3 Priority Ranker — 優先アクション Top5 と Critical Alerts を算出する。

設計: docs/report_design/v3_priority_score_weights.md
重み設定: config/priority_weights.yaml

使い方:
    from engine.priority_ranker import (
        load_weights,
        load_all_rules,
        compute_top_actions,
        compute_critical_alerts,
    )

    weights = load_weights()
    rules = load_all_rules()
    top5 = compute_top_actions(detected_rule_ids, rules, weights, monthly_spend_yen=750000)
    alerts = compute_critical_alerts(detected_rule_ids, rules, weights)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("bpo")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
WEIGHTS_PATH = CONFIG_DIR / "priority_weights.yaml"
RULES_DIR = CONFIG_DIR / "rules"


def load_weights(path: Path | None = None) -> dict[str, Any]:
    """priority_weights.yaml を読み込む。"""
    target = path or WEIGHTS_PATH
    with open(target, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_rules(rules_dir: Path | None = None) -> dict[str, dict]:
    """rules/*.yaml を全て読み込み、id をキーにした辞書を返す。"""
    target_dir = rules_dir or RULES_DIR
    rules: dict[str, dict] = {}
    for rule_file in sorted(target_dir.glob("*.yaml")):
        with open(rule_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for r in data.get("rules", []) or []:
            rules[r["id"]] = r
    return rules


def estimate_impact_yen(rule: dict, weights: dict, monthly_spend_yen: float) -> float:
    """expected_impact から月次インパクト（円）を推定する。未設定時は severity フォールバック。

    主要メトリクスごとの月次換算ロジック:
        cpa_change_pct          → |%値| × monthly_spend × 1.0   （直接削減額）
        spend_efficiency_pct    → %値 × monthly_spend × 1.0     （無駄削減）
        roas_change_pct         → %値 × monthly_spend × 1.0     （売上増換算）
        cv_count_change_pct     → %値 × monthly_spend × 0.7     （CV増の経済価値）
        ctr/cvr/IS recovery     → %値 × monthly_spend × 0.5     （二次効果）
        learning_signal_quality → %値 × monthly_spend × 0.3     （間接効果）
    """
    severity = rule.get("severity", "medium")
    fallback = weights.get("fallback_impact_yen", {}).get(severity, 8000)

    ei = rule.get("expected_impact")
    if not ei:
        return float(fallback)

    metric = ei.get("primary_metric", "")
    raw_value = ei.get("primary_value")
    if not isinstance(raw_value, (int, float)):
        return float(fallback)

    pct = abs(raw_value) / 100.0
    multipliers = {
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
    mult = multipliers.get(metric, 0.4)  # 未知メトリクスは控えめに
    return pct * monthly_spend_yen * mult


def estimate_effort_hours(rule: dict, weights: dict) -> float:
    """工数（時間）を推定。quick_win=true は固定値、それ以外は category 別に推定。"""
    if rule.get("quick_win", False):
        return float(weights.get("quick_win_effort_hours", 0.5))
    cat = rule.get("category", "")
    by_cat = weights.get("default_effort_hours_by_category", {})
    return float(by_cat.get(cat, weights.get("default_effort_hours_fallback", 4.0)))


def compute_priority_score(
    rule: dict,
    weights: dict,
    monthly_spend_yen: float,
) -> dict[str, Any]:
    """1ルール分の priority_score を計算する。"""
    severity = rule.get("severity", "medium")
    severity_w = weights["severity_weights"].get(severity, 1.0)

    ei = rule.get("expected_impact") or {}
    confidence = ei.get("confidence", "medium")
    confidence_w = weights["confidence_weights"].get(confidence, 0.7)

    impact_yen = estimate_impact_yen(rule, weights, monthly_spend_yen)
    effort_h = estimate_effort_hours(rule, weights)

    # 工数正規化
    normalization = weights.get("effort_normalization", "linear")
    if normalization == "square_root":
        effort_div = math.sqrt(max(effort_h, 0.5))
    else:
        effort_div = max(effort_h, 0.5)

    raw_score = (severity_w * impact_yen * confidence_w) / effort_div

    # quick_win ボーナス
    quick_win = bool(rule.get("quick_win", False))
    if quick_win:
        raw_score *= float(weights.get("quick_win_bonus", 1.5))

    return {
        "rule_id": rule["id"],
        "rule_name": rule.get("name", ""),
        "severity": severity,
        "category": rule.get("category", ""),
        "platform": rule.get("platform", ""),
        "confidence": confidence,
        "estimated_impact_yen": int(round(impact_yen)),
        "estimated_effort_hours": effort_h,
        "quick_win": quick_win,
        "has_expected_impact": bool(rule.get("expected_impact")),
        "principle_tag": rule.get("yonemitsu_alignment", ""),
        "redesign_note": (rule.get("redesign_note") or "")[:200],
        "priority_score": round(raw_score, 2),
    }


def compute_top_actions(
    detected_rule_ids: list[str],
    rules: dict[str, dict],
    weights: dict,
    monthly_spend_yen: float | None = None,
    max_n: int = 5,
) -> list[dict]:
    """検出されたルール ID 群から Top N を priority_score で並び替え。"""
    if monthly_spend_yen is None:
        monthly_spend_yen = float(weights.get("default_monthly_spend_yen", 750000))

    scored: list[dict] = []
    seen: set[str] = set()
    for rid in detected_rule_ids:
        if rid in seen:
            continue
        seen.add(rid)
        rule = rules.get(rid)
        if not rule:
            log.warning(f"priority_ranker: rule_id={rid} が rules/ に未定義のためスキップ")
            continue
        scored.append(compute_priority_score(rule, weights, monthly_spend_yen))

    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    return scored[:max_n]


def compute_critical_alerts(
    detected_rule_ids: list[str],
    rules: dict[str, dict],
    weights: dict,
    max_n: int | None = None,
) -> list[dict]:
    """⚠️ 今すぐ対処すべき重大問題セクション用。severity=critical を最大 N 件固定表示。

    Top5 とは独立した枠なので、Top5 と重複してもよい設計。
    """
    cfg = weights.get("critical_alerts", {})
    if max_n is None:
        max_n = int(cfg.get("max_count", 3))
    threshold = cfg.get("severity_threshold", "critical")
    exclude_qw = bool(cfg.get("exclude_quick_wins", False))

    alerts: list[dict] = []
    seen: set[str] = set()
    for rid in detected_rule_ids:
        if rid in seen:
            continue
        seen.add(rid)
        rule = rules.get(rid)
        if not rule:
            continue
        if rule.get("severity") != threshold:
            continue
        if exclude_qw and rule.get("quick_win", False):
            continue
        alerts.append(
            {
                "rule_id": rule["id"],
                "rule_name": rule.get("name", ""),
                "severity": rule.get("severity"),
                "category": rule.get("category", ""),
                "platform": rule.get("platform", ""),
                "redesign_note": (rule.get("redesign_note") or "")[:200],
                "quick_win": bool(rule.get("quick_win", False)),
            }
        )
    return alerts[:max_n]
