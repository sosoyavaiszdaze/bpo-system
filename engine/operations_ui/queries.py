"""Read-only query helpers for the operations console."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.rules.registry import load_rule_registry, summarize_rule_registry, top_rule_registry_issues
from engine.stores.clients import list_client_ids
from engine.stores.db import connect, json_loads
from engine.stores.decision_traces import list_traces, trace_summary
from engine.stores.jobs import client_health
from engine.stores.outcomes import list_outcomes, outcome_summary
from engine.stores.rules import list_registry_issues, registry_summary


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
            "rule_registry": rule_registry,
            "rule_registry_issues": rule_registry_issues,
            "decision_trace_summary": trace_summary(conn),
            "recent_decision_traces": list_traces(conn, limit=30),
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
