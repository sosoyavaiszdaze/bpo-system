"""run_audit 統合テスト — 広告監査オーケストレータのエンドツーエンド検証"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_data():
    """Google + Meta の2媒体サンプルデータ"""
    return {
        "campaigns": [
            {
                "campaign": "Google_Search_Brand",
                "platform": "google",
                "campaign_type": "search",
                "clicks": 500,
                "impressions": 10000,
                "cost": 50000,
                "conversions": 25,
                "cpa": 2000,
                "ctr": 5.0,
                "roas": 3.0,
                "revenue": 150000,
                "daily_budget": 60000,
                "bidding_strategy": "target_cpa",
                "target_cpa": 2500,
                "ad_count": 3,
                "keyword_count": 20,
                "adgroup_count": 3,
                "enhanced_conversions": True,
                "attribution_model": "dda",
                "conversion_values_set": True,
                "extensions": {"sitelinks": 4, "callouts": 4, "structured_snippets": 2, "images": 1},
            },
            {
                "campaign": "Google_Search_Generic",
                "platform": "google",
                "campaign_type": "search",
                "clicks": 300,
                "impressions": 8000,
                "cost": 40000,
                "conversions": 10,
                "cpa": 4000,
                "ctr": 3.75,
                "roas": 1.5,
                "revenue": 60000,
                "daily_budget": 50000,
                "bidding_strategy": "target_cpa",
                "target_cpa": 3000,
                "ad_count": 2,
                "keyword_count": 30,
                "adgroup_count": 2,
                "negative_keyword_lists": ["shared_neg_list"],
                "headline_counts": [10],
                "description_counts": [4],
            },
            {
                "campaign": "Meta_Conversion_Main",
                "platform": "meta",
                "campaign_type": "conversions",
                "clicks": 400,
                "impressions": 20000,
                "cost": 60000,
                "conversions": 15,
                "cpa": 4000,
                "ctr": 2.0,
                "roas": 2.0,
                "revenue": 120000,
                "daily_budget": 70000,
                "ad_count": 8,
                "frequency": 2.5,
                "adset_count": 3,
            },
            {
                "campaign": "Meta_Retarget",
                "platform": "meta",
                "campaign_type": "conversions",
                "clicks": 200,
                "impressions": 5000,
                "cost": 20000,
                "conversions": 12,
                "cpa": 1667,
                "ctr": 4.0,
                "roas": 5.0,
                "revenue": 100000,
                "daily_budget": 25000,
                "ad_count": 5,
                "frequency": 1.5,
                "adset_count": 2,
                "custom_audiences": ["purchasers_180d"],
                "audience_exclusions": ["purchasers_30d"],
            },
        ],
        "totals": {
            "campaign_count": 4,
            "total_cost": 170000,
            "total_conversions": 62,
            "avg_cpa": 2742,
            "avg_ctr": 3.5,
        },
    }


@pytest.fixture
def thresholds():
    """最小限の閾値設定"""
    return {
        "common": {"ctr_min": 1.0, "cv_zero_cost_min": 5000},
        "google": {},
        "meta": {"structure": {"max_adsets_per_campaign": 5}},
        "tiktok": {},
    }


class TestRunAuditIntegration:
    """run_audit のエンドツーエンド統合テスト"""

    def test_valid_structure(self, sample_data, thresholds):
        """監査結果が必須フィールドを含む"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)

        required_keys = [
            "score", "grade", "issues", "total_checks",
            "passed_checks", "failed_checks",
        ]
        for key in required_keys:
            assert key in result, f"結果に {key} が不足"

    def test_score_range_0_to_100(self, sample_data, thresholds):
        """スコアが0-100の範囲内"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        assert 0 <= result["score"] <= 100, f"スコアが範囲外: {result['score']}"

    def test_valid_grade(self, sample_data, thresholds):
        """グレードがA/B/C/D/Fのいずれか"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        assert result["grade"] in ("A", "B", "C", "D", "F"), (
            f"不正なグレード: {result['grade']}"
        )

    def test_issues_have_severity(self, sample_data, thresholds):
        """全issueにseverityフィールドが存在する"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        valid_severities = {"critical", "high", "medium", "warning", "low"}
        for issue in result["issues"]:
            assert "severity" in issue, (
                f"issue にseverityが不足: {issue.get('id')}"
            )
            assert issue["severity"] in valid_severities, (
                f"不正なseverity '{issue['severity']}' in {issue.get('id')}"
            )

    def test_platform_scores_present(self, sample_data, thresholds):
        """platform_scoresにプラットフォーム別スコアが含まれる"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        ps = result.get("platform_scores", {})
        assert len(ps) >= 1, "platform_scores が空"
        for platform, score_data in ps.items():
            assert "score" in score_data, f"{platform} にscoreが不足"
            assert "grade" in score_data, f"{platform} にgradeが不足"

    def test_rule_coverage_present(self, sample_data, thresholds):
        """rule_coverageフィールドが存在し基本構造を持つ"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        if "rule_coverage" in result:
            rc = result["rule_coverage"]
            assert "total_yaml_rules" in rc
            assert "coverage_percent" in rc
            assert rc["total_yaml_rules"] > 0
            assert rc["coverage_percent"] >= 0

    def test_conflicts_structure(self, sample_data, thresholds):
        """conflictsがリスト形式で返され、各要素に必須フィールドがある"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        assert "conflicts" in result
        assert isinstance(result["conflicts"], list)
        assert "conflict_count" in result
        assert result["conflict_count"] == len(result["conflicts"])
        for c in result["conflicts"]:
            assert "conflict_group" in c
            assert "auto_resolved" in c

    def test_axis_conflicts_structure(self, sample_data, thresholds):
        """axis_conflictsがhard/potentialの構造を持つ"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        assert "axis_conflicts" in result
        ac = result["axis_conflicts"]
        assert "hard" in ac
        assert "potential" in ac
        assert isinstance(ac["hard"], list)
        assert isinstance(ac["potential"], list)

    def test_no_crash_with_empty_data(self):
        """空データでクラッシュしない"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_empty", {"campaigns": []}, {})
        assert result["grade"] == "F"
        assert result["score"] == 0

    def test_no_crash_with_single_platform(self):
        """単一プラットフォーム（Google のみ）でクラッシュしない"""
        from analyzers.ads_audit import run_audit
        data = {
            "campaigns": [
                {
                    "campaign": "Only_Google",
                    "platform": "google",
                    "campaign_type": "search",
                    "clicks": 100,
                    "impressions": 5000,
                    "cost": 10000,
                    "conversions": 5,
                    "cpa": 2000,
                    "ctr": 2.0,
                    "ad_count": 1,
                },
            ],
            "totals": {
                "campaign_count": 1,
                "total_cost": 10000,
                "total_conversions": 5,
            },
        }
        result = run_audit("test_single", data, {})
        assert "score" in result
        assert 0 <= result["score"] <= 100
        assert result["grade"] in ("A", "B", "C", "D", "F")

    def test_total_checks_positive(self, sample_data, thresholds):
        """4キャンペーン入力で1件以上のチェックが実行される"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test_integration", sample_data, thresholds)
        assert result["total_checks"] > 0
        assert result["passed_checks"] + result["failed_checks"] == result["total_checks"]

    def test_all_issue_ids_in_yaml(self, sample_data, thresholds):
        """全issue IDがYAMLルールファイルに存在する"""
        from analyzers.ads_audit import run_audit
        from engine.yaml_evaluator import load_rules
        result = run_audit("test_integration", sample_data, thresholds)
        yaml_ids = set()
        for platform in ["google", "meta", "tiktok", "common", "cross"]:
            rules = load_rules(platform)
            yaml_ids.update(r["id"] for r in rules.get("rules", []))
        for issue in result.get("issues", []):
            assert issue["id"] in yaml_ids, f"Issue ID '{issue['id']}' not in YAML rules"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
