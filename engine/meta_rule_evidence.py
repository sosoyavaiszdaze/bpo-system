"""Meta API evidence and rule grouping helpers.

This module is the bridge between raw Meta API payloads and rule operations.
It does not decide what to notify by itself. It normalizes which Meta checks
were available via API, which rules can be auto-resolved, and which rule IDs
are duplicates or siblings of the same underlying measurement issue.
"""
from __future__ import annotations

from typing import Any


MEASUREMENT_RULE_GROUPS: dict[str, list[str]] = {
    "meta_pixel_foundation": ["M01", "X-PI1", "F-MF-02"],
    "meta_capi_and_dedup": ["M02", "P-EF-02", "PC-MS-01", "M06"],
    "meta_domain_and_aem": ["M04", "F-AH-04", "M05", "M56"],
    "meta_attribution_window": ["M62", "F-MF-08"],
    "meta_cpa_spike_diagnosis": ["C05", "ANO_CPA_SPIKE", "M09", "M10", "M12", "M13", "M45", "M68"],
    "meta_delivery_drop_diagnosis": ["ANO_IMPRESSION_DROP", "M14", "M16", "M18", "M20", "M44"],
}


RULE_TO_GROUP = {
    rule_id: group
    for group, rules in MEASUREMENT_RULE_GROUPS.items()
    for rule_id in rules
}


def build_meta_connection_audit(meta_cfg: dict[str, Any] | None, meta_data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a capability-level audit for Meta API integration.

    The status vocabulary is intentionally small:
      - ok: API/config has enough evidence for automated checks.
      - missing_config: client config lacks required fields.
      - no_data: API path exists but returned no usable rows.
      - unknown: supported concept, but not connected yet.
    """
    meta_cfg = meta_cfg or {}
    meta_data = meta_data or {}
    pixel = meta_data.get("pixel_status") or {}
    campaigns = meta_data.get("campaigns") or []

    account_id = meta_cfg.get("account_id") or meta_data.get("account_id")
    has_token = bool(meta_cfg.get("access_token") or meta_cfg.get("access_token_env"))
    adset_count = sum(int(c.get("adset_count") or 0) for c in campaigns if isinstance(c, dict))

    return {
        "ad_account": {
            "status": "ok" if account_id and has_token else "missing_config",
            "account_id": account_id,
            "has_token_reference": has_token,
        },
        "campaign_insights": {
            "status": "ok" if campaigns else "no_data",
            "campaign_count": len(campaigns),
        },
        "adset_insights": {
            "status": "ok" if adset_count > 0 else ("no_data" if campaigns else "unknown"),
            "adset_count": adset_count,
        },
        "pixel": {
            "status": "ok" if pixel.get("pixel_installed") else "no_data",
            "pixel_count": pixel.get("pixel_count", 0),
            "primary_pixel_id": pixel.get("primary_pixel_id"),
            "last_fired_time": pixel.get("last_fired_time"),
        },
        "capi": {
            "status": "ok" if pixel.get("capi_enabled") or pixel.get("server_events") else "unknown",
            "event_match_quality": pixel.get("event_match_quality"),
            "server_events": bool(pixel.get("server_events")),
        },
        "domain_verification": {
            "status": "unknown",
            "reason": "business_id/domain verification API is not connected yet",
        },
        "account_quality": {
            "status": "unknown",
            "reason": "account_quality API is not connected yet",
        },
    }


def build_meta_rule_evidence(meta_data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build per-rule evidence from the Meta payload.

    A rule is only `resolved` when API evidence proves the setup is already OK.
    Problems or unknowns stay `manual_required` / `unknown` so the customer can
    still be asked. This avoids hiding true issues just because an API was called.
    """
    meta_data = meta_data or {}
    pixel = meta_data.get("pixel_status") or {}
    campaigns = meta_data.get("campaigns") or []
    totals = meta_data.get("totals") or _calc_totals_from_campaigns(campaigns)

    pixel_ok = bool(pixel.get("pixel_installed"))
    capi_ok = bool(pixel.get("capi_enabled") or pixel.get("server_events"))
    emq = _to_float(pixel.get("event_match_quality"))
    has_campaigns = bool(campaigns)
    has_conversions = float(totals.get("conversions") or 0) > 0

    evidence: dict[str, dict[str, Any]] = {}

    _set_many(
        evidence,
        ["M01", "X-PI1", "F-MF-02"],
        status="resolved" if pixel_ok else "manual_required",
        source="meta_api.pixel",
        value={"pixel_installed": pixel_ok, "last_fired_time": pixel.get("last_fired_time")},
        reason="Meta APIでPixel接続を確認" if pixel_ok else "Meta APIでPixel発火を確認できない",
    )
    _set_many(
        evidence,
        ["M02", "P-EF-02"],
        status="resolved" if capi_ok else "manual_required",
        source="meta_api.pixel",
        value={"capi_enabled": capi_ok, "server_events": bool(pixel.get("server_events"))},
        reason="Meta APIでCAPI/Server Eventsを確認" if capi_ok else "Meta APIでCAPI/Server Eventsを確認できない",
    )
    evidence["M03"] = _ev(
        status="resolved" if emq is not None and emq >= 6.0 else ("manual_required" if emq is not None else "unknown"),
        source="meta_api.pixel_stats",
        value={"event_match_quality": emq},
        reason="Event Match Qualityが基準以上" if emq is not None and emq >= 6.0 else "Event Match Qualityの確認が必要",
    )
    _set_many(
        evidence,
        ["M04", "F-AH-04", "M05", "M56"],
        status="unknown",
        source="meta_api.domain",
        value={"domain_verified": pixel.get("domain_verified")},
        reason="Domain Verification/AEM API未接続のため自動確定不可",
    )
    evidence["M06"] = _ev(
        status="unknown",
        source="meta_api.pixel",
        value={"dedup_key": None},
        reason="Pixel/CAPI dedup_keyはMeta insightsだけでは確定不可",
    )
    evidence["PC-MS-01"] = _ev(
        status="unknown",
        source="meta_api.pixel",
        value={"dedup_key": None},
        reason="dedup_key整合はイベント実装またはCAPI payload確認が必要",
    )

    performance_status = "resolved" if has_campaigns else "manual_required"
    for rid in [
        "M09", "M10", "M12", "M13", "M14", "M16", "M18", "M20",
        "M44", "M45", "M47", "M49", "M52", "M57", "M58", "M59",
        "M61", "M62", "M66", "M68", "M39",
    ]:
        evidence[rid] = _ev(
            status=performance_status,
            source="meta_api.insights",
            value={
                "campaign_count": len(campaigns),
                "spend": totals.get("cost") or totals.get("spend"),
                "conversions": totals.get("conversions"),
                "has_conversions": has_conversions,
            },
            reason="Meta Campaign/AdSet insights取得済み" if has_campaigns else "Meta insights未取得",
        )

    for rid in ["ANO_CPA_SPIKE", "ANO_IMPRESSION_DROP", "C05"]:
        evidence[rid] = _ev(
            status="resolved" if has_campaigns else "manual_required",
            source="meta_api.insights",
            value={
                "campaign_count": len(campaigns),
                "conversions": totals.get("conversions"),
                "cpa": totals.get("cpa"),
                "roas": totals.get("roas"),
            },
            reason="Meta insightsを異常検知の根拠として利用可能" if has_campaigns else "異常検知の根拠データ未取得",
        )

    for rid, rec in evidence.items():
        rec["rule_group"] = rule_group_for(rid)
    return evidence


def rule_group_for(rule_id: str) -> str | None:
    return RULE_TO_GROUP.get(rule_id)


def build_rule_group_index() -> dict[str, Any]:
    return {
        "groups": MEASUREMENT_RULE_GROUPS,
        "rule_to_group": RULE_TO_GROUP,
    }


def _set_many(out: dict[str, dict[str, Any]], rule_ids: list[str], **kwargs: Any) -> None:
    for rule_id in rule_ids:
        out[rule_id] = _ev(**kwargs)


def _ev(*, status: str, source: str, value: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "value": value,
        "reason": reason,
        "rule_group": None,
    }


def _calc_totals_from_campaigns(campaigns: list[dict[str, Any]]) -> dict[str, float]:
    spend = sum(_to_float(c.get("cost")) or 0.0 for c in campaigns if isinstance(c, dict))
    conversions = sum(_to_float(c.get("conversions")) or 0.0 for c in campaigns if isinstance(c, dict))
    revenue = sum(_to_float(c.get("revenue") or c.get("conversion_value")) or 0.0 for c in campaigns if isinstance(c, dict))
    return {
        "cost": spend,
        "conversions": conversions,
        "cpa": spend / conversions if conversions else 0.0,
        "roas": revenue / spend if spend else 0.0,
    }


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
