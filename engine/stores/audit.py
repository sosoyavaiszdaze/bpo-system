"""Append-only audit trail for operational governance."""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.db import json_dumps, utc_now


def audit_id_for(action: str, entity_type: str, entity_id: str | None, occurred_at: str, actor_id: str | None) -> str:
    raw = f"{action}|{entity_type}|{entity_id or ''}|{occurred_at}|{actor_id or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_audit_event(
    conn,
    *,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_id: str | None = None,
    client_id: str | None = None,
    source: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> str:
    """Record a governance event.

    The audit log is intentionally append-only from the store API's point of
    view. It is used for questions like "who changed the rule?", "what did the
    API prove?", and "why did the system move this case forward?".
    """
    occurred_at = occurred_at or utc_now()
    audit_id = audit_id_for(action, entity_type, entity_id, occurred_at, actor_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO audit_log (
          audit_id, actor_type, actor_id, action, entity_type, entity_id,
          client_id, source, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            actor_type,
            actor_id,
            action,
            entity_type,
            entity_id,
            client_id,
            source,
            occurred_at,
            json_dumps(payload or {}),
        ),
    )
    return audit_id
