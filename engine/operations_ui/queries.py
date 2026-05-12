"""Read-only query helpers for the operations console."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.rules.registry import load_rule_registry, summarize_rule_registry, top_rule_registry_issues
from engine.stores.clients import list_client_ids
from engine.stores.db import connect, json_loads
from engine.stores.decision_traces import list_traces, trace_summary
from engine.stores.jobs import client_health
from engine.stores.learning import list_rule_learning_stats
from engine.stores.monitoring import incident_summary, list_open_incidents
from engine.stores.outcomes import list_outcomes, list_rule_outcome_rollups, outcome_summary
from engine.stores.rule_drafts import list_rule_drafts
from engine.stores.rules import family_operations_matrix, list_registry_issues, meta_rule_operations_summary, registry_summary
from engine.stores.connections import connection_summary, list_client_connections
from engine.vertical_kpi_registry import build_client_kpi_readiness


def build_console_context(db_path: Path | str | None = None, root: Path | str | None = None) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        clients = _clients(conn)
        health_rows = []
        for client in clients:
            row = client_health(conn, client["client_id"])
            row["display_name"] = client["display_name"]
            row["vertical"] = client["vertical"]
            row["ec_platform"] = client["ec_platform"]
            row["kpi_readiness"] = build_client_kpi_readiness(client["client_id"], client)
            health_rows.append(row)

        rule_registry, rule_registry_issues = _rule_registry_context(conn, root)
        return {
            "clients": health_rows,
            "case_inbox": _case_inbox(conn),
            "response_summary": _response_summary(conn),
            "recent_responses": _recent_responses(conn),
            "recent_jobs": _recent_jobs(conn),
            "outcomes": outcome_summary(conn),
            "recent_outcomes": list_outcomes(conn, limit=30),
            "rule_outcome_rollups": list_rule_outcome_rollups(conn, limit=30),
            "rule_learning_stats": _safe_rule_learning_stats(conn),
            "connections": connection_summary(conn),
            "recent_connections": list_client_connections(conn)[:30],
            "incidents": incident_summary(conn),
            "open_incidents": list_open_incidents(conn, limit=30),
            "rule_registry": rule_registry,
            "rule_registry_issues": rule_registry_issues,
            "meta_rule_operations": _safe_meta_rule_ops(conn),
            "rule_family_operations": _safe_rule_family_ops(conn),
            "rule_change_drafts": _safe_rule_drafts(conn),
            "decision_trace_summary": trace_summary(conn),
            "recent_decision_traces": _enriched_traces(conn),
        }
    finally:
        conn.close()


def _clients(conn) -> list[dict[str, Any]]:
    ids = list_client_ids(conn)
    rows = []
    for client_id in ids:
        row = conn.execute(
            "SELECT client_id, display_name, vertical, ec_platform, status FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if row:
            rows.append(dict(row))
    return rows


def _case_inbox(conn, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT case_id, client_id, rule_id, title, status, severity, owner_type,
               first_detected_at, last_detected_at, updated_at
        FROM operational_cases
        WHERE status IN ('open', 'waiting_client', 'waiting_zynect', 'planned', 'implemented', 'monitoring')
        ORDER BY
          CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
          updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _recent_jobs(conn, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT job_run_id, job_name, client_id, started_at, finished_at, status, error_json, metrics_json
        FROM job_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["errors"] = json_loads(data.pop("error_json"), [])
        data["metrics"] = json_loads(data.pop("metrics_json"), {})
        out.append(data)
    return out


def _response_summary(conn) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM client_responses
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {
        "total": sum(counts.values()),
        "confirmed_done": counts.get("confirmed_done", 0),
        "not_done": counts.get("not_done", 0),
        "wants_help": counts.get("wants_help", 0),
        "not_applicable": counts.get("not_applicable", 0),
        "status_counts": counts,
    }


def _recent_responses(conn, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT response_id, client_id, rule_id, case_id, answer_code, answer_label,
               status, answered_at, source
        FROM client_responses
        ORDER BY answered_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _rule_registry_context(conn, root: Path | str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Use DB-backed registry when synced; fall back to live YAML audit."""
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM rule_registry").fetchone()["n"]
    except Exception:
        count = 0
    if count:
        return registry_summary(conn), list_registry_issues(conn, limit=30)
    records = load_rule_registry(root)
    return summarize_rule_registry(records), top_rule_registry_issues(records, limit=30)


def _safe_meta_rule_ops(conn) -> dict[str, Any]:
    try:
        return meta_rule_operations_summary(conn)
    except Exception:
        return {}


def _safe_rule_family_ops(conn) -> list[dict[str, Any]]:
    try:
        return family_operations_matrix(conn)
    except Exception:
        return []


def _safe_rule_drafts(conn) -> list[dict[str, Any]]:
    try:
        return list_rule_drafts(conn, status="review_required", limit=20)
    except Exception:
        return []


def _safe_rule_learning_stats(conn) -> list[dict[str, Any]]:
    try:
        return list_rule_learning_stats(conn, limit=30)
    except Exception:
        return []


def _enriched_traces(conn, limit: int = 30) -> list[dict[str, Any]]:
    rows = list_traces(conn, limit=limit)
    for row in rows:
        evidence = row.get("evidence") or {}
        value = evidence.get("value") or {}
        row["rule_group"] = evidence.get("rule_group")
        row["duplicate_group_members"] = evidence.get("duplicate_group_members") or []
        if value:
            bits = []
            for key in ("pixel_installed", "capi_enabled", "domain_verified", "event_match_quality", "cpa", "roas", "conversions"):
                if key in value:
                    bits.append(f"{key}={value.get(key)}")
            for key in ("worst_campaigns", "worst_adsets", "worst_ads", "worst_placements"):
                if value.get(key):
                    first = value[key][0]
                    bits.append(f"{key}={first.get('name') or first.get('id')}")
            row["evidence_summary"] = " / ".join(bits) if bits else str(value)[:160]
        else:
            row["evidence_summary"] = "-"
    return rows
