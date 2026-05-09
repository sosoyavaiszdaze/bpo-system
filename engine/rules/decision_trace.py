"""Decision trace builders for rule selection pipelines."""
from __future__ import annotations

from typing import Any


def build_selection_traces(
    *,
    client_id: str,
    evaluation_date: str,
    loaded_rules: list[dict[str, Any]],
    matched_rules: list[dict[str, Any]],
    eligible_rules: list[dict[str, Any]],
    selected_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build coarse stage traces from auto proposal rule sets.

    This does not replace fine-grained evaluation tracing. It gives operators a
    DB-visible explanation of where rules dropped out: environment, trigger,
    eligibility, or daily cap.
    """
    loaded_ids = _ids(loaded_rules)
    matched_ids = _ids(matched_rules)
    eligible_ids = _ids(eligible_rules)
    selected_ids = _ids(selected_rules)
    traces = []
    for rule in loaded_rules:
        rid = str(rule.get("id") or "")
        if not rid:
            continue
        evidence = {
            "layer": rule.get("layer"),
            "category": rule.get("category") or rule.get("category_type"),
            "severity": rule.get("severity"),
            "root_cause_group": rule.get("root_cause_group"),
            "decision_axis": rule.get("decision_axis") or rule.get("axis_position") or rule.get("primary_axis"),
            "daily_cap_group": rule.get("daily_cap_group"),
            "applies_to": rule.get("applies_to") or {},
            "has_expected_impact": isinstance(rule.get("expected_impact"), dict),
        }
        if rid not in matched_ids:
            traces.append(_trace(client_id, rid, evaluation_date, "environment_match", "skipped", "environment_mismatch", evidence))
            continue
        traces.append(_trace(client_id, rid, evaluation_date, "environment_match", "passed", None, evidence))

        if rid not in eligible_ids:
            traces.append(_trace(client_id, rid, evaluation_date, "eligibility", "skipped", "trigger_or_prerequisite_or_cooldown", evidence))
            continue
        traces.append(_trace(client_id, rid, evaluation_date, "eligibility", "passed", None, evidence))

        if rid not in selected_ids:
            traces.append(_trace(client_id, rid, evaluation_date, "daily_cap", "suppressed", "cap_or_priority", evidence))
            continue
        traces.append(_trace(client_id, rid, evaluation_date, "daily_cap", "selected", None, evidence))
    return traces


def _trace(
    client_id: str,
    rule_id: str,
    evaluation_date: str,
    stage: str,
    status: str,
    reason: str | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "rule_id": rule_id,
        "evaluation_date": evaluation_date,
        "stage": stage,
        "status": status,
        "reason": reason,
        "evidence": evidence,
    }


def _ids(rules: list[dict[str, Any]]) -> set[str]:
    return {str(rule.get("id")) for rule in rules if rule.get("id")}
