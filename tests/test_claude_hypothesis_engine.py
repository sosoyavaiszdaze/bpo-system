from __future__ import annotations

import json


def _write_day(path, client, date, campaigns):
    total_cost = sum(c["cost"] for c in campaigns)
    total_cv = sum(c["conversions"] for c in campaigns)
    total_imp = sum(c["impressions"] for c in campaigns)
    payload = {
        "source": "test",
        "campaigns": campaigns,
        "totals": {
            "total_cost": total_cost,
            "total_conversions": total_cv,
            "total_impressions": total_imp,
            "avg_cpa": total_cost / total_cv,
        },
    }
    (path / f"{client}_{date}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_build_anomaly_followup_returns_yaml_rule_hypotheses(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = "pilotton"
    _write_day(
        tmp_path, client, "2026-05-05",
        [
            {"campaign": "MYNAILPLEX_配信_新", "campaign_id": "c1", "platform": "meta", "cost": 2340093, "conversions": 568, "impressions": 866756},
            {"campaign": "Advantage+_詳細ターゲ", "campaign_id": "c2", "platform": "meta", "cost": 251068, "conversions": 60, "impressions": 65804},
        ],
    )
    _write_day(
        tmp_path, client, "2026-05-09",
        [
            {"campaign": "MYNAILPLEX_配信_新", "campaign_id": "c1", "platform": "meta", "cost": 1041997, "conversions": 111, "impressions": 368544},
            {"campaign": "Advantage+_詳細ターゲ", "campaign_id": "c2", "platform": "meta", "cost": 281684, "conversions": 32, "impressions": 75519},
        ],
    )

    from engine.claude_hypothesis_engine import build_anomaly_followup

    record = {
        "rule_id": "ANO_CPA_SPIKE",
        "first_detected_date": "2026-05-07",
        "resolved_date": "2026-05-09",
        "payload": {
            "metric": "cpa_spike",
            "fact": "[Meta] CPA +76.3% 上昇 (¥5,494 → ¥9,686)",
        },
    }
    out = build_anomaly_followup(client, record, "2026-05-09", data_dir=tmp_path)

    assert out is not None
    assert out["type"] == "continued_issue"
    assert out["source"] == "fallback"
    assert out["campaign_metrics"][0]["campaign"] == "MYNAILPLEX_配信_新"
    rule_ids = [h["rule_id"] for h in out["hypotheses"]]
    assert "M68" in rule_ids
    assert out["customer_question"]


def test_build_current_todo_hypotheses_uses_meta_diagnostics_without_claude(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from engine.claude_hypothesis_engine import build_current_todo_hypotheses

    out = build_current_todo_hypotheses(
        "pilotton",
        {
            "platform_diagnostics": {
                "meta": {
                    "performance_diagnostics": {
                        "placements": [
                            {
                                "name": "Audience Network",
                                "cost": 50000,
                                "conversions": 0,
                                "cpa": None,
                            }
                        ],
                        "adsets": [
                            {
                                "name": "Broad Adset",
                                "cost": 120000,
                                "conversions": 5,
                                "cpa": 24000,
                            }
                        ],
                    }
                }
            }
        },
        ["ANO_CPA_SPIKE", "F-MF-02"],
    )

    assert set(out) == {"ANO_CPA_SPIKE", "F-MF-02"}
    assert out["ANO_CPA_SPIKE"]["source"] == "fallback"
    assert "CV" in out["ANO_CPA_SPIKE"]["summary"]
    assert out["ANO_CPA_SPIKE"]["hypotheses"][0]["rule_id"] == "M39"
    assert "停止しない" in " ".join(out["ANO_CPA_SPIKE"]["do_not_do"])
