"""Client profile store for ADR-018."""
from __future__ import annotations

from engine.stores.db import json_dumps, utc_now


def upsert_client(conn, client_id: str, config: dict) -> None:
    display_name = config.get("display_name") or config.get("name") or client_id
    chatwork_room_id = (config.get("chatwork_rooms") or {}).get("main")
    tech = config.get("tech_stack") or {}
    now = utc_now()
    conn.execute(
        """
        INSERT INTO clients (
          client_id, display_name, vertical, ec_platform, status,
          chatwork_room_id, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET
          display_name=excluded.display_name,
          vertical=excluded.vertical,
          ec_platform=excluded.ec_platform,
          status=excluded.status,
          chatwork_room_id=excluded.chatwork_room_id,
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            client_id,
            display_name,
            config.get("vertical") or config.get("industry"),
            config.get("ec_platform") or tech.get("ec_platform"),
            config.get("status") or "active",
            str(chatwork_room_id) if chatwork_room_id else None,
            json_dumps(config),
            now,
            now,
        ),
    )


def list_client_ids(conn) -> list[str]:
    rows = conn.execute("SELECT client_id FROM clients ORDER BY client_id").fetchall()
    return [row["client_id"] for row in rows]
