"""Execution tracking for Operational Cases.

Client replies are intent signals. This store records the stronger signal:
an action was reported, verified, or rejected with evidence. The table is the
Track layer input for rule learning.
"""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.db import json_dumps, json_loads, row_to_dict, utc_now


def execution_id_for(case_id: str, status: str, source: str, actor_id: str = "") -> str:
    raw = f"{case_id}|{status}|{source}|{actor_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_case_execution(
    conn,
    *,
    case_id: str,
    client_id: str,
    rule_id: str,
    execution_status: str,
    evidence_source: str,
    evidence_quality: str = "low",
    actor_type: str | None = None,
    actor_id: str | None = None,
    executed_at: str | None = None,
    verified_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert one execution evidence row."""
    actor_id = actor_id or ""
    executed_at = executed_at or verified_at or utc_now()
    execution_id = execution_id_for(case_id, execution_status, evidence_source, actor_id)
    conn.execute(
        """
        INSERT INTO case_executions (
          execution_id, case_id, client_id, rule_id, execution_status,
          evidence_source, evidence_quality, actor_type, actor_id,
          executed_at, verified_at, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(case_id, execution_status, evidence_source, actor_id) DO UPDATE SET
          rule_id=excluded.rule_id,
          evidence_quality=excluded.evidence_quality,
          actor_type=excluded.actor_type,
          executed_at=excluded.executed_at,
          verified_at=excluded.verified_at,
          payload_json=excluded.payload_json,
          updated_at=datetime('now')
        """,
        (
            execution_id,
            case_id,
            client_id,
            rule_id,
            execution_status,
            evidence_source,
            evidence_quality,
            actor_type,
            actor_id,
            executed_at,
            verified_at,
            json_dumps(payload or {}),
        ),
    )
    return get_case_execution(conn, execution_id) or {"execution_id": execution_id}


def get_case_execution(conn, execution_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM case_executions WHERE execution_id = ?",
        (execution_id,),
    ).fetchone()
    data = row_to_dict(row)
    if data:
        data["payload"] = json_loads(data.pop("payload_json"), {})
    return data


def list_case_executions(conn, case_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if case_id:
        where = "WHERE case_id = ?"
        params.append(case_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM case_executions
        {where}
        ORDER BY COALESCE(verified_at, executed_at, created_at) DESC
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
