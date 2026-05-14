from engine.rules.quality_gate import audit_rule_quality
from engine.rules.registry import RuleRecord


def test_quality_gate_passes_cv_safe_customer_visible_rule():
    record = _record()

    result = audit_rule_quality([record])

    assert result["ok"] is True
    assert result["blocker_count"] == 0


def test_quality_gate_blocks_rules_without_track_learn_metadata():
    record = _record(
        replace_messaging=True,
        replace_payload=True,
        messaging_payload={
            "customer_title": "CPA確認",
            "priority": "A",
            "goal_stage": "cpa_diagnosis",
            "today_action": "確認してください",
            "yes_no_question": "確認できましたか?",
            "action_options": {"A": "はい", "B": "いいえ"},
        },
        payload={
            "id": "M99",
            "name": "Missing production metadata",
            "severity": "high",
            "enabled": True,
            "lifecycle": "active",
            "root_cause_group": "measurement_foundation",
            "axis_position": "TO-99",
        },
    )

    result = audit_rule_quality([record])
    checks = {issue["check"] for issue in result["issues"] if issue["severity"] == "blocker"}

    assert result["ok"] is False
    assert "data_source" in checks
    assert "impact_estimate" in checks
    assert "measurement_window" in checks
    assert "cv_guardrail" in checks


def _record(**overrides):
    messaging = {
        "customer_title": "Meta CAPI 実装状況の確認",
        "priority": "A",
        "goal_stage": "measurement_recovery",
        "today_action": "Events Manager を確認してください。",
        "yes_no_question": "CAPI は受信できていますか?",
        "action_options": {"A": "はい", "B": "いいえ", "C": "確認したい"},
        "answer_source_preference": ["api", "chatwork_reply"],
        "impact_estimate": {
            "lift_rate": {"minimum": 0.02, "realistic": 0.07, "upper": 0.16},
            "source_basis": "CAPI improves signal quality.",
            "measurement_window": {"signal": "1-2週", "verdict": "4週"},
        },
        "risk_control": {"cv_loss_risk": "low", "do_not_do": ["計測確認前に停止しない"]},
        "outcome": {"primary_metric": "cpa", "guardrail_metric": "conversions", "measurement_window_days": 28},
    }
    payload = {
        "id": "M02",
        "name": "CAPI",
        "severity": "high",
        "enabled": True,
        "lifecycle": "active",
        "root_cause_group": "measurement_foundation",
        "axis_position": "TO-02",
        "data_source": [{"source": "meta_api", "fields": ["pixel_status"]}],
        "prerequisite": [],
        "owner": "growth_ops",
    }
    messaging_override = overrides.pop("messaging_payload", {})
    if overrides.pop("replace_messaging", False):
        messaging = messaging_override
    else:
        messaging.update(messaging_override)
    payload_override = overrides.pop("payload", {})
    if overrides.pop("replace_payload", False):
        payload = payload_override
    else:
        payload.update(payload_override)
    return RuleRecord(
        rule_id=overrides.pop("rule_id", payload["id"]),
        name=payload["name"],
        layer="layer_a",
        category="meta",
        source_path="config/rules/meta_rules.yaml",
        severity=payload["severity"],
        root_cause_group=payload["root_cause_group"],
        decision_axis=payload["axis_position"],
        applies_to_keys=("ad_platforms",),
        applies_to={"ad_platforms": ["meta"]},
        expected_impact=messaging["impact_estimate"] if "impact_estimate" in messaging else None,
        has_expected_impact="impact_estimate" in messaging,
        messaging_mapped=True,
        enabled=True,
        lifecycle=payload["lifecycle"],
        customer_visible=True,
        prerequisite=payload.get("prerequisite"),
        messaging_payload=messaging,
        payload=payload,
        issues=(),
    )
