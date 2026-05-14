"""Operational readiness gate for production-scale operation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from engine.rules.quality_gate import audit_rule_quality
from engine.rules.registry import RuleRecord
from engine.stores.audit import record_audit_event
from engine.stores.db import json_dumps, json_loads, utc_now
from engine.stores.monitoring import open_incident, record_health_check, resolve_incident

CONNECTED_STATUSES = {"ok", "success", "connected", "active", "healthy"}


def readiness_snapshot_id(checked_at: str) -> str:
    return hashlib.sha256(f"operational_readiness|{checked_at}".encode("utf-8")).hexdigest()[:32]


def evaluate_operational_readiness(
    conn,
    *,
    records: Iterable[RuleRecord] | None = None,
    checked_at: str | None = None,
    max_job_freshness_hours: float = 26.0,
    max_rule_draft_backlog: int = 30,
) -> dict[str, Any]:
    """Evaluate production SLO-style readiness.

    This gate aggregates the most important non-infrastructure risks:
    incidents, data freshness, required connections/secrets, rule quality,
    outcome measurement lag, and rule-improvement backlog.
    """
    checked_at = checked_at or utc_now()
    now = _parse_dt(checked_at) or datetime.now(timezone.utc)
    issues: list[dict[str, Any]] = []

    issues.extend(_incident_issues(conn))
    issues.extend(_job_freshness_issues(conn, now, max_job_freshness_hours))
    issues.extend(_connection_issues(conn))
    issues.extend(_secret_issues(conn, now))
    issues.extend(_stale_case_issues(conn, now))
    issues.extend(_outcome_lag_issues(conn, now))
    issues.extend(_rule_draft_issues(conn, max_rule_draft_backlog))
    if records is not None:
        quality = audit_rule_quality(records)
        for issue in quality["issues"]:
            if issue["severity"] == "blocker":
                issues.append({
                    "severity": "blocker",
                    "component": "rule_quality_gate",
                    "title": f"Rule Quality blocker: {issue['rule_id']} {issue['check']}",
                    "detail": issue["message"],
                    "payload": issue,
                })

    blocker_count = sum(1 for issue in issues if issue["severity"] == "blocker")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    status = "ready" if blocker_count == 0 else "blocked"
    summary = {
        "status": status,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "max_job_freshness_hours": max_job_freshness_hours,
        "max_rule_draft_backlog": max_rule_draft_backlog,
    }
    snapshot_id = readiness_snapshot_id(checked_at)
    conn.execute(
        """
        INSERT OR REPLACE INTO operational_readiness_snapshots (
          snapshot_id, checked_at, status, blocker_count, warning_count,
          summary_json, issues_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            checked_at,
            status,
            blocker_count,
            warning_count,
            json_dumps(summary),
            json_dumps(issues),
        ),
    )
    record_health_check(
        conn,
        component="operational_readiness",
        status="ok" if status == "ready" else "failed",
        detail=f"{blocker_count} blockers, {warning_count} warnings",
        payload=summary,
        checked_at=checked_at,
    )
    if status == "ready":
        resolve_incident(conn, component="operational_readiness", title="Operational readiness blocked")
    else:
        open_incident(
            conn,
            component="operational_readiness",
            title="Operational readiness blocked",
            severity="critical",
            detail=f"{blocker_count} blockers before production operation",
            payload={"issues": issues[:20], "summary": summary},
            seen_at=checked_at,
        )
    record_audit_event(
        conn,
        actor_type="system",
        actor_id="operational_readiness",
        action="readiness_evaluated",
        entity_type="operational_readiness_snapshot",
        entity_id=snapshot_id,
        source="operational_readiness_gate",
        payload=summary,
        occurred_at=checked_at,
    )
    return {**summary, "snapshot_id": snapshot_id, "issues": issues}


def latest_readiness_snapshot(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM operational_readiness_snapshots
        ORDER BY checked_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["summary"] = json_loads(data.pop("summary_json"), {})
    data["issues"] = json_loads(data.pop("issues_json"), [])
    return data


def _incident_issues(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT client_id, severity, component, title, detail
        FROM system_incidents
        WHERE status = 'open'
          AND severity IN ('critical', 'high')
          AND component NOT IN ('operational_readiness', 'job:production_ops_check')
        """
    ).fetchall()
    return [
        {
            "severity": "blocker" if row["severity"] == "critical" else "warning",
            "client_id": row["client_id"],
            "component": row["component"],
            "title": row["title"],
            "detail": row["detail"],
        }
        for row in rows
    ]


def _job_freshness_issues(conn, now: datetime, max_hours: float) -> list[dict[str, Any]]:
    clients = conn.execute("SELECT client_id FROM clients WHERE status = 'active'").fetchall()
    issues = []
    for row in clients:
        client_id = row["client_id"]
        latest = conn.execute(
            """
            SELECT finished_at FROM job_runs
            WHERE client_id = ? AND status = 'success'
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (client_id,),
        ).fetchone()
        if not latest:
            issues.append({
                "severity": "blocker",
                "client_id": client_id,
                "component": "job_freshness",
                "title": "No successful daily job",
                "detail": "client has no successful job run",
            })
            continue
        finished = _parse_dt(latest["finished_at"])
        age_hours = ((now - finished).total_seconds() / 3600) if finished else None
        if age_hours is None or age_hours > max_hours:
            issues.append({
                "severity": "blocker",
                "client_id": client_id,
                "component": "job_freshness",
                "title": "Stale successful job",
                "detail": f"last success age={round(age_hours or 0, 1)}h",
            })
    return issues


def _connection_issues(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT client_id, provider, connection_type, status, last_error
        FROM client_connections
        WHERE required = 1
          AND status NOT IN ('ok', 'success', 'connected', 'active', 'healthy')
        """
    ).fetchall()
    return [
        {
            "severity": "blocker",
            "client_id": row["client_id"],
            "component": "connection",
            "title": f"{row['provider']} {row['connection_type']} not connected",
            "detail": row["last_error"] or row["status"],
        }
        for row in rows
    ]


def _secret_issues(conn, now: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT client_id, provider, env_name, status, rotation_due_at
        FROM secret_references
        WHERE required = 1
          AND status NOT IN ('referenced', 'verified', 'ok', 'active')
        """
    ).fetchall()
    issues = [
        {
            "severity": "blocker",
            "client_id": row["client_id"],
            "component": "secret",
            "title": f"{row['provider']} secret not ready",
            "detail": f"{row['env_name']} status={row['status']}",
        }
        for row in rows
    ]
    due_rows = conn.execute(
        """
        SELECT client_id, provider, env_name, rotation_due_at
        FROM secret_references
        WHERE required = 1 AND rotation_due_at IS NOT NULL
        """
    ).fetchall()
    for row in due_rows:
        due = _parse_dt(row["rotation_due_at"])
        if due and due <= now + timedelta(days=7):
            issues.append({
                "severity": "warning",
                "client_id": row["client_id"],
                "component": "secret_rotation",
                "title": f"{row['provider']} secret rotation due",
                "detail": f"{row['env_name']} due at {row['rotation_due_at']}",
            })
    return issues


def _stale_case_issues(conn, now: datetime, stale_days: int = 7) -> list[dict[str, Any]]:
    """Surface cases that are stuck before outcome measurement."""
    rows = conn.execute(
        """
        SELECT case_id, client_id, rule_id, title, status, severity, updated_at
        FROM operational_cases
        WHERE status IN ('detected', 'notified', 'acknowledged', 'open',
                         'waiting_client', 'waiting_zynect', 'planned')
        """
    ).fetchall()
    issues = []
    for row in rows:
        updated = _parse_dt(row["updated_at"])
        age_days = (now - updated).days if updated else 0
        if age_days < stale_days:
            continue
        severity = "blocker" if row["severity"] == "critical" else "warning"
        issues.append({
            "severity": severity,
            "client_id": row["client_id"],
            "component": "case_staleness",
            "title": f"Stale case: {row['rule_id']} {row['status']}",
            "detail": f"{row['title']} has not progressed for {age_days} days",
            "payload": {
                "case_id": row["case_id"],
                "rule_id": row["rule_id"],
                "case_status": row["status"],
                "age_days": age_days,
            },
        })
    return issues


def _outcome_lag_issues(conn, now: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT case_id, client_id, metric, baseline_start
        FROM outcome_measurements
        WHERE measured_value IS NULL
          AND baseline_value IS NOT NULL
          AND baseline_start IS NOT NULL
        """
    ).fetchall()
    issues = []
    for row in rows:
        start = _parse_date(row["baseline_start"])
        if not start:
            continue
        age_days = (now.date() - start).days
        if age_days < 28:
            continue
        measurement_end = (start + timedelta(days=28)).isoformat()
        measured = conn.execute(
            """
            SELECT 1 FROM outcome_measurements
            WHERE case_id = ? AND metric = ? AND measurement_end = ? AND measured_value IS NOT NULL
            """,
            (row["case_id"], row["metric"], measurement_end),
        ).fetchone()
        if not measured:
            issues.append({
                "severity": "warning",
                "client_id": row["client_id"],
                "component": "outcome_tracker",
                "title": "28d outcome not measured",
                "detail": f"case={row['case_id']} metric={row['metric']}",
            })
    return issues


def _rule_draft_issues(conn, max_backlog: int) -> list[dict[str, Any]]:
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM rule_change_drafts WHERE status = 'review_required'"
    ).fetchone()["n"]
    if count <= max_backlog:
        return []
    return [{
        "severity": "warning",
        "component": "rule_change_drafts",
        "title": "Rule review backlog is high",
        "detail": f"{count} review_required drafts > limit {max_backlog}",
    }]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None
