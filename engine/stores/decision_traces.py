"""Decision trace store for explaining why rules did or did not surface."""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.db import json_dumps, json_loads, utc_now


def trace_id_for(client_id: str, rule_id: str, evaluation_date: str, stage: str) -> str:
    raw = f"{client_id}|{rule_id}|{evaluation_date}|{stage}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_trace(
    conn,
    *,
    client_id: str,
    rule_id: str,
    evaluation_date: str,
    stage: str,
    status: str,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> str:
    trace_id = trace_id_for(client_id, rule_id, evaluation_date, stage)
    conn.execute(
        """
        INSERT INTO decision_traces (
          trace_id, client_id, rule_id, evaluation_date, stage, status, reason, evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trace_id) DO UPDATE SET
          status=excluded.status,
          reason=excluded.reason,
          evidence_json=excluded.evidence_json,
          created_at=excluded.created_at
        """,
        (
            trace_id,
            client_id,
            rule_id,
            evaluation_date,
            stage,
            status,
            reason,
            json_dumps(evidence or {}),
            utc_now(),
        ),
    )
    return trace_id


def list_traces(conn, client_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if client_id:
        rows = conn.execute(
            """
            SELECT * FROM decision_traces
            WHERE client_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (client_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decision_traces ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row(row) for row in rows]


def trace_summary(conn, client_id: str | None = None) -> dict[str, Any]:
    if client_id:
        rows = conn.execute(
            """
            SELECT stage, status, COUNT(*) AS n
            FROM decision_traces
            WHERE client_id = ?
            GROUP BY stage, status
            ORDER BY stage, status
            """,
            (client_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT stage, status, COUNT(*) AS n
            FROM decision_traces
            GROUP BY stage, status
            ORDER BY stage, status
            """
        ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        out.setdefault(row["stage"], {})[row["status"]] = row["n"]
    return out


def _row(row) -> dict[str, Any]:
    data = dict(row)
    data["evidence"] = json_loads(data.pop("evidence_json"), {})
    return data
