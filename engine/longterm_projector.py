"""v3 long-term effect projection.

Top5 actions already carry a steady-state monthly impact from impact_estimator.
This module turns that into a 12-month view using lightweight lifecycle curves.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("bpo")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LIFECYCLE_PATH = CONFIG_DIR / "rule_lifecycle.yaml"

SCENARIOS = ("lower", "realistic", "upper")
MILESTONE_MONTHS = (1, 3, 6, 12)


def load_rule_lifecycle(path: Path | None = None) -> dict[str, Any]:
    """Load lifecycle configuration."""
    target = path or LIFECYCLE_PATH
    with open(target, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_longterm_projection(
    actions: list[dict],
    audit: dict | None = None,
    lifecycle_cfg: dict | None = None,
) -> dict[str, Any]:
    """Build a 12-month projection from v3 Top actions.

    Args:
        actions: enriched actions from report_generator_v3. Each action may contain
            impact.estimated_savings_yen and impact.confidence.
        audit: ads audit result. Used only for context values.
        lifecycle_cfg: optional config override for tests.
    """
    cfg = lifecycle_cfg or load_rule_lifecycle()
    proj_cfg = cfg.get("projection") or {}
    months_count = int(proj_cfg.get("months", 12))
    weeks_per_month = float(proj_cfg.get("weeks_per_month", 4))
    audit = audit or {}

    projected_actions = _prepare_actions(actions, cfg)
    if not projected_actions:
        return _empty_projection(months_count)

    monthly_rows: list[dict] = []
    cumulative = {scenario: 0 for scenario in SCENARIOS}
    action_cumulative = {action["rule_id"]: 0 for action in projected_actions}

    for month in range(1, months_count + 1):
        week = month * weeks_per_month
        monthly = {scenario: 0 for scenario in SCENARIOS}
        action_rows = []

        for action in projected_actions:
            factors = _scenario_factors(action, week, cfg)
            contribution = {
                scenario: int(round(action["monthly_steady_state_yen"] * factors[scenario]))
                for scenario in SCENARIOS
            }
            for scenario in SCENARIOS:
                monthly[scenario] += contribution[scenario]
            action_cumulative[action["rule_id"]] += contribution["realistic"]
            action_rows.append({
                "rule_id": action["rule_id"],
                "factor": round(factors["realistic"], 3),
                "realistic_yen": contribution["realistic"],
            })

        for scenario in SCENARIOS:
            cumulative[scenario] += monthly[scenario]

        monthly_rows.append({
            "month": month,
            "month_label": f"Month {month}",
            "monthly": dict(monthly),
            "cumulative": dict(cumulative),
            "monthly_display": {k: _format_yen(v) for k, v in monthly.items()},
            "cumulative_display": {k: _format_yen(v) for k, v in cumulative.items()},
            "actions": action_rows,
        })

    milestones = [
        {
            "month": row["month"],
            "monthly_realistic_yen": row["monthly"]["realistic"],
            "cumulative_realistic_yen": row["cumulative"]["realistic"],
            "monthly_realistic_display": _format_yen(row["monthly"]["realistic"]),
            "cumulative_realistic_display": _format_yen(row["cumulative"]["realistic"]),
            "range_display": (
                f"{_format_yen(row['cumulative']['lower'])} - "
                f"{_format_yen(row['cumulative']['upper'])}"
            ),
        }
        for row in monthly_rows
        if row["month"] in MILESTONE_MONTHS and row["month"] <= months_count
    ]

    total_12m = monthly_rows[-1]["cumulative"]
    top_contributors = _build_top_contributors(projected_actions, monthly_rows[-1], action_cumulative)
    chart = _build_chart(monthly_rows)
    monthly_spend = float(audit.get("total_cost", 0) or 0)

    return {
        "has_projection": True,
        "months_count": months_count,
        "monthly_spend_yen": int(monthly_spend),
        "monthly_spend_display": _format_yen(monthly_spend) if monthly_spend else "実績値なし",
        "months": monthly_rows,
        "milestones": milestones,
        "top_contributors": top_contributors,
        "chart": chart,
        "total_12m": total_12m,
        "total_12m_display": {k: _format_yen(v) for k, v in total_12m.items()},
        "summary_text": (
            f"Top{len(projected_actions)}施策を実行した場合、12か月累計で"
            f"{_format_yen(total_12m['realistic'])}の改善余地があります。"
        ),
        "assumptions": [
            "現状の月次広告費とCV規模が大きく変わらない前提",
            "Top施策が実行され、設定や計測が維持される前提",
            "信頼区間はルールごとの試算確度をもとにしたMVP推定",
        ],
    }


def _prepare_actions(actions: list[dict], cfg: dict) -> list[dict]:
    prepared = []
    dependency_sources: dict[str, dict] = {}

    for action in actions:
        impact = action.get("impact") or {}
        monthly_yen = impact.get("estimated_savings_yen")
        if not impact.get("has_estimate") or not isinstance(monthly_yen, (int, float)) or monthly_yen <= 0:
            continue

        rule_id = action.get("rule_id") or impact.get("rule_id")
        lifecycle = _resolve_lifecycle(rule_id, action, cfg)
        prepared_action = {
            "rule_id": rule_id,
            "rule_name": action.get("rule_name", ""),
            "severity": action.get("severity", "medium"),
            "platform": action.get("platform", ""),
            "tier": lifecycle["tier"],
            "monthly_steady_state_yen": float(monthly_yen),
            "monthly_steady_state_display": _format_yen(monthly_yen),
            "confidence": impact.get("confidence") or action.get("confidence") or "unknown",
            "confidence_label": impact.get("confidence_label") or action.get("confidence_label") or "—",
            "ramp_up_weeks": lifecycle["ramp_up_weeks"],
            "decay_half_life_weeks": lifecycle["decay_half_life_weeks"],
            "dependency_multiplier": lifecycle["dependency_multiplier"],
            "operational_cost_yen": lifecycle["operational_cost_yen"],
            "unlocks_after_week": lifecycle.get("unlocks_after_week"),
            "unlocks": lifecycle.get("unlocks") or {},
        }
        prepared.append(prepared_action)
        if prepared_action["unlocks"]:
            dependency_sources[rule_id] = prepared_action

    for action in prepared:
        action["dependency_unlocks"] = []
        for source_id, source in dependency_sources.items():
            unlock = source.get("unlocks", {}).get(action["rule_id"])
            if unlock:
                action["dependency_unlocks"].append({
                    "source_rule_id": source_id,
                    "unlock_week": float(source.get("unlocks_after_week") or 0),
                    "dependency_multiplier": float(unlock.get("dependency_multiplier", 1.0)),
                })

    return prepared


def _resolve_lifecycle(rule_id: str, action: dict, cfg: dict) -> dict[str, Any]:
    defaults = cfg.get("defaults") or {}
    rule_cfg = _match_rule_config(rule_id, cfg.get("rules") or {})
    severity = action.get("severity", "medium")
    tier = rule_cfg.get("tier") or (cfg.get("default_tier_by_severity") or {}).get(severity, "monitor")

    return {
        "tier": tier,
        "ramp_up_weeks": int(rule_cfg.get("ramp_up_weeks", defaults.get("ramp_up_weeks", 4))),
        "decay_half_life_weeks": rule_cfg.get("decay_half_life_weeks", defaults.get("decay_half_life_weeks")),
        "dependency_multiplier": float(rule_cfg.get("dependency_multiplier", defaults.get("dependency_multiplier", 1.0))),
        "operational_cost_yen": float(rule_cfg.get("operational_cost_yen", defaults.get("operational_cost_yen", 0))),
        "unlocks_after_week": rule_cfg.get("unlocks_after_week"),
        "unlocks": rule_cfg.get("unlocks") or {},
    }


def _match_rule_config(rule_id: str, rules_cfg: dict) -> dict[str, Any]:
    if rule_id in rules_cfg:
        return rules_cfg[rule_id] or {}
    for key, value in rules_cfg.items():
        if "-" not in key:
            continue
        prefix_start = "".join(ch for ch in key.split("-", 1)[0] if not ch.isdigit())
        start = _rule_number(key.split("-", 1)[0])
        end = _rule_number(key.split("-", 1)[1])
        current = _rule_number(rule_id)
        if rule_id.startswith(prefix_start) and start is not None and end is not None and current is not None:
            if start <= current <= end:
                return value or {}
    return {}


def _rule_number(rule_id: str) -> int | None:
    digits = "".join(ch for ch in rule_id if ch.isdigit())
    return int(digits) if digits else None


def _scenario_factors(action: dict, week: float, cfg: dict) -> dict[str, float]:
    curve = (cfg.get("default_acceptance_curve") or {}).get(action["tier"], {})
    acceptance = _curve_value(curve, week)
    decay = _decay_factor(week, action.get("decay_half_life_weeks"))
    dependency_multiplier = action.get("dependency_multiplier", 1.0)
    for unlock in action.get("dependency_unlocks") or []:
        if week >= unlock.get("unlock_week", 0):
            dependency_multiplier *= unlock.get("dependency_multiplier", 1.0)
    base = acceptance * decay * dependency_multiplier
    op_cost_factor = _operational_cost_factor(action)

    band_pct = (cfg.get("confidence_band_pct") or {}).get(action["confidence"])
    if band_pct is None:
        band_pct = (cfg.get("confidence_band_pct") or {}).get("unknown", 35)
    band = float(band_pct) / 100.0

    return {
        "lower": max(0.0, base * (1.0 - band) - op_cost_factor),
        "realistic": max(0.0, base - op_cost_factor),
        "upper": max(0.0, base * (1.0 + band) - op_cost_factor),
    }


def _operational_cost_factor(action: dict) -> float:
    monthly = action.get("monthly_steady_state_yen") or 0
    op_cost = action.get("operational_cost_yen") or 0
    if monthly <= 0 or op_cost <= 0:
        return 0.0
    return min(0.8, float(op_cost) / float(monthly))


def _curve_value(curve: dict, week: float) -> float:
    if not curve:
        return 1.0
    points = sorted((_parse_week(k), float(v)) for k, v in curve.items())
    if week <= points[0][0]:
        return points[0][1]
    for (w0, v0), (w1, v1) in zip(points, points[1:]):
        if week <= w1:
            if w1 == w0:
                return v1
            ratio = (week - w0) / (w1 - w0)
            return v0 + (v1 - v0) * ratio
    return points[-1][1]


def _parse_week(key: str) -> float:
    return float(str(key).replace("week_", ""))


def _decay_factor(week: float, half_life_weeks) -> float:
    if not half_life_weeks:
        return 1.0
    try:
        half_life = float(half_life_weeks)
    except (TypeError, ValueError):
        return 1.0
    if half_life <= 0:
        return 1.0
    return math.pow(0.5, week / half_life)


def _build_top_contributors(actions: list[dict], final_month: dict, action_cumulative: dict[str, int]) -> list[dict]:
    final_actions = {a["rule_id"]: a for a in final_month.get("actions", [])}
    rows = []
    for action in actions:
        final = final_actions.get(action["rule_id"], {})
        cumulative = int(action_cumulative.get(action["rule_id"], 0))
        rows.append({
            "rule_id": action["rule_id"],
            "rule_name": action["rule_name"],
            "tier": action["tier"],
            "confidence_label": action["confidence_label"],
            "monthly_steady_state_display": action["monthly_steady_state_display"],
            "month12_monthly_display": _format_yen(final.get("realistic_yen", 0)),
            "approx_12m_yen": cumulative,
            "approx_12m_display": _format_yen(cumulative),
        })
    rows.sort(key=lambda x: x["approx_12m_yen"], reverse=True)
    return rows[:5]


def _build_chart(monthly_rows: list[dict]) -> dict[str, Any]:
    width = 560
    height = 150
    pad_x = 28
    pad_y = 18
    values = [r["monthly"]["upper"] for r in monthly_rows]
    max_y = max(values) if values else 1
    if max_y <= 0:
        max_y = 1

    def points_for(scenario: str) -> str:
        pts = []
        for idx, row in enumerate(monthly_rows):
            x = pad_x + (idx / max(len(monthly_rows) - 1, 1)) * (width - pad_x * 2)
            y = height - pad_y - (row["monthly"][scenario] / max_y) * (height - pad_y * 2)
            pts.append(f"{round(x, 1)},{round(y, 1)}")
        return " ".join(pts)

    return {
        "width": width,
        "height": height,
        "max_y": int(max_y),
        "max_y_display": _format_yen(max_y),
        "lower_points": points_for("lower"),
        "realistic_points": points_for("realistic"),
        "upper_points": points_for("upper"),
    }


def _empty_projection(months_count: int) -> dict[str, Any]:
    return {
        "has_projection": False,
        "months_count": months_count,
        "months": [],
        "milestones": [],
        "top_contributors": [],
        "chart": {},
        "total_12m": {"lower": 0, "realistic": 0, "upper": 0},
        "total_12m_display": {"lower": "¥0", "realistic": "¥0", "upper": "¥0"},
        "summary_text": "長期効果予測に必要な試算対象アクションがありません。",
        "assumptions": [],
    }


def _format_yen(value) -> str:
    try:
        return f"¥{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "¥0"
