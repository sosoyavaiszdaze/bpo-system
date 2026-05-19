"""Operational case store for ADR-018."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from engine.stores.db import json_dumps, json_loads, row_to_dict, utc_now

ACTIVE_STATUSES = {
    "detected", "notified", "acknowledged", "open",
    "waiting_client", "waiting_zynect", "planned",
    "executed", "implemented", "verified", "measuring", "monitoring",
    "learned",
}
TERMINAL_STATUSES = {"closed"}
ALLOWED_TRANSITIONS = {
    "detected": {"notified", "acknowledged", "planned", "waiting_client", "waiting_zynect", "executed", "verified", "measuring", "closed"},
    "notified": {"acknowledged", "planned", "waiting_client", "waiting_zynect", "executed", "verified", "measuring", "closed"},
    "acknowledged": {"planned", "executed", "verified", "measuring", "waiting_client", "waiting_zynect", "closed"},
    "open": {"waiting_client", "waiting_zynect", "planned", "executed", "implemented", "verified", "measuring", "monitoring", "resolved", "closed"},
    "waiting_client": {"waiting_zynect", "planned", "executed", "implemented", "verified", "measuring", "monitoring", "resolved", "closed", "open"},
    "waiting_zynect": {"waiting_client", "planned", "executed", "implemented", "verified", "measuring", "monitoring", "resolved", "closed", "open"},
    "planned": {"executed", "implemented", "verified", "measuring", "monitoring", "waiting_client", "waiting_zynect", "closed"},
    "executed": {"verified", "measuring", "monitoring", "waiting_client", "waiting_zynect", "closed"},
    "implemented": {"verified", "measuring", "monitoring", "resolved", "waiting_client", "waiting_zynect", "closed"},
    "verified": {"measuring", "monitoring", "resolved", "closed"},
    "measuring": {"learned", "resolved", "closed"},
    "monitoring": {"measuring", "learned", "resolved", "implemented", "waiting_client", "waiting_zynect", "closed"},
    "learned": {"resolved", "closed", "monitoring"},
    "resolved": {"closed", "monitoring", "learned"},
    "closed": set(),
}
CANONICAL_CASE_FLOW = (
    "detected", "notified", "acknowledged", "planned", "executed",
    "verified", "measuring", "learned", "closed",
)
LEGACY_STATUS_TO_CANONICAL = {
    "open": "detected",
    "waiting_client": "acknowledged",
    "waiting_zynect": "acknowledged",
    "implemented": "executed",
    "monitoring": "measuring",
    "resolved": "learned",
    "closed": "closed",
}
CASE_STATUS_FROM_INDICATION = {
    "open": "open",
    "resolved_pending": "monitoring",
    "resolved_confirmed": "resolved",
    "archived": "resolved",
}
CASE_STATUS_FROM_RESPONSE = {
    "confirmed_done": "executed",
    "not_done": "waiting_client",
    "wants_help": "waiting_zynect",
    "not_applicable": "closed",
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


def transition_case(
    conn,
    *,
    case_id: str,
    to_status: str,
    actor_type: str,
    reason: str | None = None,
    actor_id: str = "",
    transitioned_at: str | None = None,
    payload: dict | None = None,
    allow_any: bool = False,
) -> str:
    """Move an operational case through an explicit state transition."""
    row = conn.execute(
        "SELECT client_id, status FROM operational_cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"case not found: {case_id}")
    from_status = row["status"]
    if from_status in TERMINAL_STATUSES and to_status != from_status:
        raise ValueError(f"terminal case cannot transition: {from_status} -> {to_status}")
    if not allow_any and to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"invalid case transition: {from_status} -> {to_status}")

    transitioned_at = transitioned_at or utc_now()
    raw = f"{case_id}|{from_status}|{to_status}|{transitioned_at}|{actor_type}|{actor_id}"
    transition_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    conn.execute(
        """
        INSERT OR IGNORE INTO case_transitions (
          transition_id, case_id, client_id, from_status, to_status,
          actor_type, actor_id, reason, transitioned_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition_id,
            case_id,
            row["client_id"],
            from_status,
            to_status,
            actor_type,
            actor_id or None,
            reason,
            transitioned_at,
            json_dumps(payload or {}),
        ),
    )
    update_fields = ["status = ?", "updated_at = ?"]
    params: list[Any] = [to_status, transitioned_at]
    if to_status == "resolved":
        update_fields.extend(["resolved_at = ?", "resolved_date = ?"])
        params.extend([transitioned_at, transitioned_at[:10]])
    if to_status == "closed":
        update_fields.append("closed_at = ?")
        params.append(transitioned_at)
    params.append(case_id)
    conn.execute(
        f"UPDATE operational_cases SET {', '.join(update_fields)} WHERE case_id = ?",
        params,
    )
    add_case_event(
        conn,
        case_id=case_id,
        client_id=row["client_id"],
        event_type=f"transition:{from_status}->{to_status}",
        actor_type=actor_type,
        actor_id=actor_id,
        event_at=transitioned_at,
        message=reason,
        payload=payload or {},
    )
    return transition_id


def canonical_case_status(status: str | None) -> str:
    """Map legacy operational statuses onto the production case lifecycle."""
    if not status:
        return "detected"
    if status in CANONICAL_CASE_FLOW:
        return status
    return LEGACY_STATUS_TO_CANONICAL.get(status, status)


def apply_client_response_to_case(
    conn,
    *,
    case_id: str | None,
    response: dict,
    actor_type: str = "client",
) -> str | None:
    """Attach a client response to a case and move the case state.

    ChatWork/file ingestion and future UI/API ingestion should converge here so
    "the client answered" always has the same operational meaning.
    """
    if not case_id:
        return None
    row = conn.execute(
        "SELECT client_id, status FROM operational_cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if not row:
        return None

    answered_at = response.get("answered_at") or utc_now()
    actor_id = str(response.get("chatwork_message_id") or response.get("source") or "")
    add_case_event(
        conn,
        case_id=case_id,
        client_id=row["client_id"],
        event_type="client_response",
        actor_type=actor_type,
        actor_id=actor_id,
        event_at=answered_at,
        message=response.get("raw_message"),
        payload=response,
    )

    target_status = CASE_STATUS_FROM_RESPONSE.get(str(response.get("status") or ""))
    if response.get("status") == "confirmed_done":
        from engine.stores.executions import record_case_execution
        record_case_execution(
            conn,
            case_id=case_id,
            client_id=row["client_id"],
            rule_id=str(response.get("rule_id") or ""),
            execution_status="client_reported",
            evidence_source=response.get("source") or "chatwork_reply",
            evidence_quality="low",
            actor_type=actor_type,
            actor_id=str(response.get("chatwork_message_id") or ""),
            executed_at=answered_at,
            payload={
                "answer_code": response.get("answer_code"),
                "answer_label": response.get("answer_label"),
                "raw_message": response.get("raw_message"),
            },
        )
    if not target_status or target_status == row["status"]:
        return None
    return transition_case(
        conn,
        case_id=case_id,
        to_status=target_status,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=f"client_response:{response.get('status')}",
        transitioned_at=answered_at,
        payload={
            "rule_id": response.get("rule_id"),
            "answer_code": response.get("answer_code"),
            "answer_label": response.get("answer_label"),
            "source": response.get("source"),
        },
        allow_any=False,
    )


def apply_api_evidence_to_cases(
    conn,
    *,
    client_id: str,
    evidence_map: dict[str, dict],
    evidence_source: str = "meta_api",
    verified_at: str | None = None,
) -> dict[str, Any]:
    """Turn API-resolved evidence into execution tracking for active cases.

    This is the automatic Track bridge: if an API/validator proves a previously
    open rule is already fixed, we record a high-quality execution evidence row
    and move the case to measuring so Outcome Tracker can judge the result.
    """
    if not evidence_map:
        return {"cases_checked": 0, "executions_recorded": 0, "transitions": 0}

    active_rows = conn.execute(
        f"""
        SELECT case_id, client_id, rule_id, status
        FROM operational_cases
        WHERE client_id = ?
          AND status IN ({",".join("?" for _ in ACTIVE_STATUSES)})
        """,
        (client_id, *sorted(ACTIVE_STATUSES)),
    ).fetchall()

    from engine.stores.executions import record_case_execution

    executions = 0
    transitions = 0
    verified_at = verified_at or utc_now()
    for row in active_rows:
        rid = row["rule_id"]
        evidence = evidence_map.get(rid) or {}
        if evidence.get("status") != "resolved":
            continue
        source = evidence.get("source") or evidence_source
        record_case_execution(
            conn,
            case_id=row["case_id"],
            client_id=row["client_id"],
            rule_id=rid,
            execution_status="verified",
            evidence_source=source,
            evidence_quality="high" if str(source).startswith("meta_api") else "medium",
            actor_type="system",
            actor_id=evidence_source,
            verified_at=verified_at,
            payload={
                "reason": evidence.get("reason"),
                "value": evidence.get("value") or {},
                "rule_group": evidence.get("rule_group"),
            },
        )
        executions += 1
        if row["status"] != "measuring":
            transition_case(
                conn,
                case_id=row["case_id"],
                to_status="measuring",
                actor_type="system",
                actor_id=evidence_source,
                reason=f"api_evidence_resolved:{rid}",
                transitioned_at=verified_at,
                payload={
                    "rule_id": rid,
                    "source": source,
                    "reason": evidence.get("reason"),
                    "rule_group": evidence.get("rule_group"),
                },
                allow_any=False,
            )
            transitions += 1
    return {
        "cases_checked": len(active_rows),
        "executions_recorded": executions,
        "transitions": transitions,
    }


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
