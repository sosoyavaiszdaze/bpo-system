"""Client connection and secret-reference store.

This is the operational source of truth for "what is connected for this
client?" and "which secret references exist?", without storing secret values.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from engine.stores.db import json_dumps, json_loads, utc_now
from engine.vertical_kpi_registry import build_client_kpi_readiness


def connection_id_for(client_id: str, provider: str, connection_type: str) -> str:
    raw = f"{client_id}|{provider}|{connection_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def secret_ref_id_for(client_id: str | None, provider: str, env_name: str, purpose: str) -> str:
    raw = f"{client_id or ''}|{provider}|{env_name}|{purpose}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_connection(
    conn,
    *,
    client_id: str,
    provider: str,
    connection_type: str,
    status: str,
    required: bool = False,
    strongly_recommended: bool = False,
    config_ref: str | None = None,
    last_error: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    now = utc_now()
    connection_id = connection_id_for(client_id, provider, connection_type)
    last_success_at = now if status in {"configured", "ok", "success"} else None
    conn.execute(
        """
        INSERT INTO client_connections (
          connection_id, client_id, provider, connection_type, status,
          required, strongly_recommended, last_checked_at, last_success_at,
          last_error, config_ref, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(client_id, provider, connection_type) DO UPDATE SET
          status=excluded.status,
          required=excluded.required,
          strongly_recommended=excluded.strongly_recommended,
          last_checked_at=excluded.last_checked_at,
          last_success_at=COALESCE(excluded.last_success_at, client_connections.last_success_at),
          last_error=excluded.last_error,
          config_ref=excluded.config_ref,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            connection_id,
            client_id,
            provider,
            connection_type,
            status,
            1 if required else 0,
            1 if strongly_recommended else 0,
            now,
            last_success_at,
            last_error,
            config_ref,
            json_dumps(payload or {}),
            now,
            now,
        ),
    )
    return connection_id


def upsert_secret_reference(
    conn,
    *,
    client_id: str | None,
    provider: str,
    env_name: str,
    purpose: str,
    required: bool = True,
    payload: dict[str, Any] | None = None,
) -> str:
    now = utc_now()
    status = "present" if os.environ.get(env_name) else "missing_env"
    secret_ref_id = secret_ref_id_for(client_id, provider, env_name, purpose)
    conn.execute(
        """
        INSERT INTO secret_references (
          secret_ref_id, client_id, provider, env_name, purpose, status,
          required, last_verified_at, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(client_id, provider, env_name, purpose) DO UPDATE SET
          status=excluded.status,
          required=excluded.required,
          last_verified_at=excluded.last_verified_at,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            secret_ref_id,
            client_id,
            provider,
            env_name,
            purpose,
            status,
            1 if required else 0,
            now,
            json_dumps(payload or {}),
            now,
            now,
        ),
    )
    return secret_ref_id


def sync_client_connections_from_config(conn, client_id: str, config: dict[str, Any]) -> dict[str, int]:
    """Populate connection and secret ledgers from clients.yaml shape."""
    readiness = build_client_kpi_readiness(client_id, config)
    connections = 0
    secrets = 0

    for source_id, row in readiness.get("source_status", {}).items():
        status = "configured" if row.get("configured") else "missing_config"
        upsert_connection(
            conn,
            client_id=client_id,
            provider=source_id,
            connection_type="data_source",
            status=status,
            required=bool(row.get("required")),
            strongly_recommended=bool(row.get("strongly_recommended")),
            config_ref=f"vertical_kpi_registry:{readiness.get('vertical_id')}:{source_id}",
            payload=row,
        )
        connections += 1

    for provider, env_name, purpose in _iter_secret_env_refs(config):
        upsert_secret_reference(
            conn,
            client_id=client_id,
            provider=provider,
            env_name=env_name,
            purpose=purpose,
        )
        secrets += 1

    return {"connections": connections, "secret_references": secrets}


def connection_summary(conn, client_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if client_id:
        where = "WHERE client_id = ?"
        params.append(client_id)
    rows = conn.execute(
        f"""
        SELECT status, required, strongly_recommended, COUNT(*) AS n
        FROM client_connections
        {where}
        GROUP BY status, required, strongly_recommended
        """
    , params).fetchall()
    by_status: dict[str, int] = {}
    missing_required = 0
    missing_recommended = 0
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
        if row["status"] != "configured" and row["required"]:
            missing_required += row["n"]
        if row["status"] != "configured" and row["strongly_recommended"]:
            missing_recommended += row["n"]
    secret_rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM secret_references {where} GROUP BY status",
        params,
    ).fetchall()
    secret_status = {row["status"]: row["n"] for row in secret_rows}
    return {
        "connection_status": by_status,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "secret_status": secret_status,
        "missing_secrets": secret_status.get("missing_env", 0),
    }


def list_client_connections(conn, client_id: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if client_id:
        where = "WHERE client_id = ?"
        params.append(client_id)
    rows = conn.execute(
        f"""
        SELECT * FROM client_connections
        {where}
        ORDER BY client_id, required DESC, strongly_recommended DESC, provider
        """,
        params,
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json"), {})
        out.append(data)
    return out


def _iter_secret_env_refs(config: dict[str, Any]):
    ads = config.get("ads") if isinstance(config.get("ads"), dict) else {}
    for provider, cfg in ads.items():
        if not isinstance(cfg, dict):
            continue
        for key, purpose in (
            ("access_token_env", "api_access_token"),
            ("developer_token_env", "developer_token"),
            ("refresh_token_env", "refresh_token"),
        ):
            if cfg.get(key):
                yield str(provider), str(cfg[key]), purpose
    for key, provider, purpose in (
        ("chatwork_bot_token_env", "chatwork", "bot_token"),
        ("chatwork_token_env", "chatwork", "api_token"),
    ):
        if config.get(key):
            yield provider, str(config[key]), purpose
    notifications = config.get("notifications") if isinstance(config.get("notifications"), dict) else {}
    for provider in ("slack", "lark"):
        cfg = notifications.get(provider)
        if isinstance(cfg, dict) and cfg.get("webhook_env"):
            yield provider, str(cfg["webhook_env"]), "webhook"
