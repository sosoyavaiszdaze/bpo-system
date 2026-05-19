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
    "meta_account_quality_and_review": ["M16", "M18", "M19"],
    "meta_attribution_window": ["M62", "F-MF-08"],
    "meta_cpa_spike_diagnosis": ["C05", "ANO_CPA_SPIKE", "M09", "M10", "M12", "M13", "M45", "M68"],
    "meta_delivery_drop_diagnosis": ["ANO_IMPRESSION_DROP", "M14", "M20", "M44"],
    "meta_creative_diversity_and_fatigue": ["M24", "M25", "M28", "M35", "M47", "M57", "M58", "M59"],
}


RULE_TO_GROUP = {
    rule_id: group
    for group, rules in MEASUREMENT_RULE_GROUPS.items()
    for rule_id in rules
}

GROUP_REQUIRED_DATA_SOURCES: dict[str, list[str]] = {
    "meta_pixel_foundation": ["meta_api.pixel"],
    "meta_capi_and_dedup": ["meta_api.pixel", "capi_event_log"],
    "meta_domain_and_aem": ["meta_api.domain"],
    "meta_account_quality_and_review": ["meta_api.account", "meta_api.ad_review"],
    "meta_attribution_window": ["meta_api.insights"],
    "meta_cpa_spike_diagnosis": ["meta_api.insights"],
    "meta_delivery_drop_diagnosis": ["meta_api.insights"],
    "meta_creative_diversity_and_fatigue": ["meta_api.insights", "creative_asset_audit"],
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
    domain = meta_data.get("domain_verification") or {}
    account = meta_data.get("account_status") or {}
    capi_dedup = meta_data.get("capi_dedup") or {}
    ad_review = meta_data.get("ad_delivery_statuses") or {}

    account_id = meta_cfg.get("account_id") or meta_data.get("account_id")
    has_token = bool(meta_cfg.get("access_token") or meta_cfg.get("access_token_env"))
    adset_count = sum(int(c.get("adset_count") or 0) for c in campaigns if isinstance(c, dict))
    performance = meta_data.get("performance_diagnostics") or {}

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
            "status": "ok" if adset_count > 0 or (performance.get("summary") or {}).get("adset_count") else ("no_data" if campaigns else "unknown"),
            "adset_count": adset_count or (performance.get("summary") or {}).get("adset_count", 0),
        },
        "ad_insights": {
            "status": "ok" if (performance.get("summary") or {}).get("ad_count") else ("no_data" if campaigns else "unknown"),
            "ad_count": (performance.get("summary") or {}).get("ad_count", 0),
        },
        "placement_insights": {
            "status": "ok" if (performance.get("summary") or {}).get("placement_count") else ("no_data" if campaigns else "unknown"),
            "placement_count": (performance.get("summary") or {}).get("placement_count", 0),
        },
        "pixel": {
            "status": "ok" if pixel.get("pixel_installed") else "no_data",
            "pixel_count": pixel.get("pixel_count", 0),
            "primary_pixel_id": pixel.get("primary_pixel_id"),
            "last_fired_time": pixel.get("last_fired_time"),
            "events_seen": pixel.get("events_seen") or [],
        },
        "capi": {
            "status": _capi_status(pixel, capi_dedup),
            "event_match_quality": pixel.get("event_match_quality"),
            "purchase_event_match_quality": pixel.get("purchase_event_match_quality"),
            "event_match_quality_by_event": pixel.get("event_match_quality_by_event") or {},
            "server_events": bool(pixel.get("server_events")),
            "dedup_status": capi_dedup.get("status", "unknown"),
        },
        "domain_verification": {
            "status": domain.get("status") or "unknown",
            "business_id": domain.get("business_id"),
            "verified_count": domain.get("verified_count", 0),
            "verified_domains": domain.get("verified_domains") or [],
            "missing_expected_domains": domain.get("missing_expected_domains") or [],
            "expected_domains": domain.get("expected_domains") or [],
            "reason": domain.get("reason") or domain.get("error"),
            "required_permissions": domain.get("required_permissions") or [],
        },
        "account_quality": {
            "status": _account_quality_status(account),
            "account_status": account.get("account_status"),
            "disable_reason": account.get("disable_reason"),
            "is_restricted": account.get("is_restricted"),
            "reason": account.get("message") or account.get("error"),
            "required_permissions": account.get("required_permissions") or [],
        },
        "ad_review": {
            "status": _ad_review_status(ad_review),
            "ad_count": ad_review.get("ad_count", 0),
            "problem_count": ad_review.get("problem_count", 0),
            "disapproved_count": ad_review.get("disapproved_count", 0),
            "problem_ads": ad_review.get("problem_ads") or [],
            "reason": ad_review.get("message") or ad_review.get("error"),
            "required_permissions": ad_review.get("required_permissions") or [],
        },
        "capi_dedup": {
            "status": capi_dedup.get("status", "unknown"),
            "matched_event_ids": capi_dedup.get("matched_event_ids", 0),
            "reason": capi_dedup.get("reason"),
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
    domain = meta_data.get("domain_verification") or {}
    capi_dedup = meta_data.get("capi_dedup") or {}
    performance = meta_data.get("performance_diagnostics") or {}
    account = meta_data.get("account_status") or {}
    ad_review = meta_data.get("ad_delivery_statuses") or {}

    pixel_ok = bool(pixel.get("pixel_installed"))
    capi_ok = bool(pixel.get("capi_enabled") or pixel.get("server_events"))
    emq = _to_float(pixel.get("purchase_event_match_quality"))
    emq_source = "purchase_event_match_quality"
    if emq is None:
        emq = _to_float(pixel.get("event_match_quality"))
        emq_source = "event_match_quality_average"
    has_campaigns = bool(campaigns)
    total_spend = totals.get("cost") or totals.get("spend") or totals.get("total_cost")
    total_conversions = totals.get("conversions") or totals.get("total_conversions")
    total_cpa = totals.get("cpa") or totals.get("avg_cpa")
    total_roas = totals.get("roas") or totals.get("total_roas") or totals.get("avg_roas")
    has_conversions = float(total_conversions or 0) > 0

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
        value={
            "capi_enabled": capi_ok,
            "server_events": bool(pixel.get("server_events")),
            "dedup_status": capi_dedup.get("status"),
            "purchase_event_match_quality": pixel.get("purchase_event_match_quality"),
        },
        reason="Meta APIでCAPI/Server Eventsを確認" if capi_ok else "Meta APIでCAPI/Server Eventsを確認できない",
    )
    evidence["M03"] = _ev(
        status="resolved" if emq is not None and emq >= 6.0 else ("manual_required" if emq is not None else "unknown"),
        source="meta_api.pixel_stats",
        value={
            "event_match_quality": emq,
            "score_source": emq_source,
            "event_match_quality_by_event": pixel.get("event_match_quality_by_event") or {},
            "events_seen": pixel.get("events_seen") or [],
        },
        reason="Event Match Qualityが基準以上" if emq is not None and emq >= 6.0 else "Event Match Qualityの確認が必要",
    )
    domain_status = domain.get("status")
    domain_resolved = domain_status == "ok"
    _set_many(
        evidence,
        ["M04", "F-AH-04", "M05", "M56"],
        status="resolved" if domain_resolved else ("manual_required" if domain_status == "manual_required" else "unknown"),
        source="meta_api.domain",
        value={
            "domain_verified": domain_resolved,
            "domain_status": domain_status,
            "verified_count": domain.get("verified_count", 0),
            "verified_domains": domain.get("verified_domains") or [],
            "missing_expected_domains": domain.get("missing_expected_domains") or [],
            "expected_domains": domain.get("expected_domains") or [],
            "domains": domain.get("domains") or [],
        },
        reason="Meta Business APIでDomain Verificationを確認" if domain_resolved else "Domain Verificationは確認が必要",
    )
    evidence["M06"] = _ev(
        status="resolved" if capi_dedup.get("status") == "ok" else ("manual_required" if capi_dedup.get("status") in {"manual_required", "missing_log"} else "unknown"),
        source="capi_event_log",
        value=capi_dedup,
        reason="CAPIログでPixel/Serverのevent_id一致を確認" if capi_dedup.get("status") == "ok" else "dedup_keyはCAPIログまたは実装確認が必要",
    )
    evidence["PC-MS-01"] = _ev(
        status="resolved" if capi_dedup.get("status") == "ok" else ("manual_required" if capi_dedup.get("status") in {"manual_required", "missing_log"} else "unknown"),
        source="capi_event_log",
        value=capi_dedup,
        reason="CAPIログでdedup_key整合を確認" if capi_dedup.get("status") == "ok" else "dedup_key整合はイベント実装またはCAPI payload確認が必要",
    )

    ad_review_status = _ad_review_status(ad_review)
    evidence["M16"] = _ev(
        status="resolved" if ad_review_status == "ok" else ("manual_required" if ad_review_status in {"manual_required", "permission_missing", "error"} else "unknown"),
        source="meta_api.ad_review",
        value={
            "ad_review_status": ad_review_status,
            "ad_count": ad_review.get("ad_count", 0),
            "problem_count": ad_review.get("problem_count", 0),
            "disapproved_count": ad_review.get("disapproved_count", 0),
            "problem_ads": ad_review.get("problem_ads") or [],
            "required_permissions": ad_review.get("required_permissions") or [],
        },
        reason="Meta APIで不承認/警告広告なしを確認" if ad_review_status == "ok" else "広告審査状態は確認が必要",
    )
    account_quality_status = _account_quality_status(account)
    evidence["M18"] = _ev(
        status="resolved" if account_quality_status == "ok" else ("manual_required" if account_quality_status in {"manual_required", "permission_missing", "error"} else "unknown"),
        source="meta_api.account_quality",
        value={
            "account_quality_status": account_quality_status,
            "account_status": account.get("account_status"),
            "disable_reason": account.get("disable_reason"),
            "is_restricted": account.get("is_restricted"),
            "required_permissions": account.get("required_permissions") or [],
        },
        reason="Meta APIで広告アカウント制限なしを確認" if account_quality_status == "ok" else "広告アカウント制限/権限状態は確認が必要",
    )
    evidence["M19"] = _ev(
        status="resolved" if account.get("business") else ("manual_required" if account_quality_status in {"permission_missing", "manual_required", "error"} else "unknown"),
        source="meta_api.account_quality",
        value={
            "business": account.get("business"),
            "account_quality_status": account_quality_status,
            "required_permissions": account.get("required_permissions") or [],
        },
        reason="Meta APIでBusiness Manager紐付きを確認" if account.get("business") else "Business Manager権限/所有状態は確認が必要",
    )

    performance_status = "resolved" if has_campaigns else "manual_required"
    for rid in [
        "M09", "M10", "M12", "M13", "M14", "M20",
        "M24", "M25", "M28", "M35", "M44", "M45", "M47", "M49", "M52", "M57", "M58", "M59",
        "M61", "M62", "M66", "M68", "M39",
    ]:
        evidence[rid] = _ev(
            status=performance_status,
            source="meta_api.insights",
            value={
                "campaign_count": len(campaigns),
                "spend": total_spend,
                "conversions": total_conversions,
                "has_conversions": has_conversions,
                "worst_campaigns": (performance.get("campaigns") or [])[:3],
                "worst_adsets": (performance.get("adsets") or [])[:3],
                "worst_ads": (performance.get("ads") or [])[:3],
                "worst_placements": (performance.get("placements") or [])[:3],
                "diagnostic_summary": performance.get("summary") or {},
            },
            reason="Meta Campaign/AdSet insights取得済み" if has_campaigns else "Meta insights未取得",
        )

    for rid in ["ANO_CPA_SPIKE", "ANO_IMPRESSION_DROP", "C05"]:
        evidence[rid] = _ev(
            status="resolved" if has_campaigns else "manual_required",
            source="meta_api.insights",
            value={
                "campaign_count": len(campaigns),
                "conversions": total_conversions,
                "cpa": total_cpa,
                "roas": total_roas,
                "performance_diagnostics": performance,
            },
            reason="Meta insightsを異常検知の根拠として利用可能" if has_campaigns else "異常検知の根拠データ未取得",
        )

    for rid, rec in evidence.items():
        rec["rule_group"] = rule_group_for(rid)
    return evidence


def rule_group_for(rule_id: str) -> str | None:
    return RULE_TO_GROUP.get(rule_id)


def required_data_sources_for_rule(rule_id: str) -> list[str]:
    """Return the evidence sources needed to evaluate one Meta rule.

    Rule YAML is still being migrated, so this provides a canonical Meta-first
    fallback for Rule Registry operations and UI readiness checks.
    """
    group = rule_group_for(rule_id)
    if not group:
        return []
    return list(GROUP_REQUIRED_DATA_SOURCES.get(group, []))


def build_rule_group_index() -> dict[str, Any]:
    return {
        "groups": MEASUREMENT_RULE_GROUPS,
        "rule_to_group": RULE_TO_GROUP,
        "group_required_data_sources": GROUP_REQUIRED_DATA_SOURCES,
    }


def _account_quality_status(account: dict[str, Any]) -> str:
    if not account:
        return "unknown"
    if account.get("status") == "permission_missing":
        return "permission_missing"
    if account.get("status") == "error":
        return "error"
    if str(account.get("account_status")) == "1" and not account.get("disable_reason"):
        return "ok"
    if account.get("account_status") is not None or account.get("disable_reason") is not None:
        return "manual_required"
    return account.get("status") or "unknown"


def _capi_status(pixel: dict[str, Any], capi_dedup: dict[str, Any]) -> str:
    if capi_dedup.get("status") == "ok":
        return "ok"
    if pixel.get("capi_enabled") or pixel.get("server_events"):
        return "ok"
    if capi_dedup.get("status") in {"manual_required", "missing_log"}:
        return "manual_required"
    return "unknown"


def _ad_review_status(ad_review: dict[str, Any]) -> str:
    if not ad_review:
        return "unknown"
    if ad_review.get("status") == "permission_missing":
        return "permission_missing"
    if ad_review.get("status") == "error":
        return "error"
    if ad_review.get("status") == "ok" and int(ad_review.get("problem_count") or 0) == 0:
        return "ok"
    if ad_review.get("status") == "ok":
        return "manual_required"
    return ad_review.get("status") or "unknown"


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
