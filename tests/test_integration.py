"""統合テスト — 実データでの run_audit 実行検証"""
import os
import sys
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_data():
    """テスト用の最小限キャンペーンデータ"""
    return {
        "campaigns": [
            {
                "campaign": "Google_Search_Brand_2024", "platform": "google",
                "campaign_type": "search", "status": "ENABLED",
                "bidding_strategy": "target_cpa", "daily_budget": 50000,
                "impressions": 10000, "clicks": 500, "cost": 45000,
                "conversions": 25, "cpa": 1800, "ctr": 5.0, "cpm": 4500,
                "frequency": 1.5, "roas": 3.5, "revenue": 157500,
                "ad_count": 3, "keyword_count": 20, "adgroup_count": 3,
                "quality_score_avg": 7,
            },
            {
                "campaign": "Meta_Conversion_Prospecting", "platform": "meta",
                "campaign_type": "conversions", "status": "ENABLED",
                "bidding_strategy": "lowest_cost", "daily_budget": 30000,
                "impressions": 50000, "clicks": 800, "cost": 28000,
                "conversions": 15, "cpa": 1866, "ctr": 1.6, "cpm": 560,
                "frequency": 2.0, "roas": 2.0, "revenue": 56000, "ad_count": 5,
            },
        ],
        "totals": {"total_cost": 73000, "total_conversions": 40, "avg_cpa": 1825, "avg_ctr": 3.3},
        "pixel_statuses": {"meta": {"pixel_installed": True, "capi_enabled": True}},
        "client_config": {"objective": "cpa_minimize"},
    }


@pytest.fixture
def thresholds():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "thresholds.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestRunAuditIntegration:
    """run_audit の統合テスト"""

    def test_audit_returns_valid_structure(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        for key in ("score", "grade", "issues", "platform_scores", "rule_coverage", "axis_conflicts", "conflicts"):
            assert key in result, f"Missing key: {key}"

    def test_score_in_valid_range(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        assert 0 <= result["score"] <= 100

    def test_grade_is_valid(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        assert result["grade"] in ("A", "B", "C", "D", "F")

    def test_issues_have_severity(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        for issue in result["issues"]:
            assert issue.get("severity") in ("critical", "high", "medium", "warning", "low")

    def test_platform_scores_present(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        assert len(result["platform_scores"]) > 0

    def test_rule_coverage_present(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        cov = result.get("rule_coverage", {})
        assert "coverage_percent" in cov
        assert cov["total_yaml_rules"] > 0

    def test_conflicts_structure(self, sample_data, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_client", sample_data, thresholds)
        for c in result.get("conflicts", []):
            assert "conflict_group" in c
            assert "auto_resolved" in c

    def test_no_crash_with_empty_data(self, thresholds):
        from analyzers.ads_audit import run_audit
        result = run_audit("test_empty", {"campaigns": []}, thresholds)
        assert result["score"] == 0
        assert result["grade"] == "F"

    def test_no_crash_with_single_platform(self, thresholds):
        from analyzers.ads_audit import run_audit
        data = {
            "campaigns": [{"campaign": "TT_Test", "platform": "tiktok",
                           "impressions": 5000, "clicks": 100, "cost": 10000,
                           "conversions": 5, "cpa": 2000, "ctr": 2.0, "cpm": 2000}],
            "totals": {"total_cost": 10000, "total_conversions": 5},
        }
        result = run_audit("test_single", data, thresholds)
        assert result["score"] >= 0
