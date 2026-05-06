"""ADR-009 Zynect 推奨生成エンジンのテスト (8 ケース)"""
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.recommendation_engine import (
    load_operating_charter,
    load_decision_history,
    find_similar_past_decisions,
    generate_recommendation,
    check_consistency,
    log_decision,
    PREFS_DIR,
)


@pytest.fixture
def tmp_prefs(tmp_path, monkeypatch):
    """テスト用に PREFS_DIR を tmp に差し替え"""
    monkeypatch.setattr("engine.recommendation_engine.PREFS_DIR", tmp_path)
    return tmp_path


class TestOperatingCharter:
    def test_load_charter_returns_default_when_missing(self, tmp_prefs):
        """ファイル不在時は default charter (tbd 値) を返す"""
        charter = load_operating_charter("nonexistent_client")
        assert charter["primary_kpi"] == "tbd"
        assert charter["delegation_scope"] == "tbd"
        assert charter["charter_version"] == "0.1-default"

    def test_load_existing_charter(self, tmp_prefs):
        """既存 charter ファイルを正しく読み込む"""
        charter_data = {
            "client_id": "test_client",
            "operating_charter": {
                "primary_kpi": "cv_max",
                "cv_loss_tolerance_pct": 10,
                "charter_version": "1.0",
            },
        }
        path = tmp_prefs / "test_client.yaml"
        path.write_text(yaml.safe_dump(charter_data, allow_unicode=True))
        charter = load_operating_charter("test_client")
        assert charter["primary_kpi"] == "cv_max"
        assert charter["cv_loss_tolerance_pct"] == 10


class TestDecisionHistory:
    def test_load_empty_history(self, tmp_prefs):
        """履歴ファイル不在時は空リスト"""
        history = load_decision_history("test_client")
        assert history == []

    def test_log_and_load_decision(self, tmp_prefs):
        """log_decision で記録、load_decision_history で取得"""
        decision = {
            "rule_id": "F-AF-03",
            "customer_decision": "monitor",
            "consistency_with_charter": True,
            "consistency_with_past": True,
        }
        log_decision("test_client", decision)
        history = load_decision_history("test_client")
        assert len(history) == 1
        assert history[0]["customer_decision"] == "monitor"
        assert "decision_id" in history[0]
        assert "timestamp" in history[0]


class TestSimilarPastDecisions:
    def test_find_similar_returns_empty_when_no_history(self, tmp_prefs):
        """履歴なしの場合は空リスト"""
        rule_context = {"media": "meta", "fraud_score": 0.8, "cv_rate_pct": 10}
        similar = find_similar_past_decisions(rule_context, "no_client")
        assert similar == []

    def test_find_similar_matches_by_score(self, tmp_prefs):
        """媒体 + fraud_score + cv_rate が近い過去判断を返す"""
        for i, fs in enumerate([0.85, 0.50, 0.82]):
            log_decision("test_client", {
                "rule_id": f"R-{i}",
                "customer_decision": "block" if fs > 0.8 else "monitor",
                "grey_zone_data": {"media": "meta", "fraud_score": fs, "cv_rate_pct": 10},
            })
        similar = find_similar_past_decisions(
            {"media": "meta", "fraud_score": 0.83, "cv_rate_pct": 10},
            "test_client", top_n=3,
        )
        assert len(similar) >= 2
        # 0.85 と 0.82 が上位、0.50 は下位 or 除外
        assert similar[0]["grey_zone_data"]["fraud_score"] in [0.85, 0.82]


class TestRecommendation:
    def test_generate_recommendation_with_cv_max_charter(self, tmp_prefs):
        """primary_kpi=cv_max なら monitor 推奨"""
        rule = {
            "rule_id": "F-AF-03", "client_id": "test", "fraud_score": 0.85,
            "cv_count": 10, "cv_rate_pct": 8, "ad_cost": 100000, "media": "meta",
        }
        client_data = {
            "charter": {"primary_kpi": "cv_max", "cv_loss_tolerance_pct": 15},
            "history": [],
        }
        rec = generate_recommendation(rule, client_data)
        assert rec["recommended_action"] == "monitor_with_close_watch"
        assert "cv_max" in rec["rationale"]
        assert rec["confidence"] > 0

    def test_generate_recommendation_with_cpa_min_charter(self, tmp_prefs):
        """primary_kpi=cpa_min なら block_aggressive 推奨"""
        rule = {
            "rule_id": "F-AF-03", "client_id": "test", "fraud_score": 0.85,
            "cv_count": 1, "cv_rate_pct": 1, "ad_cost": 100000, "media": "meta",
            "total_cv_30d": 100,
        }
        client_data = {
            "charter": {"primary_kpi": "cpa_min", "cv_loss_tolerance_pct": 5},
            "history": [],
        }
        rec = generate_recommendation(rule, client_data)
        # cv_count=1, total=100 → loss_pct=1% < tolerance 5% → downgrade されない
        assert rec["recommended_action"] == "block_aggressive"


class TestConsistency:
    def test_check_consistency_no_data(self, tmp_prefs):
        """履歴なしなら None"""
        result = check_consistency("test_client")
        assert result["charter_consistency_pct"] is None
        assert result["decision_count"] == 0
