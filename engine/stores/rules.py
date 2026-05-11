"""DB store for ADR-019 rule registry."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from engine.rules.registry import RuleRecord
from engine.stores.db import json_dumps, utc_now

ISSUE_SEVERITY = {
    "high_severity_unmapped": "high",
    "messaging_unmapped": "high",
    "placeholder_marked_active": "high",
    "missing_expected_impact": "medium",
    "missing_impact_estimate": "medium",
    "missing_measurement_window": "medium",
    "missing_answer_source_preference": "medium",
    "incomplete_customer_message_schema": "medium",
    "missing_root_cause_group": "high",
    "weak_or_missing_decision_axis": "medium",
    "missing_applies_to": "medium",
    "missing_trigger": "high",
    "unsafe_eval_trigger": "medium",
    "draft_or_placeholder": "medium",
    "missing_lifecycle": "medium",
    "id_variant_suffix": "low",
    "duplicate_group_missing_relationship": "high",
    "missing_dependency_reference": "high",
    "dependency_cycle": "high",
}


def sync_rule_registry(conn, records: list[RuleRecord]) -> dict[str, Any]:
    """Replace DB registry rows with the current YAML-derived registry."""
    synced_at = utc_now()
    conn.execute("DELETE FROM rule_registry_issues")
    conn.execute("DELETE FROM rule_registry_operations")
    conn.execute("DELETE FROM rule_registry")
    for record in records:
        conn.execute(
            """
            INSERT INTO rule_registry (
              rule_id, canonical_rule_id, name, layer, category, severity,
              root_cause_group, decision_axis, applies_to_json, prerequisite_json,
              expected_impact_json, messaging_mapped, customer_visible, source_path,
              payload_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.rule_id,
                canonical_rule_id(record.rule_id),
                record.name,
                record.layer,
                record.category,
                record.severity,
                record.root_cause_group,
                record.decision_axis,
                json_dumps(record.applies_to),
                json.dumps(record.prerequisite, ensure_ascii=False, sort_keys=True),
                json.dumps(record.expected_impact, ensure_ascii=False, sort_keys=True),
                1 if record.messaging_mapped else 0,
                1 if record.customer_visible else 0,
                record.source_path,
                json_dumps(record.payload),
                synced_at,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO rule_registry_operations (
              rule_id, lifecycle, owner, required_data_sources_json,
              duplicate_group, prerequisite_rule_ids_json, conflict_rule_ids_json,
              rule_family, payload_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.rule_id,
                record.lifecycle,
                record.payload.get("owner"),
                json_dumps(_required_data_sources(record.payload)),
                record.payload.get("dedupe_group") or record.payload.get("duplicate_group"),
                json_dumps(_rule_refs(record.payload, "prerequisite", "dependencies", "extends", "replaces")),
                json_dumps(_rule_refs(record.payload, "conflicts")),
                record.root_cause_group or record.category,
                json_dumps({
                    "source_path": record.source_path,
                    "issues": list(record.issues),
                    "customer_visible": record.customer_visible,
                }),
                synced_at,
            ),
        )
        for issue in record.issues:
            conn.execute(
                """
                INSERT INTO rule_registry_issues (
                  issue_id, rule_id, issue_type, severity, source_path, payload_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_id_for(record.rule_id, issue),
                    record.rule_id,
                    issue,
                    ISSUE_SEVERITY.get(issue, "medium"),
                    record.source_path,
                    json_dumps({"name": record.name, "layer": record.layer}),
                    synced_at,
                ),
            )
    return {
        "rules_synced": len(records),
        "issues_synced": sum(len(record.issues) for record in records),
        "synced_at": synced_at,
    }


def registry_summary(conn) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) AS n FROM rule_registry").fetchone()["n"]
    if not total:
        return {
            "total_rules": 0,
            "messaging_mapped": 0,
            "messaging_coverage_pct": 0.0,
            "expected_impact_rules": 0,
            "expected_impact_coverage_pct": 0.0,
            "root_cause_group_rules": 0,
            "root_cause_group_coverage_pct": 0.0,
            "decision_axis_rules": 0,
            "decision_axis_coverage_pct": 0.0,
            "layer_counts": {},
            "issue_counts": {},
        }
    row = conn.execute(
        """
        SELECT
          SUM(messaging_mapped) AS messaging_mapped,
          SUM(CASE WHEN expected_impact_json != 'null' THEN 1 ELSE 0 END) AS expected_impact_rules,
          SUM(CASE WHEN root_cause_group IS NOT NULL AND root_cause_group != '' THEN 1 ELSE 0 END) AS root_cause_group_rules,
          SUM(CASE WHEN decision_axis IS NOT NULL AND decision_axis NOT IN ('', 'neutral', 'null') THEN 1 ELSE 0 END) AS decision_axis_rules
        FROM rule_registry
        """
    ).fetchone()
    layer_counts = {
        r["layer"]: r["n"]
        for r in conn.execute("SELECT layer, COUNT(*) AS n FROM rule_registry GROUP BY layer ORDER BY layer").fetchall()
    }
    issue_counts = {
        r["issue_type"]: r["n"]
        for r in conn.execute(
            "SELECT issue_type, COUNT(*) AS n FROM rule_registry_issues GROUP BY issue_type ORDER BY issue_type"
        ).fetchall()
    }
    return {
        "total_rules": total,
        "messaging_mapped": int(row["messaging_mapped"] or 0),
        "messaging_coverage_pct": _pct(row["messaging_mapped"] or 0, total),
        "expected_impact_rules": int(row["expected_impact_rules"] or 0),
        "expected_impact_coverage_pct": _pct(row["expected_impact_rules"] or 0, total),
        "root_cause_group_rules": int(row["root_cause_group_rules"] or 0),
        "root_cause_group_coverage_pct": _pct(row["root_cause_group_rules"] or 0, total),
        "decision_axis_rules": int(row["decision_axis_rules"] or 0),
        "decision_axis_coverage_pct": _pct(row["decision_axis_rules"] or 0, total),
        "layer_counts": layer_counts,
        "issue_counts": issue_counts,
    }


def list_registry_issues(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT i.rule_id, r.name, r.layer, r.category, r.source_path,
               GROUP_CONCAT(i.issue_type, ',') AS issues
        FROM rule_registry_issues i
        LEFT JOIN rule_registry r ON r.rule_id = i.rule_id
        GROUP BY i.rule_id, r.name, r.layer, r.category, r.source_path
        ORDER BY
          MIN(CASE i.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END),
          i.rule_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "rule_id": row["rule_id"],
            "name": row["name"],
            "layer": row["layer"],
            "category": row["category"],
            "source_path": row["source_path"],
            "issues": [x for x in str(row["issues"] or "").split(",") if x],
        }
        for row in rows
    ]


def meta_rule_operations_summary(conn) -> dict[str, Any]:
    """Meta-first rule operations readiness summary."""
    summary = rule_operations_summary(conn, family="meta")
    return {
        "meta_total": summary["total"],
        "meta_customer_visible": summary["customer_visible"],
        "meta_high_critical": summary["high_critical"],
        "meta_high_critical_unmapped": summary["high_critical_unmapped"],
        "meta_required_data_sources": summary["required_data_sources"],
        "meta_duplicate_group_defined": summary["duplicate_group_defined"],
    }


def rule_operations_summary(conn, family: str | None = None) -> dict[str, Any]:
    """Operational readiness summary for one rule family or the whole registry."""
    where = ""
    params: list[Any] = []
    if family:
        where = """
        WHERE r.source_path LIKE ?
           OR r.rule_id LIKE ?
           OR o.rule_family = ?
           OR r.category = ?
        """
        prefix = _family_prefix(family)
        params.extend([f"%{family}_rules.yaml", f"{prefix}%", family, family])
    rows = conn.execute(
        f"""
        SELECT r.rule_id, r.severity, r.messaging_mapped, r.customer_visible,
               o.lifecycle, o.duplicate_group, o.required_data_sources_json
        FROM rule_registry r
        LEFT JOIN rule_registry_operations o ON o.rule_id = r.rule_id
        {where}
        """,
        params,
    ).fetchall()
    total = len(rows)
    high_critical = [
        r for r in rows
        if r["severity"] in {"critical", "high"} and (r["lifecycle"] or "active") == "active"
    ]
    visible = [r for r in rows if r["customer_visible"]]
    with_sources = [r for r in rows if r["required_data_sources_json"] not in (None, "", "[]")]
    with_duplicate_group = [r for r in rows if r["duplicate_group"]]
    return {
        "family": family or "all",
        "total": total,
        "customer_visible": len(visible),
        "high_critical": len(high_critical),
        "high_critical_unmapped": len([r for r in high_critical if not r["messaging_mapped"]]),
        "required_data_sources": len(with_sources),
        "duplicate_group_defined": len(with_duplicate_group),
    }


def family_operations_matrix(conn) -> list[dict[str, Any]]:
    """Readiness matrix across major rule families."""
    families = ["meta", "google", "tiktok", "seo", "adtruth", "legal", "ec_platform"]
    return [rule_operations_summary(conn, family=f) for f in families]


def canonical_rule_id(rule_id: str) -> str:
    return rule_id.strip().replace("_", "-").upper()


def issue_id_for(rule_id: str, issue_type: str) -> str:
    return hashlib.sha256(f"{rule_id}|{issue_type}".encode("utf-8")).hexdigest()[:32]


def _pct(value: int | float, total: int) -> float:
    return round((float(value) / total * 100), 1) if total else 0.0


def _required_data_sources(rule: dict) -> list[str]:
    out = []
    for item in rule.get("data_source") or rule.get("data_sources") or []:
        if isinstance(item, dict) and item.get("source"):
            out.append(str(item["source"]))
        elif isinstance(item, str):
            out.append(item)
    return sorted(set(out))


def _rule_refs(rule: dict, *keys: str) -> list[str]:
    refs = []
    for key in keys:
        val = rule.get(key)
        if isinstance(val, str):
            refs.append(val)
        elif isinstance(val, list):
            refs.extend(str(x) for x in val if x)
    return sorted(set(refs))


def _family_prefix(family: str) -> str:
    return {
        "meta": "M",
        "google": "G",
        "tiktok": "T",
        "seo": "S",
        "adtruth": "F",
        "legal": "F-LC",
        "ec_platform": "V-EC",
    }.get(family, family.upper())
