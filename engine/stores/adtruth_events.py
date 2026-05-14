"""AdTruth behavioral event evidence store."""
from __future__ import annotations

import hashlib
from typing import Any

from engine.adtruth_behavioral_signals import score_adtruth_event
from engine.stores.db import json_dumps, json_loads


def adtruth_event_id(client_id: str, normalized: dict[str, Any]) -> str:
    raw = "|".join(
        str(normalized.get(k) or "")
        for k in (
            "session_id",
            "visitor_id",
            "event_name",
            "event_at",
            "campaign_id",
            "adset_id",
            "ad_id",
            "placement",
        )
    )
    return hashlib.sha256(f"{client_id}|{raw}".encode("utf-8")).hexdigest()[:32]


def record_adtruth_event(conn, *, client_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Normalize, score, and persist one AdTruth event.

    The row is evidence, not an automatic blocking decision. Downstream
    fraud/CV-preservation logic can aggregate these rows by campaign/adset/ad
    and require human review before suppressing traffic.
    """
    scored = score_adtruth_event(event)
    normalized = scored["normalized_event"]
    event_id = adtruth_event_id(client_id, normalized)
    conn.execute(
        """
        INSERT INTO adtruth_events (
          event_id, client_id, session_id, visitor_id, event_name, event_at,
          source, medium, campaign, campaign_id, adset_id, ad_id, placement,
          gclid, fbclid, fraud_probability, fraud_band, signals_json, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          fraud_probability=excluded.fraud_probability,
          fraud_band=excluded.fraud_band,
          signals_json=excluded.signals_json,
          payload_json=excluded.payload_json
        """,
        (
            event_id,
            client_id,
            normalized.get("session_id"),
            normalized.get("visitor_id"),
            normalized.get("event_name"),
            normalized.get("event_at"),
            normalized.get("source"),
            normalized.get("medium"),
            normalized.get("campaign"),
            normalized.get("campaign_id"),
            normalized.get("adset_id"),
            normalized.get("ad_id"),
            normalized.get("placement"),
            normalized.get("gclid"),
            normalized.get("fbclid"),
            scored["fraud_probability"],
            scored["fraud_band"],
            json_dumps(scored.get("signals") or []),
            json_dumps({"raw": event, "normalized": normalized}),
        ),
    )
    return get_adtruth_event(conn, event_id) or {"event_id": event_id}


def get_adtruth_event(conn, event_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM adtruth_events WHERE event_id = ?", (event_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["signals"] = json_loads(data.pop("signals_json"), [])
    data["payload"] = json_loads(data.pop("payload_json"), {})
    return data


def adtruth_band_summary(conn, *, client_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT fraud_band, COUNT(*) AS n, AVG(fraud_probability) AS avg_probability
        FROM adtruth_events
        WHERE client_id = ?
        GROUP BY fraud_band
        ORDER BY fraud_band
        """,
        (client_id,),
    ).fetchall()
    return {
        "client_id": client_id,
        "bands": [dict(row) for row in rows],
        "total": sum(int(row["n"] or 0) for row in rows),
    }
