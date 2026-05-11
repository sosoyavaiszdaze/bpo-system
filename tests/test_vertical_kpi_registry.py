from engine.vertical_kpi_registry import (
    build_client_kpi_readiness,
    get_vertical_kpi_profile,
    load_vertical_kpi_registry,
    normalize_vertical_event,
)


def test_matching_app_profile_contains_required_app_growth_kpis():
    profile = get_vertical_kpi_profile("matching_app")

    assert profile.display_name == "Matching app / Dating app"
    assert profile.required_event_ids == [
        "app_install",
        "registration_cv",
        "identity_verification_cv",
        "paid_conversion_cv",
    ]
    assert "paid_cpa" in profile.economic_metrics
    assert "ltv_30d" in profile.economic_metrics
    assert "mmp" in profile.data_sources
    assert any(q["id"] == "gender_balance" for q in profile.quality_dimensions)


def test_matching_app_aliases_resolve_to_same_profile():
    registry = load_vertical_kpi_registry()

    assert registry["dating_app"].vertical_id == "matching_app"
    assert registry["app_subscription"].vertical_id == "matching_app"


def test_matching_app_event_alias_normalization():
    profile = get_vertical_kpi_profile("matching_app")

    assert normalize_vertical_event("complete_registration", profile) == "registration_cv"
    assert normalize_vertical_event("kyc_completed", profile) == "identity_verification_cv"
    assert normalize_vertical_event("subscription_start", profile) == "paid_conversion_cv"
    assert normalize_vertical_event("unknown_event", profile) is None


def test_client_kpi_readiness_flags_missing_app_sources():
    readiness = build_client_kpi_readiness(
        "matchco",
        {
            "vertical": "matching_app",
            "ads": {"meta": {"enabled": True, "account_id": "act_123"}},
        },
    )

    assert readiness["vertical_id"] == "matching_app"
    assert readiness["source_status"]["meta_api"]["configured"] is True
    assert readiness["source_status"]["sdk_or_backend_events"]["configured"] is False
    assert readiness["required_missing"] == ["sdk_or_backend_events"]
    assert readiness["ready_for_high_confidence_recommendations"] is False


def test_client_kpi_readiness_accepts_mmp_sdk_skan_and_revenue_sources():
    readiness = build_client_kpi_readiness(
        "matchco",
        {
            "vertical": "dating_app",
            "ads": {"meta": {"enabled": True, "account_id": "act_123"}},
            "app": {
                "mmp": {"enabled": True, "provider": "adjust"},
                "sdk_event_log_path": "data/events.jsonl",
                "skan": {"enabled": True, "provider": "adjust"},
                "revenue": {"enabled": True, "provider": "revenuecat"},
            },
        },
    )

    assert readiness["vertical_id"] == "matching_app"
    assert readiness["required_missing"] == []
    assert readiness["recommended_missing"] == []
    assert readiness["ready_for_high_confidence_recommendations"] is True


def test_ec_d2c_alias_keeps_existing_pilotton_style_industry_supported():
    readiness = build_client_kpi_readiness(
        "pilotton",
        {
            "company": {"industry": "beauty_d2c", "ec_platform": "ecforce"},
            "ads": {"meta": {"enabled": True, "account_id": "act_123"}},
        },
    )

    assert readiness["vertical_id"] == "ec_d2c"
    assert readiness["required_missing"] == []
