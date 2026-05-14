from engine.adtruth_behavioral_signals import normalize_adtruth_event, score_adtruth_event


def test_normalize_adtruth_event_accepts_sdk_style_payload():
    event = {
        "session_id": "s1",
        "campaign": {"source": "facebook", "medium": "paid_social", "campaign": "spring"},
        "click_ids": {"fbclid": "fb.123"},
        "behavior": {
            "timeOnPage": 42,
            "scrollDepth": 80,
            "clickCount": 2,
            "mouseMoves": 120,
        },
    }

    normalized = normalize_adtruth_event(event)

    assert normalized["source"] == "facebook"
    assert normalized["medium"] == "paid_social"
    assert normalized["campaign"] == "spring"
    assert normalized["fbclid"] == "fb.123"
    assert normalized["time_on_page"] == 42


def test_score_adtruth_event_flags_machine_like_behavior():
    result = score_adtruth_event({
        "utm_source": "meta",
        "utm_medium": "paid_social",
        "behavior_metrics": {
            "time_on_page": 2,
            "scroll_depth": 0,
            "click_count": 25,
            "mouse_moves": 0,
            "avg_click_interval_ms": 80,
            "time_to_first_click_ms": 120,
        },
    })

    assert result["fraud_band"] == "black"
    assert result["fraud_probability"] >= 0.75
    assert "machine_like_click_interval" in result["signals"]
    assert "paid_session_without_click_id" in result["signals"]


def test_score_adtruth_event_keeps_normal_behavior_clean():
    result = score_adtruth_event({
        "utm_source": "meta",
        "utm_medium": "paid_social",
        "fbclid": "fb.123",
        "behavior_metrics": {
            "time_on_page": 65,
            "scroll_depth": 78,
            "click_count": 3,
            "mouse_moves": 220,
            "avg_click_interval_ms": 2400,
            "time_to_first_click_ms": 4200,
        },
    })

    assert result["fraud_band"] == "clean"
    assert result["signals"] == {}
