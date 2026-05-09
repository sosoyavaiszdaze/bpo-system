"""Read-only rule registry audit for ADR-019.

This module intentionally does not evaluate rules. It inspects rule metadata so
operators can see whether the YAML axes are connected well enough to support
notification ordering, impact prediction, and outcome feedback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULE_DIRS = (
    "config/rules",
    "config/foundation",
    "config/verticals",
    "config/ec_platforms",
    "config/precision_categories",
)


@dataclass(frozen=True)
class RuleRecord:
    rule_id: str
    name: str
    layer: str
    category: str
    source_path: str
    severity: str | None
    root_cause_group: str | None
    decision_axis: str | None
    applies_to_keys: tuple[str, ...]
    applies_to: dict[str, Any]
    expected_impact: dict[str, Any] | None
    has_expected_impact: bool
    messaging_mapped: bool
    prerequisite: Any = None
    payload: dict[str, Any] = field(default_factory=dict)
    issues: tuple[str, ...] = field(default_factory=tuple)


def load_rule_registry(root: Path | str | None = None) -> list[RuleRecord]:
    root_path = Path(root) if root else ROOT
    messaging_ids = _load_messaging_ids(root_path)
    records: list[RuleRecord] = []
    for rel_dir in RULE_DIRS:
        for path in sorted((root_path / rel_dir).glob("*.yaml")):
            records.extend(_load_rule_file(root_path, path, messaging_ids))
    return records


def summarize_rule_registry(records: list[RuleRecord]) -> dict[str, Any]:
    total = len(records)
    issue_counts: dict[str, int] = {}
    layer_counts: dict[str, int] = {}
    for record in records:
        layer_counts[record.layer] = layer_counts.get(record.layer, 0) + 1
        for issue in record.issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    mapped = sum(1 for r in records if r.messaging_mapped)
    with_impact = sum(1 for r in records if r.has_expected_impact)
    with_root = sum(1 for r in records if r.root_cause_group)
    with_axis = sum(1 for r in records if r.decision_axis and r.decision_axis not in {"neutral", "null"})
    return {
        "total_rules": total,
        "messaging_mapped": mapped,
        "messaging_coverage_pct": _pct(mapped, total),
        "expected_impact_rules": with_impact,
        "expected_impact_coverage_pct": _pct(with_impact, total),
        "root_cause_group_rules": with_root,
        "root_cause_group_coverage_pct": _pct(with_root, total),
        "decision_axis_rules": with_axis,
        "decision_axis_coverage_pct": _pct(with_axis, total),
        "layer_counts": dict(sorted(layer_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
    }


def top_rule_registry_issues(records: list[RuleRecord], limit: int = 50) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if not record.issues:
            continue
        rows.append({
            "rule_id": record.rule_id,
            "name": record.name,
            "layer": record.layer,
            "category": record.category,
            "issues": list(record.issues),
            "source_path": record.source_path,
        })
    return rows[:limit]


def _load_rule_file(root: Path, path: Path, messaging_ids: set[str]) -> list[RuleRecord]:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        return []
    rules = raw.get("rules")
    if not isinstance(rules, list):
        return []

    default_layer = str(raw.get("layer") or _infer_layer(path))
    default_category = str(raw.get("category") or path.stem)
    records = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rid = str(rule.get("id") or "").strip()
        if not rid:
            continue
        layer = str(rule.get("layer") or default_layer)
        category = str(rule.get("category") or rule.get("category_type") or default_category)
        applies_to = rule.get("applies_to") if isinstance(rule.get("applies_to"), dict) else {}
        decision_axis = _decision_axis(rule)
        issues = _issues_for(rule, layer, decision_axis, rid in messaging_ids)
        records.append(
            RuleRecord(
                rule_id=rid,
                name=str(rule.get("name") or rid),
                layer=layer,
                category=category,
                source_path=str(path.relative_to(root)),
                severity=rule.get("severity"),
                root_cause_group=rule.get("root_cause_group"),
                decision_axis=decision_axis,
                applies_to_keys=tuple(sorted(str(k) for k in applies_to.keys())),
                applies_to=applies_to,
                expected_impact=rule.get("expected_impact") if isinstance(rule.get("expected_impact"), dict) else None,
                has_expected_impact=isinstance(rule.get("expected_impact"), dict),
                messaging_mapped=rid in messaging_ids,
                prerequisite=rule.get("prerequisite") or rule.get("dependencies"),
                payload=rule,
                issues=tuple(issues),
            )
        )
    return records


def _issues_for(rule: dict, layer: str, decision_axis: str | None, messaging_mapped: bool) -> list[str]:
    issues = []
    if not rule.get("root_cause_group"):
        issues.append("missing_root_cause_group")
    if not decision_axis or decision_axis in {"neutral", "null"}:
        issues.append("weak_or_missing_decision_axis")
    if not isinstance(rule.get("expected_impact"), dict):
        issues.append("missing_expected_impact")
    if not messaging_mapped:
        issues.append("messaging_unmapped")
    if layer != "rules" and not isinstance(rule.get("applies_to"), dict):
        issues.append("missing_applies_to")
    if not rule.get("trigger"):
        issues.append("missing_trigger")
    return issues


def _decision_axis(rule: dict) -> str | None:
    for key in ("decision_axis", "axis_position", "primary_axis"):
        value = rule.get(key)
        if value is not None:
            return str(value)
    return None


def _load_messaging_ids(root: Path) -> set[str]:
    raw = _read_yaml(root / "config" / "rule_messaging.yaml")
    rules = raw.get("rules") if isinstance(raw, dict) else {}
    return {str(k) for k in rules.keys()} if isinstance(rules, dict) else set()


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _infer_layer(path: Path) -> str:
    parent = path.parent.name
    if parent == "rules":
        return "layer_a"
    if parent == "precision_categories":
        return "precision"
    return parent


def _pct(value: int, total: int) -> float:
    return round((value / total * 100), 1) if total else 0.0
