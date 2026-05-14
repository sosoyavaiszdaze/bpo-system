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
from engine.stores.readiness import latest_readiness_snapshot
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
        case_inbox = _case_inbox(conn)
        response_summary = _response_summary(conn)
        outcomes = outcome_summary(conn)
        recent_outcomes = list_outcomes(conn, limit=30)
        rule_outcome_rollups = list_rule_outcome_rollups(conn, limit=30)
        connections = connection_summary(conn)
        recent_connections = list_client_connections(conn)[:30]
        incidents = incident_summary(conn)
        open_incidents = list_open_incidents(conn, limit=30)
        operational_readiness = latest_readiness_snapshot(conn)
        rule_family_operations = _safe_rule_family_ops(conn)
        rule_learning_stats = _safe_rule_learning_stats(conn)
        return {
            "clients": health_rows,
            "daily_ops": _daily_ops_summary(
                clients=health_rows,
                cases=case_inbox,
                responses=response_summary,
                outcomes=outcomes,
                connections=connections,
                incidents=incidents,
                readiness=operational_readiness,
                rule_registry=rule_registry,
            ),
            "case_status_board": _case_status_board(conn),
            "connection_matrix": _connection_matrix(conn),
            "outcome_workbench": _outcome_workbench(conn, recent_outcomes, rule_outcome_rollups),
            "rule_quality_board": _rule_quality_board(rule_registry, rule_registry_issues, rule_family_operations),
            "case_inbox": case_inbox,
            "response_summary": response_summary,
            "recent_responses": _recent_responses(conn),
            "recent_jobs": _recent_jobs(conn),
            "outcomes": outcomes,
            "recent_outcomes": recent_outcomes,
            "rule_outcome_rollups": rule_outcome_rollups,
            "rule_learning_stats": rule_learning_stats,
            "connections": connections,
            "recent_connections": recent_connections,
            "incidents": incidents,
            "open_incidents": open_incidents,
            "operational_readiness": operational_readiness,
            "rule_registry": rule_registry,
            "rule_registry_issues": rule_registry_issues,
            "meta_rule_operations": _safe_meta_rule_ops(conn),
            "rule_family_operations": rule_family_operations,
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


def _case_status_board(conn) -> list[dict[str, Any]]:
    labels = {
        "detected": "検知",
        "notified": "通知済み",
        "acknowledged": "確認済み",
        "planned": "対応予定",
        "executed": "実行済み",
        "verified": "実装確認",
        "measuring": "効果測定中",
        "learned": "学習反映",
        "closed": "完了",
        "open": "未着手",
        "waiting_client": "顧客待ち",
        "waiting_zynect": "Zynect待ち",
        "implemented": "実装済み",
        "monitoring": "監視中",
        "resolved": "解消",
    }
    order = [
        "detected",
        "notified",
        "acknowledged",
        "planned",
        "executed",
        "verified",
        "measuring",
        "learned",
        "closed",
        "open",
        "waiting_client",
        "waiting_zynect",
        "implemented",
        "monitoring",
        "resolved",
    ]
    rows = conn.execute(
        """
        SELECT status,
               COUNT(*) AS n,
               SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical,
               SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high
        FROM operational_cases
        GROUP BY status
        """
    ).fetchall()
    by_status = {row["status"]: dict(row) for row in rows}
    out = []
    for status in order:
        row = by_status.pop(status, None)
        if row:
            out.append({
                "status": status,
                "label": labels.get(status, status),
                "count": row["n"],
                "critical": row["critical"] or 0,
                "high": row["high"] or 0,
            })
    for status, row in sorted(by_status.items()):
        out.append({
            "status": status,
            "label": labels.get(status, status),
            "count": row["n"],
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
        })
    return out


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


def _connection_matrix(conn) -> dict[str, Any]:
    rows = list_client_connections(conn)
    missing_required = [r for r in rows if r.get("required") and r.get("status") != "configured"]
    missing_recommended = [
        r for r in rows
        if r.get("strongly_recommended") and r.get("status") != "configured"
    ]
    by_client: dict[str, dict[str, Any]] = {}
    for row in rows:
        client = by_client.setdefault(
            row["client_id"],
            {
                "client_id": row["client_id"],
                "total": 0,
                "configured": 0,
                "missing_required": 0,
                "missing_recommended": 0,
                "connection_items": [],
            },
        )
        client["total"] += 1
        if row.get("status") == "configured":
            client["configured"] += 1
        if row.get("required") and row.get("status") != "configured":
            client["missing_required"] += 1
        if row.get("strongly_recommended") and row.get("status") != "configured":
            client["missing_recommended"] += 1
        if row.get("status") != "configured" or row.get("required"):
            client["connection_items"].append(row)
    return {
        "clients": sorted(by_client.values(), key=lambda r: (-r["missing_required"], -r["missing_recommended"], r["client_id"])),
        "attention_items": missing_required + missing_recommended,
        "missing_required": len(missing_required),
        "missing_recommended": len(missing_recommended),
    }


def _outcome_workbench(conn, recent_outcomes: list[dict[str, Any]], rollups: list[dict[str, Any]]) -> dict[str, Any]:
    pending = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM outcome_measurements
        WHERE measured_value IS NULL AND baseline_value IS NOT NULL
        """
    ).fetchone()["n"]
    measured = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM outcome_measurements
        WHERE measured_value IS NOT NULL
        """
    ).fetchone()["n"]
    improved = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM outcome_measurements
        WHERE measured_value IS NOT NULL AND change_pct > 0
        """
    ).fetchone()["n"]
    value = conn.execute(
        """
        SELECT SUM(COALESCE(estimated_value_yen, 0)) AS v
        FROM outcome_measurements
        WHERE measured_value IS NOT NULL OR estimated_value_yen IS NOT NULL
        """
    ).fetchone()["v"] or 0
    return {
        "pending_baselines": pending or 0,
        "measured": measured or 0,
        "improved": improved or 0,
        "win_rate_pct": round((float(improved or 0) / float(measured or 1)) * 100, 1) if measured else 0.0,
        "estimated_value_yen": float(value),
        "recent": recent_outcomes[:8],
        "rollups": rollups[:8],
    }


def _rule_quality_board(
    registry: dict[str, Any],
    issues: list[dict[str, Any]],
    family_ops: list[dict[str, Any]],
) -> dict[str, Any]:
    issue_counts = registry.get("issue_counts") or {}
    blocker_types = {
        "high_severity_unmapped",
        "messaging_unmapped",
        "missing_trigger",
        "missing_root_cause_group",
        "duplicate_group_missing_relationship",
        "missing_dependency_reference",
        "dependency_cycle",
    }
    blocker_count = sum(int(issue_counts.get(key, 0) or 0) for key in blocker_types)
    return {
        "blocker_count": blocker_count,
        "coverage": {
            "messaging": registry.get("messaging_coverage_pct", 0),
            "impact": registry.get("expected_impact_coverage_pct", 0),
            "root_cause": registry.get("root_cause_group_coverage_pct", 0),
            "decision_axis": registry.get("decision_axis_coverage_pct", 0),
        },
        "top_issues": sorted(issue_counts.items(), key=lambda kv: kv[1], reverse=True)[:8],
        "family_operations": family_ops,
        "sample_issues": issues[:8],
    }


def _daily_ops_summary(
    *,
    clients: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    responses: dict[str, Any],
    outcomes: dict[str, Any],
    connections: dict[str, Any],
    incidents: dict[str, Any],
    readiness: dict[str, Any] | None,
    rule_registry: dict[str, Any],
) -> dict[str, Any]:
    critical_cases = sum(1 for c in cases if c.get("severity") == "critical")
    waiting_client = sum(1 for c in cases if c.get("status") == "waiting_client")
    waiting_zynect = sum(1 for c in cases if c.get("status") == "waiting_zynect")
    readiness = readiness or {}
    readiness_status = readiness.get("status") or "unknown"
    return {
        "clients": len(clients),
        "open_cases": sum(int(c.get("open_cases_count") or 0) for c in clients),
        "critical_cases": critical_cases,
        "waiting_client": waiting_client,
        "waiting_zynect": waiting_zynect,
        "responses_total": responses.get("total", 0),
        "outcome_value_yen": outcomes.get("total_estimated_value_yen", 0),
        "missing_required_connections": connections.get("missing_required", 0),
        "missing_secrets": connections.get("missing_secrets", 0),
        "open_incidents": incidents.get("open_incidents", 0),
        "readiness_status": readiness_status,
        "readiness_blockers": readiness.get("blocker_count", 0),
        "readiness_warnings": readiness.get("warning_count", 0),
        "rule_total": rule_registry.get("total_rules", 0),
        "rule_messaging_coverage_pct": rule_registry.get("messaging_coverage_pct", 0),
    }


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
