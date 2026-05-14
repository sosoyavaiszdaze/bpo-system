from engine.meta_rule_evidence import (
    build_meta_connection_audit,
    build_meta_rule_evidence,
    build_rule_group_index,
)
from engine.rules.decision_trace import build_meta_api_evidence_traces


def _meta_payload():
    return {
        "account_id": "act_123",
        "campaigns": [
            {
                "campaign": "A",
                "cost": 10000,
                "conversions": 10,
                "revenue": 50000,
                "adset_count": 2,
            }
        ],
        "totals": {"cost": 10000, "conversions": 10, "cpa": 1000, "roas": 5.0},
        "pixel_status": {
            "pixel_installed": True,
            "pixel_count": 1,
            "primary_pixel_id": "px_1",
            "last_fired_time": "2026-05-10T00:00:00+0000",
            "capi_enabled": True,
            "server_events": True,
            "event_match_quality": 7.2,
        },
        "domain_verification": {
            "status": "ok",
            "business_id": "bm_1",
            "verified_count": 1,
            "domains": [{"domain": "example.com", "verification_status": "verified"}],
        },
        "capi_dedup": {
            "status": "ok",
            "matched_event_ids": 3,
        },
        "performance_diagnostics": {
            "campaigns": [{"name": "Campaign A", "cpa": 1000, "conversions": 10}],
            "adsets": [{"name": "Adset A", "cpa": 1500, "conversions": 4}],
            "ads": [{"name": "Ad A", "cpa": 2000, "conversions": 1}],
            "placements": [{"name": "Audience Network", "cpa": 2500, "conversions": 0}],
        },
    }


def test_meta_connection_audit_covers_required_capabilities():
    audit = build_meta_connection_audit(
        {"account_id": "act_123", "access_token_env": "META_TOKEN"},
        _meta_payload(),
    )

    assert audit["ad_account"]["status"] == "ok"
    assert audit["pixel"]["status"] == "ok"
    assert audit["capi"]["status"] == "ok"
    assert audit["campaign_insights"]["campaign_count"] == 1
    assert audit["adset_insights"]["adset_count"] == 2
    assert audit["domain_verification"]["status"] == "ok"
    assert audit["account_quality"]["status"] == "unknown"
    assert audit["capi_dedup"]["status"] == "ok"


def test_meta_rule_evidence_maps_api_to_measurement_rules():
    evidence = build_meta_rule_evidence(_meta_payload())

    assert evidence["M01"]["status"] == "resolved"
    assert evidence["X-PI1"]["status"] == "resolved"
    assert evidence["F-MF-02"]["rule_group"] == "meta_pixel_foundation"
    assert evidence["M02"]["status"] == "resolved"
    assert evidence["P-EF-02"]["status"] == "resolved"
    assert evidence["M03"]["value"]["event_match_quality"] == 7.2
    assert evidence["F-AH-04"]["status"] == "resolved"
    assert evidence["PC-MS-01"]["status"] == "resolved"
    assert evidence["PC-MS-01"]["source"] == "capi_event_log"
    assert evidence["ANO_CPA_SPIKE"]["value"]["performance_diagnostics"]["adsets"][0]["name"] == "Adset A"


def test_meta_rule_group_index_unifies_duplicate_measurement_rules():
    index = build_rule_group_index()

    assert index["rule_to_group"]["M01"] == "meta_pixel_foundation"
    assert index["rule_to_group"]["X-PI1"] == "meta_pixel_foundation"
    assert index["rule_to_group"]["PC-MS-01"] == "meta_capi_and_dedup"
    assert "M02" in index["groups"]["meta_capi_and_dedup"]
    assert "meta_api.pixel" in index["group_required_data_sources"]["meta_pixel_foundation"]
    assert "creative_asset_audit" in index["group_required_data_sources"]["meta_creative_diversity_and_fatigue"]


def test_meta_api_evidence_decision_traces_include_values_and_group():
    evidence = build_meta_rule_evidence(_meta_payload())
    traces = build_meta_api_evidence_traces(
        client_id="pilotton",
        evaluation_date="2026-05-10",
        audit_results={
            "platform_diagnostics": {
                "meta": {
                    "connection_audit": {"pixel": {"status": "ok"}},
                    "rule_evidence": evidence,
                    "rule_groups": build_rule_group_index(),
                }
            }
        },
    )

    m02 = next(t for t in traces if t["rule_id"] == "M02")
    assert m02["stage"] == "meta_api_evidence"
    assert m02["status"] == "resolved"
    assert m02["evidence"]["rule_group"] == "meta_capi_and_dedup"
    assert m02["evidence"]["value"]["capi_enabled"] is True


def test_rule_registry_uses_meta_groups_as_dedupe_and_data_sources(tmp_path):
    from engine.rules.registry import load_rule_registry
    from engine.stores.db import connect
    from engine.stores.rules import meta_rule_operations_summary, sync_rule_registry

    db = tmp_path / "ops.sqlite"
    conn = connect(db)
    try:
        sync_rule_registry(conn, load_rule_registry())
        summary = meta_rule_operations_summary(conn)
    finally:
        conn.close()

    assert summary["meta_high_critical_unmapped"] == 0
    assert summary["meta_required_data_sources"] > 0
    assert summary["meta_duplicate_group_defined"] > 0
