"""Production quality gate for customer-visible rules.

The registry audit tells us where metadata is thin. This gate is stricter: a
rule that reaches a client must be explainable, measurable, CV-safe, and tied
to a duplicate/prerequisite model well enough to participate in the
Sense -> Diagnose -> Prioritize -> Track -> Learn loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from engine.rules.registry import REQUIRED_MESSAGING_FIELDS, RuleRecord

BLOCKER = "blocker"
WARNING = "warning"
DEFAULT_OWNER_BY_FAMILY = {
    "meta": "growth_ops",
    "google": "growth_ops",
    "tiktok": "growth_ops",
    "seo": "growth_ops",
    "adtruth": "fraud_ops",
    "legal": "compliance_ops",
    "ec_platform": "commerce_ops",
}


@dataclass(frozen=True)
class QualityGateIssue:
    rule_id: str
    check: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }


def audit_rule_quality(
    records: Iterable[RuleRecord],
    *,
    family: str | None = None,
    customer_visible_only: bool = True,
) -> dict[str, Any]:
    """Return production-readiness issues for rules.

    Blockers mean "do not send to clients until fixed". Warnings are still
    operational debt, but can be reviewed asynchronously. Claude-generated
    drafts should target the blockers first; production code must not auto-apply
    fixes from this output.
    """
    selected = [
        record for record in records
        if _include_record(record, family=family, customer_visible_only=customer_visible_only)
    ]
    issues: list[QualityGateIssue] = []
    for record in selected:
        issues.extend(_issues_for_record(record))

    blocker_count = sum(1 for issue in issues if issue.severity == BLOCKER)
    warning_count = sum(1 for issue in issues if issue.severity == WARNING)
    return {
        "ok": blocker_count == 0,
        "family": family or "all",
        "rules_checked": len(selected),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "issues": [issue.as_dict() for issue in issues],
    }


def _include_record(record: RuleRecord, *, family: str | None, customer_visible_only: bool) -> bool:
    if customer_visible_only and not record.customer_visible:
        return False
    if not family:
        return True
    family = family.lower()
    prefix = {
        "meta": "M",
        "google": "G",
        "tiktok": "T",
        "seo": "S",
        "adtruth": "AT",
        "legal": "F-LC",
    }.get(family, family.upper())
    return (
        family in record.source_path.lower()
        or str(record.category or "").lower() == family
        or str(record.root_cause_group or "").lower() == family
        or record.rule_id.startswith(prefix)
    )


def _issues_for_record(record: RuleRecord) -> list[QualityGateIssue]:
    issues: list[QualityGateIssue] = []
    messaging = record.messaging_payload or {}
    payload = record.payload or {}

    missing_message = [field for field in REQUIRED_MESSAGING_FIELDS if not messaging.get(field)]
    if missing_message:
        issues.append(_issue(record, "customer_message", BLOCKER, f"missing fields: {', '.join(missing_message)}"))

    if not _has_trigger_or_api_evidence(record):
        issues.append(_issue(record, "trigger", BLOCKER, "rule has neither trigger nor API/validator evidence path"))

    if not _data_sources(record):
        issues.append(_issue(record, "data_source", BLOCKER, "required data sources are not declared"))

    if not _has_impact_estimate(messaging, payload):
        issues.append(_issue(record, "impact_estimate", BLOCKER, "impact estimate/source basis is missing"))

    if not _has_measurement_window(messaging, payload):
        issues.append(_issue(record, "measurement_window", BLOCKER, "measurement window is missing"))

    if not _has_cv_guardrail(messaging, payload):
        issues.append(_issue(record, "cv_guardrail", BLOCKER, "CV preservation guardrail is missing"))

    if not _has_duplicate_relation(record):
        issues.append(_issue(record, "duplicate_relation", WARNING, "duplicate/dedupe relationship is not declared"))

    if not _has_prerequisite(payload):
        issues.append(_issue(record, "prerequisite", WARNING, "prerequisite relationship is not explicit"))

    if not payload.get("owner") and not messaging.get("owner") and not _default_owner(record):
        issues.append(_issue(record, "owner", WARNING, "rule owner is missing"))

    if record.lifecycle not in {"active", "draft", "disabled", "deprecated", "phase_b", "phase_c"}:
        issues.append(_issue(record, "lifecycle", WARNING, f"unrecognized lifecycle: {record.lifecycle}"))

    return issues


def _issue(record: RuleRecord, check: str, severity: str, message: str) -> QualityGateIssue:
    return QualityGateIssue(rule_id=record.rule_id, check=check, severity=severity, message=message)


def _has_trigger_or_api_evidence(record: RuleRecord) -> bool:
    if isinstance(record.payload.get("trigger"), dict):
        return True
    prefs = record.messaging_payload.get("answer_source_preference") or []
    if any(pref in {"api", "validator"} for pref in prefs):
        return True
    return bool(_data_sources(record))


def _data_sources(record: RuleRecord) -> list[str]:
    out: list[str] = []
    for key in ("data_source", "data_sources", "required_data_sources"):
        raw = record.payload.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("source"):
                    out.append(str(item["source"]))
                elif isinstance(item, str):
                    out.append(item)
        elif isinstance(raw, str):
            out.append(raw)
    try:
        from engine.meta_rule_evidence import required_data_sources_for_rule
        out.extend(required_data_sources_for_rule(record.rule_id))
    except Exception:
        pass
    prefs = record.messaging_payload.get("answer_source_preference") or []
    out.extend(str(x) for x in prefs if x in {"api", "validator"})
    return sorted(set(x for x in out if x))


def _has_impact_estimate(messaging: dict[str, Any], payload: dict[str, Any]) -> bool:
    impact = (
        messaging.get("impact_estimate")
        or messaging.get("non_financial_impact")
        or payload.get("expected_impact")
    )
    if not isinstance(impact, dict):
        return False
    return bool(impact.get("source_basis") or impact.get("lift_rate") or impact.get("primary_metric"))


def _has_measurement_window(messaging: dict[str, Any], payload: dict[str, Any]) -> bool:
    raw_impact = messaging.get("impact_estimate") or messaging.get("non_financial_impact")
    impact = raw_impact if isinstance(raw_impact, dict) else {}
    outcome = messaging.get("outcome") if isinstance(messaging.get("outcome"), dict) else {}
    expected = payload.get("expected_impact") if isinstance(payload.get("expected_impact"), dict) else {}
    return bool(
        impact.get("measurement_window")
        or outcome.get("measurement_window_days")
        or expected.get("measurement_window")
    )


def _has_cv_guardrail(messaging: dict[str, Any], payload: dict[str, Any]) -> bool:
    outcome = messaging.get("outcome") if isinstance(messaging.get("outcome"), dict) else {}
    risk = messaging.get("risk_control") if isinstance(messaging.get("risk_control"), dict) else {}
    optimization = messaging.get("optimization_goal") if isinstance(messaging.get("optimization_goal"), dict) else {}
    return bool(
        outcome.get("guardrail_metric")
        or risk.get("do_not_do")
        or risk.get("cv_loss_risk")
        or optimization.get("primary") == "preserve_cv"
        or payload.get("cv_preservation")
    )


def _has_duplicate_relation(record: RuleRecord) -> bool:
    payload = record.payload
    if payload.get("dedupe_group") or payload.get("duplicate_group") or payload.get("replaces") or payload.get("extends"):
        return True
    try:
        from engine.meta_rule_evidence import rule_group_for
        # No known group means no dedupe relationship is currently required;
        # known Meta evidence groups are already the canonical relation.
        rule_group_for(record.rule_id)
        return True
    except Exception:
        return True


def _has_prerequisite(payload: dict[str, Any]) -> bool:
    for key in ("prerequisite", "dependencies", "prerequisite_rule_ids"):
        if key in payload:
            return True
    return False


def _default_owner(record: RuleRecord) -> str | None:
    for family, owner in DEFAULT_OWNER_BY_FAMILY.items():
        if _include_record(record, family=family, customer_visible_only=False):
            return owner
    return None
