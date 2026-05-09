"""Operational case store for ADR-018."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from engine.stores.db import json_dumps, json_loads, row_to_dict, utc_now

ACTIVE_STATUSES = {"open", "waiting_client", "waiting_zynect", "planned", "implemented", "monitoring"}
CASE_STATUS_FROM_INDICATION = {
    "open": "open",
    "resolved_pending": "monitoring",
    "resolved_confirmed": "resolved",
    "archived": "resolved",
}


def case_id_for_indication(record: dict) -> str:
    if record.get("indication_id"):
        return str(record["indication_id"])
    raw = "|".join(
        str(record.get(k, ""))
        for k in ("client_id", "rule_id", "platform", "target_id", "first_detected_date")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def event_id_for(case_id: str, event_type: str, event_at: str, actor_type: str, actor_id: str = "") -> str:
    raw = f"{case_id}|{event_type}|{event_at}|{actor_type}|{actor_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_case_from_indication(conn, record: dict) -> str:
    case_id = case_id_for_indication(record)
    payload = record.get("payload") or {}
    title = payload.get("completion_title") or payload.get("title") or record.get("rule_id") or case_id
    status = CASE_STATUS_FROM_INDICATION.get(record.get("status"), record.get("status") or "open")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO operational_cases (
          case_id, client_id, rule_id, title, status, severity, owner_type,
          first_detected_at, first_detected_date, last_detected_at, last_detected_date,
          notified_at, resolved_at, resolved_date, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
          title=excluded.title,
          status=excluded.status,
          severity=excluded.severity,
          last_detected_at=excluded.last_detected_at,
          last_detected_date=excluded.last_detected_date,
          notified_at=excluded.notified_at,
          resolved_at=excluded.resolved_at,
          resolved_date=excluded.resolved_date,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            case_id,
            record.get("client_id", ""),
            record.get("rule_id", ""),
            title,
            status,
            record.get("severity"),
            "client",
            record.get("first_detected_at") or now,
            record.get("first_detected_date"),
            record.get("last_detected_at"),
            record.get("last_detected_date"),
            record.get("notified_at"),
            record.get("resolved_at"),
            record.get("resolved_date"),
            json_dumps(payload),
            now,
            now,
        ),
    )
    for h in record.get("history") or []:
        add_case_event(
            conn,
            case_id=case_id,
            client_id=record.get("client_id", ""),
            event_type=str(h.get("event") or "history"),
            actor_type="system",
            event_at=str(h.get("at") or now),
            message=None,
            payload=h,
        )
    return case_id


def add_case_event(
    conn,
    case_id: str,
    client_id: str,
    event_type: str,
    actor_type: str,
    event_at: Optional[str] = None,
    message: Optional[str] = None,
    payload: Optional[dict] = None,
    actor_id: str = "",
) -> str:
    event_at = event_at or utc_now()
    event_id = event_id_for(case_id, event_type, event_at, actor_type, actor_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO case_events (
          event_id, case_id, client_id, event_type, actor_type, actor_id, event_at, message, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, case_id, client_id, event_type, actor_type, actor_id or None, event_at, message, json_dumps(payload or {})),
    )
    return event_id


def get_case(conn, case_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM operational_cases WHERE case_id = ?", (case_id,)).fetchone()
    data = row_to_dict(row)
    if data:
        data["payload"] = json_loads(data.pop("payload_json"), {})
    return data


def summarize_client_cases(conn, client_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM operational_cases WHERE client_id = ? GROUP BY status",
        (client_id,),
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {
        "open_cases_count": sum(counts.get(s, 0) for s in ACTIVE_STATUSES),
        "waiting_client_count": counts.get("waiting_client", 0),
        "waiting_zynect_count": counts.get("waiting_zynect", 0),
        "implemented_count": counts.get("implemented", 0),
        "monitoring_count": counts.get("monitoring", 0),
        "resolved_count": counts.get("resolved", 0),
        "status_counts": counts,
    }


def list_stale_cases(conn, client_id: str, today: Optional[datetime] = None, days: int = 14) -> list[dict]:
    today = today or datetime.now(timezone.utc)
    out = []
    rows = conn.execute(
        """
        SELECT * FROM operational_cases
        WHERE client_id = ? AND status IN ('open', 'waiting_client', 'waiting_zynect', 'planned')
        """,
        (client_id,),
    ).fetchall()
    for row in rows:
        data = dict(row)
        first = _parse_dt(data.get("first_detected_at"))
        age = (today - first).days if first else 0
        if age >= days:
            data["age_days"] = age
            out.append(data)
    return out


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
