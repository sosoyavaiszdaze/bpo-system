"""Read-only rule registry audit for ADR-019.

This module intentionally does not evaluate rules. It inspects rule metadata so
operators can see whether the YAML axes are connected well enough to support
notification ordering, impact prediction, and outcome feedback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
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
HIGH_SEVERITIES = {"critical", "high"}
REQUIRED_MESSAGING_FIELDS = (
    "customer_title",
    "priority",
    "goal_stage",
    "today_action",
    "yes_no_question",
    "action_options",
)
LEGACY_VARIANT_RE = re.compile(r"^[A-Z]\d+(?:[A-Za-z]|-.+)$")


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
    enabled: bool
    lifecycle: str
    customer_visible: bool
    prerequisite: Any = None
    messaging_payload: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    issues: tuple[str, ...] = field(default_factory=tuple)


def load_rule_registry(root: Path | str | None = None) -> list[RuleRecord]:
    root_path = Path(root) if root else ROOT
    messaging = _load_messaging(root_path)
    records: list[RuleRecord] = []
    for rel_dir in RULE_DIRS:
        for path in sorted((root_path / rel_dir).glob("*.yaml")):
            records.extend(_load_rule_file(root_path, path, messaging))
    return _with_cross_rule_issues(records)


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
    enabled = sum(1 for r in records if r.enabled)
    high_critical = sum(1 for r in records if _severity(r.severity) in HIGH_SEVERITIES and r.enabled)
    high_critical_unmapped = sum(1 for r in records if "high_severity_unmapped" in r.issues)
    customer_visible = sum(1 for r in records if r.customer_visible)
    return {
        "total_rules": total,
        "enabled_rules": enabled,
        "disabled_rules": total - enabled,
        "customer_visible_rules": customer_visible,
        "high_critical_rules": high_critical,
        "high_critical_unmapped_rules": high_critical_unmapped,
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
            "severity": record.severity,
            "lifecycle": record.lifecycle,
            "issues": list(record.issues),
            "source_path": record.source_path,
        })
    rows.sort(key=lambda row: (_issue_rank(row["issues"]), _severity_rank(row.get("severity")), row["rule_id"]))
    return rows[:limit]


def _load_rule_file(root: Path, path: Path, messaging: dict[str, dict[str, Any]]) -> list[RuleRecord]:
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
        messaging_payload = messaging.get(rid, {})
        expected_impact = _expected_impact(rule, messaging_payload)
        has_expected_impact = expected_impact is not None
        lifecycle = _lifecycle(rule)
        enabled = rule.get("enabled", True) is not False
        messaging_mapped = rid in messaging
        customer_visible = enabled and lifecycle == "active" and messaging_mapped
        issues = _issues_for(
            rule,
            layer,
            decision_axis,
            messaging_mapped,
            messaging_payload,
            expected_impact,
            lifecycle,
            enabled,
        )
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
                expected_impact=expected_impact,
                has_expected_impact=has_expected_impact,
                messaging_mapped=messaging_mapped,
                enabled=enabled,
                lifecycle=lifecycle,
                customer_visible=customer_visible,
                prerequisite=rule.get("prerequisite") or rule.get("dependencies"),
                messaging_payload=messaging_payload,
                payload=rule,
                issues=tuple(issues),
            )
        )
    return records


def _issues_for(
    rule: dict,
    layer: str,
    decision_axis: str | None,
    messaging_mapped: bool,
    messaging_payload: dict[str, Any],
    expected_impact: dict[str, Any] | None,
    lifecycle: str,
    enabled: bool,
) -> list[str]:
    issues = []
    severity = _severity(rule.get("severity"))
    if not rule.get("root_cause_group"):
        issues.append("missing_root_cause_group")
    if not decision_axis or decision_axis in {"neutral", "null"}:
        issues.append("weak_or_missing_decision_axis")
    if expected_impact is None:
        issues.append("missing_expected_impact")
    if not messaging_mapped:
        issues.append("messaging_unmapped")
    if enabled and severity in HIGH_SEVERITIES and not messaging_mapped:
        issues.append("high_severity_unmapped")
    if messaging_mapped:
        missing = [field for field in REQUIRED_MESSAGING_FIELDS if not messaging_payload.get(field)]
        if missing:
            issues.append("incomplete_customer_message_schema")
        if not messaging_payload.get("answer_source_preference"):
            issues.append("missing_answer_source_preference")
        impact = messaging_payload.get("impact_estimate") if isinstance(messaging_payload, dict) else None
        if not isinstance(impact, dict):
            issues.append("missing_impact_estimate")
        elif not isinstance(impact.get("measurement_window"), dict):
            issues.append("missing_measurement_window")
    if layer != "rules" and not isinstance(rule.get("applies_to"), dict):
        issues.append("missing_applies_to")
    if not rule.get("trigger"):
        issues.append("missing_trigger")
    trigger = rule.get("trigger") if isinstance(rule.get("trigger"), dict) else {}
    if isinstance(trigger.get("condition"), str):
        issues.append("unsafe_eval_trigger")
    if _looks_like_placeholder(rule):
        issues.append("draft_or_placeholder")
    if lifecycle == "active" and _looks_like_placeholder(rule):
        issues.append("placeholder_marked_active")
    if not rule.get("lifecycle") and not rule.get("status"):
        issues.append("missing_lifecycle")
    if _has_legacy_variant_suffix(str(rule.get("id") or "")):
        issues.append("id_variant_suffix")
    return issues


def _with_cross_rule_issues(records: list[RuleRecord]) -> list[RuleRecord]:
    """Add duplicate/dependency governance issues that need whole-registry context."""
    by_id = {r.rule_id: r for r in records}
    try:
        from engine.meta_rule_evidence import RULE_TO_GROUP
    except Exception:
        RULE_TO_GROUP = {}

    out = []
    for record in records:
        issues = list(record.issues)
        group = RULE_TO_GROUP.get(record.rule_id)
        if group:
            siblings = [rid for rid, g in RULE_TO_GROUP.items() if g == group and rid != record.rule_id and rid in by_id]
            if siblings and not (record.payload.get("replaces") or record.payload.get("extends") or record.payload.get("dedupe_group")):
                issues.append("duplicate_group_missing_relationship")

        refs = _dependency_refs(record.payload)
        missing_refs = [rid for rid in refs if rid not in by_id]
        if missing_refs:
            issues.append("missing_dependency_reference")
        if record.rule_id in _reachable_dependency_refs(record.rule_id, by_id, set()):
            issues.append("dependency_cycle")

        out.append(
            RuleRecord(
                **{**record.__dict__, "issues": tuple(dict.fromkeys(issues))}
            )
        )
    return out


def _dependency_refs(rule: dict) -> list[str]:
    refs = []
    for key in ("prerequisite", "dependencies", "conflicts", "replaces", "extends"):
        val = rule.get(key)
        if isinstance(val, str):
            refs.append(val)
        elif isinstance(val, list):
            refs.extend(str(x) for x in val if x)
    return [r for r in refs if re.match(r"^[A-Z][A-Z0-9_-]*\d", r)]


def _reachable_dependency_refs(rule_id: str, by_id: dict[str, RuleRecord], seen: set[str]) -> set[str]:
    if rule_id in seen or rule_id not in by_id:
        return set()
    seen.add(rule_id)
    refs = set(_dependency_refs(by_id[rule_id].payload))
    out = set(refs)
    for ref in refs:
        out |= _reachable_dependency_refs(ref, by_id, seen)
    return out


def _decision_axis(rule: dict) -> str | None:
    for key in ("decision_axis", "axis_position", "primary_axis"):
        value = rule.get(key)
        if value is not None:
            return str(value)
    return None


def _load_messaging(root: Path) -> dict[str, dict[str, Any]]:
    raw = _read_yaml(root / "config" / "rule_messaging.yaml")
    rules = raw.get("rules") if isinstance(raw, dict) else {}
    if not isinstance(rules, dict):
        return {}
    return {
        str(k): v if isinstance(v, dict) else {}
        for k, v in rules.items()
    }


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


def _expected_impact(rule: dict, messaging_payload: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(rule.get("expected_impact"), dict):
        return rule["expected_impact"]
    if isinstance(messaging_payload.get("impact_estimate"), dict):
        return messaging_payload["impact_estimate"]
    return None


def _lifecycle(rule: dict) -> str:
    raw = rule.get("lifecycle") or rule.get("status")
    if raw:
        return str(raw).strip().lower()
    if rule.get("enabled", True) is False:
        return "disabled"
    if _looks_like_placeholder(rule):
        return "draft"
    return "active"


def _looks_like_placeholder(rule: dict) -> bool:
    text = " ".join(str(rule.get(k) or "") for k in ("name", "rationale", "description", "note"))
    return any(marker in text for marker in ("Phase B", "詳細記述", "詳細実装", "予定"))


def _has_legacy_variant_suffix(rule_id: str) -> bool:
    if "-" in rule_id:
        return bool(re.match(r"^[GMTSC]\d+-", rule_id))
    return bool(LEGACY_VARIANT_RE.match(rule_id))


def _severity(value: Any) -> str:
    return str(value or "").strip().lower()


def _severity_rank(value: Any) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(_severity(value), 4)


def _issue_rank(issues: list[str]) -> int:
    priority = {
        "high_severity_unmapped": 0,
        "placeholder_marked_active": 1,
        "messaging_unmapped": 2,
        "incomplete_customer_message_schema": 3,
        "missing_impact_estimate": 4,
        "missing_answer_source_preference": 5,
        "unsafe_eval_trigger": 6,
    }
    return min((priority.get(issue, 50) for issue in issues), default=50)
