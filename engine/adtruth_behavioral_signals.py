"""AdTruth-style behavioral signal scoring.

This mirrors the useful parts of papa-torb/adtruth's browser SDK data shape
without taking it as a runtime dependency. The input is a page/session event
with UTM/click IDs, fingerprint fields, and behavior metrics. The output is a
deterministic fraud score that can feed Zynect's AdTruth samples.
"""
from __future__ import annotations

from typing import Any


def normalize_adtruth_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize SDK-style payloads into a compact session evidence object."""
    behavior = event.get("behavior") or event.get("behavior_metrics") or {}
    click_ids = event.get("click_ids") or {}
    campaign = event.get("campaign") or {}
    return {
        "session_id": event.get("session_id"),
        "visitor_id": event.get("visitor_id"),
        "event_name": event.get("event_name") or event.get("event") or "page_view",
        "event_at": event.get("event_at") or event.get("timestamp") or event.get("created_at"),
        "source": campaign.get("source") or event.get("utm_source") or event.get("source"),
        "medium": campaign.get("medium") or event.get("utm_medium") or event.get("medium"),
        "campaign": campaign.get("campaign") or event.get("utm_campaign") or event.get("campaign"),
        "campaign_id": campaign.get("campaign_id") or event.get("campaign_id"),
        "adset_id": campaign.get("adset_id") or event.get("adset_id"),
        "ad_id": campaign.get("ad_id") or event.get("ad_id"),
        "placement": campaign.get("placement") or event.get("placement"),
        "gclid": click_ids.get("gclid") or event.get("gclid"),
        "fbclid": click_ids.get("fbclid") or event.get("fbclid"),
        "fingerprint": event.get("fingerprint"),
        "canvas_fingerprint": event.get("canvas_fingerprint"),
        "time_on_page": _num(behavior.get("timeOnPage") or behavior.get("time_on_page")),
        "scroll_depth": _num(behavior.get("scrollDepth") or behavior.get("scroll_depth")),
        "click_count": _num(behavior.get("clickCount") or behavior.get("click_count")),
        "mouse_moves": _num(behavior.get("mouseMoves") or behavior.get("mouse_moves")),
        "avg_click_interval_ms": _num(
            behavior.get("averageClickInterval")
            or behavior.get("avg_click_interval_ms")
        ),
        "time_to_first_click_ms": _num(
            behavior.get("timeToFirstClick")
            or behavior.get("time_to_first_click_ms")
        ),
        "input_inconsistency": bool(
            event.get("inputInconsistency")
            or (event.get("fingerprint_details") or {}).get("inputInconsistency")
        ),
    }


def score_adtruth_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic fraud probability and signal breakdown.

    The score is intentionally conservative. It should trigger investigation
    and rule prioritization, not automatic blocking by itself.
    """
    e = normalize_adtruth_event(event)
    signals: dict[str, float] = {}

    if e["time_on_page"] is not None and e["time_on_page"] < 3:
        signals["instant_bounce"] = 0.22
    if e["scroll_depth"] is not None and e["scroll_depth"] == 0 and (e["time_on_page"] or 0) < 8:
        signals["no_scroll_short_session"] = 0.16
    if e["time_to_first_click_ms"] is not None and e["time_to_first_click_ms"] < 200:
        signals["instant_reaction"] = 0.20
    if e["avg_click_interval_ms"] is not None and e["avg_click_interval_ms"] < 120 and (e["click_count"] or 0) >= 3:
        signals["machine_like_click_interval"] = 0.22
    if e["click_count"] is not None and e["click_count"] >= 20 and (e["time_on_page"] or 1) <= 10:
        signals["rapid_fire_clicks"] = 0.25
    if e["mouse_moves"] is not None and e["mouse_moves"] == 0 and (e["click_count"] or 0) > 0:
        signals["clicks_without_pointer_movement"] = 0.18
    if e["input_inconsistency"]:
        signals["input_inconsistency"] = 0.12
    if not (e["gclid"] or e["fbclid"]) and e["medium"] in {"cpc", "paid", "paid_social"}:
        signals["paid_session_without_click_id"] = 0.12

    probability = min(1.0, 0.08 + sum(signals.values()))
    if len(signals) >= 3:
        probability = min(1.0, probability + 0.10)

    if probability >= 0.75:
        band = "black"
    elif probability >= 0.55:
        band = "gray"
    else:
        band = "clean"

    return {
        "fraud_probability": round(probability, 4),
        "fraud_band": band,
        "signals": signals,
        "normalized_event": e,
        "source": "adtruth_behavioral_signals",
    }


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
