from engine.stores.adtruth_events import adtruth_band_summary, record_adtruth_event
from engine.stores.db import connect


def test_record_adtruth_event_persists_campaign_level_fraud_evidence(tmp_path):
    conn = connect(tmp_path / "zynect.db")

    row = record_adtruth_event(
        conn,
        client_id="pilotton",
        event={
            "session_id": "s-1",
            "visitor_id": "v-1",
            "event_name": "landing_page_view",
            "event_at": "2026-05-12T09:00:00+09:00",
            "campaign": {
                "source": "facebook",
                "medium": "paid_social",
                "campaign": "prospecting",
                "campaign_id": "cmp-1",
                "adset_id": "as-1",
                "ad_id": "ad-1",
                "placement": "feed",
            },
            "behavior": {
                "timeOnPage": 1,
                "scrollDepth": 0,
                "clickCount": 4,
                "averageClickInterval": 80,
                "mouseMoves": 0,
            },
        },
    )
    summary = adtruth_band_summary(conn, client_id="pilotton")

    assert row["client_id"] == "pilotton"
    assert row["campaign_id"] == "cmp-1"
    assert row["adset_id"] == "as-1"
    assert row["ad_id"] == "ad-1"
    assert row["placement"] == "feed"
    assert row["fraud_band"] in {"gray", "black"}
    assert summary["total"] == 1
