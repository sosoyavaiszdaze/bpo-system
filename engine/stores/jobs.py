"""Job run store and client health summaries (ADR-018)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from engine.stores.cases import list_stale_cases, summarize_client_cases
from engine.stores.db import json_dumps, json_loads, utc_now


def new_job_run_id(job_name: str, client_id: str | None, started_at: str | None = None) -> str:
    started_at = started_at or utc_now()
    raw = f"{job_name}|{client_id or ''}|{started_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def start_job(conn, job_name: str, client_id: str | None = None, scheduled_at: str | None = None) -> str:
    started_at = utc_now()
    job_run_id = new_job_run_id(job_name, client_id, started_at)
    conn.execute(
        """
        INSERT INTO job_runs (
          job_run_id, job_name, client_id, scheduled_at, started_at, status, error_json, metrics_json
        ) VALUES (?, ?, ?, ?, ?, 'running', '[]', '{}')
        """,
        (job_run_id, job_name, client_id, scheduled_at, started_at),
    )
    return job_run_id


def finish_job(
    conn,
    job_run_id: str,
    status: str,
    errors: Optional[list] = None,
    metrics: Optional[dict] = None,
    finished_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE job_runs
        SET status = ?, finished_at = ?, error_json = ?, metrics_json = ?
        WHERE job_run_id = ?
        """,
        (status, finished_at or utc_now(), json_dumps(errors or []), json_dumps(metrics or {}), job_run_id),
    )
    row = conn.execute("SELECT job_name, client_id FROM job_runs WHERE job_run_id = ?", (job_run_id,)).fetchone()
    if row:
        try:
            from engine.stores.monitoring import record_job_health
            record_job_health(conn, job_name=row["job_name"], client_id=row["client_id"], status=status, errors=errors, metrics=metrics)
        except Exception:
            pass


def record_job(
    conn,
    job_name: str,
    client_id: str | None,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    errors: Optional[list] = None,
    metrics: Optional[dict] = None,
) -> str:
    started_at = started_at or utc_now()
    job_run_id = new_job_run_id(job_name, client_id, started_at)
    conn.execute(
        """
        INSERT OR REPLACE INTO job_runs (
          job_run_id, job_name, client_id, started_at, finished_at, status, error_json, metrics_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_run_id,
            job_name,
            client_id,
            started_at,
            finished_at or started_at,
            status,
            json_dumps(errors or []),
            json_dumps(metrics or {}),
        ),
    )
    try:
        from engine.stores.monitoring import record_job_health
        record_job_health(conn, job_name=job_name, client_id=client_id, status=status, errors=errors, metrics=metrics)
    except Exception:
        pass
    return job_run_id


def latest_job(conn, client_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT * FROM job_runs
        WHERE client_id = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["errors"] = json_loads(data.pop("error_json"), [])
    data["metrics"] = json_loads(data.pop("metrics_json"), {})
    return data


def client_health(conn, client_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    case_summary = summarize_client_cases(conn, client_id)
    latest = latest_job(conn, client_id)
    stale_cases = list_stale_cases(conn, client_id, today=now)
    data_freshness_hours = None
    if latest and latest.get("finished_at"):
        dt = _parse_dt(latest["finished_at"])
        if dt:
            data_freshness_hours = round((now - dt).total_seconds() / 3600, 1)
    return {
        "client_id": client_id,
        "last_successful_run_at": _last_successful_run_at(conn, client_id),
        "latest_job": latest,
        "data_freshness_hours": data_freshness_hours,
        "stale_cases_count": len(stale_cases),
        "stale_case_ids": [c["case_id"] for c in stale_cases[:10]],
        **case_summary,
    }


def _last_successful_run_at(conn, client_id: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT finished_at FROM job_runs
        WHERE client_id = ? AND status = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    return row["finished_at"] if row else None


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None
