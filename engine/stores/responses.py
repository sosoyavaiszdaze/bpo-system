"""Client response DB store.

This bridges legacy ChatWork YAML responses and future UI/API responses into
Operational Case state transitions.
"""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.cases import apply_client_response_to_case
from engine.stores.db import json_dumps


def response_id_for(client_id: str, rule_id: str, source_id: str) -> str:
    raw = f"{client_id}|{rule_id}|{source_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_client_response(
    conn,
    *,
    client_id: str,
    record: dict[str, Any],
    case_id: str | None = None,
) -> str:
    """Persist one response and update its case state when possible."""
    rule_id = str(record.get("rule_id") or "")
    if not rule_id:
        raise ValueError("record.rule_id is required")
    if case_id is None:
        case_id = latest_case_id_for_rule(conn, client_id, rule_id)
    source_id = str(record.get("chatwork_message_id") or record.get("answered_at") or record.get("source") or "")
    response_id = response_id_for(client_id, rule_id, source_id)
    conn.execute(
        """
        INSERT OR REPLACE INTO client_responses (
          response_id, client_id, rule_id, case_id, answer_code, answer_label,
          status, raw_message, chatwork_message_id, answered_at, source, expires_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            response_id,
            client_id,
            rule_id,
            case_id,
            record.get("answer_code"),
            record.get("answer_label"),
            record.get("status") or "unknown",
            record.get("raw_message"),
            str(record.get("chatwork_message_id")) if record.get("chatwork_message_id") else None,
            record.get("answered_at"),
            record.get("source") or "chatwork_reply",
            record.get("expires_at"),
            json_dumps(record),
        ),
    )
    if case_id:
        apply_client_response_to_case(conn, case_id=case_id, response=record)
    return response_id


def latest_case_id_for_rule(conn, client_id: str, rule_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT case_id FROM operational_cases
        WHERE client_id = ? AND rule_id = ?
        ORDER BY first_detected_at DESC
        LIMIT 1
        """,
        (client_id, rule_id),
    ).fetchone()
    return row["case_id"] if row else None
