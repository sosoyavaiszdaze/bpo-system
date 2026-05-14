"""Self-monitoring and incident store."""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.db import json_dumps, json_loads, utc_now


def incident_id_for(client_id: str | None, component: str, title: str) -> str:
    raw = f"{client_id or ''}|{component}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def health_check_id_for(client_id: str | None, component: str, checked_at: str) -> str:
    raw = f"{client_id or ''}|{component}|{checked_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_health_check(
    conn,
    *,
    component: str,
    status: str,
    client_id: str | None = None,
    latency_ms: float | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
    checked_at: str | None = None,
) -> str:
    checked_at = checked_at or utc_now()
    health_check_id = health_check_id_for(client_id, component, checked_at)
    conn.execute(
        """
        INSERT OR REPLACE INTO health_checks (
          health_check_id, client_id, component, status, checked_at,
          latency_ms, detail, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            health_check_id,
            client_id,
            component,
            status,
            checked_at,
            latency_ms,
            detail,
            json_dumps(payload or {}),
        ),
    )
    return health_check_id


def open_incident(
    conn,
    *,
    component: str,
    title: str,
    severity: str = "high",
    client_id: str | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
    seen_at: str | None = None,
) -> str:
    seen_at = seen_at or utc_now()
    incident_id = incident_id_for(client_id, component, title)
    conn.execute(
        """
        INSERT INTO system_incidents (
          incident_id, client_id, severity, status, component, title, detail,
          first_seen_at, last_seen_at, payload_json
        ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(incident_id) DO UPDATE SET
          severity=excluded.severity,
          status='open',
          detail=excluded.detail,
          last_seen_at=excluded.last_seen_at,
          resolved_at=NULL,
          payload_json=excluded.payload_json
        ON CONFLICT(client_id, component, title, status) DO UPDATE SET
          severity=excluded.severity,
          detail=excluded.detail,
          last_seen_at=excluded.last_seen_at,
          payload_json=excluded.payload_json
        """,
        (
            incident_id,
            client_id,
            severity,
            component,
            title,
            detail,
            seen_at,
            seen_at,
            json_dumps(payload or {}),
        ),
    )
    return incident_id


def resolve_incident(
    conn,
    *,
    component: str,
    title: str,
    client_id: str | None = None,
    resolved_at: str | None = None,
) -> int:
    resolved_at = resolved_at or utc_now()
    cur = conn.execute(
        """
        UPDATE system_incidents
        SET status = 'resolved', resolved_at = ?, last_seen_at = ?
        WHERE component = ? AND title = ? AND IFNULL(client_id, '') = IFNULL(?, '') AND status = 'open'
        """,
        (resolved_at, resolved_at, component, title, client_id),
    )
    return cur.rowcount


def record_job_health(conn, *, job_name: str, client_id: str | None, status: str, errors: list | None = None, metrics: dict | None = None) -> None:
    """Mirror job status into health/incident tables."""
    component = f"job:{job_name}"
    errors = errors or []
    metrics = metrics or {}
    record_health_check(
        conn,
        component=component,
        client_id=client_id,
        status="ok" if status == "success" else "failed",
        detail="; ".join(str(e) for e in errors[:3]) if errors else None,
        payload={"job_status": status, "errors": errors, "metrics": metrics},
    )
    title = f"{job_name} failed"
    if status == "success":
        resolve_incident(conn, component=component, client_id=client_id, title=title)
    else:
        open_incident(
            conn,
            component=component,
            client_id=client_id,
            title=title,
            severity="critical" if status == "failed" else "high",
            detail="; ".join(str(e) for e in errors[:5]) if errors else status,
            payload={"job_status": status, "errors": errors, "metrics": metrics},
        )


def incident_summary(conn, client_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if client_id:
        where = "WHERE client_id = ?"
        params.append(client_id)
    rows = conn.execute(
        f"SELECT status, severity, COUNT(*) AS n FROM system_incidents {where} GROUP BY status, severity",
        params,
    ).fetchall()
    counts = {}
    open_count = 0
    for row in rows:
        key = f"{row['status']}:{row['severity']}"
        counts[key] = row["n"]
        if row["status"] == "open":
            open_count += row["n"]
    return {"open_incidents": open_count, "counts": counts}


def list_open_incidents(conn, client_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE status = 'open'"
    if client_id:
        where += " AND client_id = ?"
        params.append(client_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM system_incidents
        {where}
        ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                 last_seen_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json"), {})
        out.append(data)
    return out
