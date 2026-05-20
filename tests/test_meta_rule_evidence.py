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
            "purchase_event_match_quality": 7.9,
            "event_match_quality_by_event": {"Purchase": 7.9, "Lead": 6.5},
            "events_seen": ["Lead", "Purchase"],
        },
        "domain_verification": {
            "status": "ok",
            "business_id": "bm_1",
            "verified_count": 1,
            "expected_domains": ["example.com"],
            "verified_domains": ["example.com"],
            "missing_expected_domains": [],
            "domains": [{"domain": "example.com", "verification_status": "verified"}],
        },
        "capi_dedup": {
            "status": "ok",
            "matched_event_ids": 3,
        },
        "ad_delivery_statuses": {
            "status": "ok",
            "ad_count": 3,
            "problem_count": 0,
            "disapproved_count": 0,
            "problem_ads": [],
        },
        "account_status": {
            "status": "ok",
            "account_status": 1,
            "disable_reason": None,
            "business": {"id": "bm_1", "name": "BM"},
            "is_restricted": False,
        },
        "performance_diagnostics": {
            "campaigns": [{"name": "Campaign A", "cpa": 1000, "conversions": 10}],
            "adsets": [{"name": "Adset A", "cpa": 1500, "conversions": 4}],
            "ads": [{"name": "Ad A", "cpa": 2000, "conversions": 1}],
            "placements": [{"name": "Audience Network", "cpa": 2500, "conversions": 0}],
            "summary": {"campaign_count": 1, "adset_count": 2, "ad_count": 1, "placement_count": 1},
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
    assert audit["ad_insights"]["status"] == "ok"
    assert audit["placement_insights"]["status"] == "ok"
    assert audit["capi"]["purchase_event_match_quality"] == 7.9
    assert audit["domain_verification"]["missing_expected_domains"] == []
    assert audit["domain_verification"]["status"] == "ok"
    assert audit["account_quality"]["status"] == "ok"
    assert audit["ad_review"]["status"] == "ok"
    assert audit["capi_dedup"]["status"] == "ok"


def test_meta_rule_evidence_maps_api_to_measurement_rules():
    evidence = build_meta_rule_evidence(_meta_payload())

    assert evidence["M01"]["status"] == "resolved"
    assert evidence["X-PI1"]["status"] == "resolved"
    assert evidence["F-MF-02"]["rule_group"] == "meta_pixel_foundation"
    assert evidence["M02"]["status"] == "resolved"
    assert evidence["P-EF-02"]["status"] == "resolved"
    assert evidence["M03"]["value"]["event_match_quality"] == 7.9
    assert evidence["M03"]["value"]["score_source"] == "purchase_event_match_quality"
    assert evidence["F-AH-04"]["status"] == "resolved"
    assert evidence["F-AH-04"]["value"]["verified_domains"] == ["example.com"]
    assert evidence["PC-MS-01"]["status"] == "resolved"
    assert evidence["PC-MS-01"]["source"] == "capi_event_log"
    assert evidence["M16"]["status"] == "resolved"
    assert evidence["M16"]["source"] == "meta_api.ad_review"
    assert evidence["M18"]["status"] == "resolved"
    assert evidence["M19"]["status"] == "resolved"
    assert evidence["ANO_CPA_SPIKE"]["value"]["performance_diagnostics"]["adsets"][0]["name"] == "Adset A"


def test_meta_rule_group_index_unifies_duplicate_measurement_rules():
    index = build_rule_group_index()

    assert index["rule_to_group"]["M01"] == "meta_pixel_foundation"
    assert index["rule_to_group"]["X-PI1"] == "meta_pixel_foundation"
    assert index["rule_to_group"]["PC-MS-01"] == "meta_capi_and_dedup"
    assert index["rule_to_group"]["M18"] == "meta_account_quality_and_review"
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


def test_meta_connection_audit_keeps_permission_missing_actionable():
    payload = _meta_payload()
    payload["account_status"] = {
        "status": "permission_missing",
        "message": "(#100) Requires business_management permission to access the field.",
        "required_permissions": ["business_management"],
    }
    payload["domain_verification"] = {
        "status": "permission_missing",
        "business_id": "bm_1",
        "expected_domains": ["example.com"],
        "required_permissions": ["business_management"],
    }

    audit = build_meta_connection_audit(
        {"account_id": "act_123", "access_token_env": "META_TOKEN"},
        payload,
    )

    assert audit["account_quality"]["status"] == "permission_missing"
    assert audit["account_quality"]["required_permissions"] == ["business_management"]
    assert audit["domain_verification"]["status"] == "permission_missing"
    assert audit["domain_verification"]["required_permissions"] == ["business_management"]


def test_meta_ad_review_evidence_flags_problem_ads():
    payload = _meta_payload()
    payload["ad_delivery_statuses"] = {
        "status": "ok",
        "ad_count": 2,
        "problem_count": 1,
        "disapproved_count": 1,
        "problem_ads": [{"ad_id": "ad_1", "effective_status": "DISAPPROVED"}],
    }

    audit = build_meta_connection_audit(
        {"account_id": "act_123", "access_token_env": "META_TOKEN"},
        payload,
    )
    evidence = build_meta_rule_evidence(payload)

    assert audit["ad_review"]["status"] == "manual_required"
    assert evidence["M16"]["status"] == "manual_required"
    assert evidence["M16"]["value"]["problem_ads"][0]["ad_id"] == "ad_1"


def test_meta_domain_evidence_tracks_expected_domain_gaps():
    payload = _meta_payload()
    payload["domain_verification"] = {
        "status": "manual_required",
        "business_id": "bm_1",
        "verified_count": 1,
        "expected_domains": ["example.com", "lp.example.com"],
        "verified_domains": ["example.com"],
        "missing_expected_domains": ["lp.example.com"],
        "domains": [{"domain": "example.com", "verification_status": "verified"}],
    }

    evidence = build_meta_rule_evidence(payload)

    assert evidence["M04"]["status"] == "manual_required"
    assert evidence["M04"]["value"]["verified_domains"] == ["example.com"]
    assert evidence["M04"]["value"]["missing_expected_domains"] == ["lp.example.com"]


def test_meta_adapter_domain_verification_matches_expected_domains(monkeypatch):
    from adapters import meta_adapter

    def fake_api_get(path, access_token, params=None, timeout=30):
        assert path == "bm_1/owned_domains"
        return {
            "data": [
                {"domain": "example.com", "verification_status": "verified"},
                {"domain": "old.example.com", "verification_status": "pending"},
            ]
        }

    monkeypatch.setattr(meta_adapter, "_api_get", fake_api_get)

    result = meta_adapter._fetch_domain_verification(
        {"business_id": "bm_1", "domains": ["https://example.com", "lp.example.com"]},
        "token",
    )

    assert result["status"] == "manual_required"
    assert result["verified_expected_domains"] == ["example.com"]
    assert result["missing_expected_domains"] == ["lp.example.com"]


def test_client_state_sync_updates_domain_from_meta_api(tmp_path, monkeypatch):
    from scripts import daily_chatwork_check as daily

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    state_dir = tmp_path / "outputs" / "client_state"
    state_dir.mkdir(parents=True)
    (state_dir / "pilotton.yaml").write_text(
        "client_id: pilotton\n"
        "domain_verification_status: unverified\n",
        encoding="utf-8",
    )

    result = daily.sync_client_state_from_api_evidence(
        "pilotton",
        {
            "meta_rule_evidence": {
                "F-AH-04": {
                    "status": "resolved",
                    "reason": "Meta Business APIでDomain Verificationを確認",
                    "value": {
                        "domain_verified": True,
                        "verified_domains": ["titanistlab.jp", "mynailplex.jp"],
                    },
                }
            }
        },
        today_str="2026-05-20",
    )

    assert result["updated"] is True
    saved = __import__("yaml").safe_load((state_dir / "pilotton.yaml").read_text(encoding="utf-8"))
    assert saved["domain_verification_status"] == "completed"
    assert saved["domain_verification_source"] == "meta_api.domain"
    assert saved["verified_domains"] == ["titanistlab.jp", "mynailplex.jp"]


def test_fetch_data_keeps_meta_diagnostics_when_csv_fallback(monkeypatch, tmp_path):
    import pipeline

    csv_file = tmp_path / "pilotton.csv"
    csv_file.write_text("campaign,cost,conversions\nA,100,1\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "_validate", lambda data: data)

    def fake_fetch_meta_ads(_cfg):
        return {
            "campaigns": [],
            "pixel_status": {"pixel_installed": True},
            "meta_connection_audit": {"domain_verification": {"status": "ok"}},
            "meta_rule_evidence": {"F-AH-04": {"status": "resolved"}},
            "meta_rule_groups": {"groups": {}},
            "performance_diagnostics": {},
            "account_id": "act_1",
            "date_range": {"since": "2026-05-01", "until": "2026-05-20"},
        }

    monkeypatch.setattr("adapters.meta_adapter.fetch_meta_ads", fake_fetch_meta_ads)
    monkeypatch.setattr(
        "adapters.csv_adapter.load_csv",
        lambda _path: {"source": "csv", "campaigns": [{"campaign": "A"}]},
    )

    data = pipeline.fetch_data(
        "pilotton",
        {"ads": {"meta": {"account_id": "act_1", "access_token": "token"}}},
    )

    assert data["source"] == "csv"
    assert data["platform_diagnostics"]["meta"]["rule_evidence"]["F-AH-04"]["status"] == "resolved"


def test_meta_adapter_adset_and_placement_diagnostics_keep_cv_metrics():
    from adapters import meta_adapter

    adset = meta_adapter._parse_metric_row({
        "spend": "12000",
        "impressions": "2000",
        "clicks": "80",
        "ctr": "4.0",
        "frequency": "3.5",
        "actions": [{"action_type": "purchase", "value": "3"}],
        "action_values": [{"action_type": "purchase", "value": "30000"}],
    })
    adset.update({"name": "Adset A", "id": "as_1", "campaign_id": "c_1"})
    diagnostics = meta_adapter._build_performance_diagnostics(
        campaigns=[],
        adsets_by_campaign={"c_1": [adset]},
        ads=[],
        placements=[{
            "name": "facebook:feed",
            "adset_id": "as_1",
            "campaign_id": "c_1",
            "cost": 5000,
            "impressions": 1200,
            "conversions": 0,
            "publisher_platform": "facebook",
            "platform_position": "feed",
        }],
    )

    assert diagnostics["summary"]["adset_count"] == 1
    assert diagnostics["adsets"][0]["cpa"] == 4000
    assert "high_frequency" in diagnostics["adsets"][0]["diagnosis_flags"]
    assert diagnostics["placements"][0]["diagnosis_hint"] == "spend_without_cv"
