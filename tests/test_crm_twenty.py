"""Twenty CRM 統合モジュールのテスト"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTwentyCRM:
    """outputs/crm_twenty.py のテスト（API未接続時の動作確認）"""

    def test_init_without_env(self):
        """環境変数未設定でも初期化できる"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM()
        assert crm.api_url == "" or crm.api_url is not None

    def test_save_action_log_no_api(self):
        """API未設定時はNoneを返す（エラーにならない）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        result = crm.save_action_log(
            "test_client", "fraud_block", "google", "テストブロック",
            description="テスト", cost_saved=5000,
        )
        assert result is None

    def test_save_health_snapshot_no_api(self):
        """ヘルススナップショット保存（API未設定）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        result = crm.save_health_snapshot("test_client", {
            "ads_audit": {"score": 75, "grade": "B", "total_campaigns": 5,
                          "total_cost": 100000, "total_conversions": 50, "avg_cpa": 2000,
                          "failed_checks": 10},
            "anomalies": {"alert_count": 2},
            "waste": {"total_waste_cost": 15000},
        })
        assert result is None

    def test_save_fraud_judgment_no_api(self):
        """Fraud判断保存（API未設定）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        result = crm.save_fraud_judgment({
            "judgment_id": "cvj_test_001",
            "category": "cv_fraud_judgment",
            "status": "resolved",
            "action": "block",
            "judge": "tester",
            "metadata": {"client_id": "c1", "platform": "meta"},
            "created_at": "2026-01-01",
            "resolved_at": "2026-01-01",
        })
        assert result is None

    def test_save_rule_change_no_api(self):
        """ルール変更保存（API未設定）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        result = crm.save_rule_change({
            "metric": "ctr", "old_threshold": 0.10,
            "new_threshold": 0.05, "reason": "click_flood detected",
            "confidence": 0.85, "auto_applied": True,
        })
        assert result is None

    def test_save_advisory_comment_no_api(self):
        """アドバイザリーコメント保存（API未設定）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        result = crm.save_advisory_comment(
            "action_001", "yamamoto", "閾値を下げるべき",
            comment_type="advice", suggested_action="threshold_decrease",
        )
        assert result is None

    def test_generate_monthly_report(self):
        """月次レポート生成（クエリ未実装でもクラッシュしない）"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        report = crm.generate_monthly_report("test_client", "2026-04")
        assert report["client_id"] == "test_client"
        assert report["month"] == "2026-04"

    def test_query_methods_return_empty(self):
        """クエリメソッドが空リスト/Noneを返す"""
        from outputs.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        assert crm.get_client_actions("test") == []
        assert crm.get_client_health_history("test") == []
        assert crm.get_monthly_report("test", "2026-04") is None


class TestIndustryThresholds:
    """業界別閾値のテスト"""

    def test_default_industry(self):
        from analyzers.industry_thresholds import apply_dynamic_thresholds
        t = apply_dynamic_thresholds({})
        assert t["industry"] == "default"
        assert t["ip_block_threshold"] == 0.85

    def test_gaming_industry(self):
        from analyzers.industry_thresholds import apply_dynamic_thresholds
        t = apply_dynamic_thresholds({"industry": "gaming"})
        assert t["industry"] == "gaming"
        assert t["ip_block_threshold"] == 0.80

    def test_finance_industry(self):
        from analyzers.industry_thresholds import apply_dynamic_thresholds
        t = apply_dynamic_thresholds({"industry": "finance"})
        assert t["ip_block_threshold"] == 0.90
        assert t["cv_safe_threshold"] == 80

    def test_available_industries(self):
        from analyzers.industry_thresholds import get_available_industries
        industries = get_available_industries()
        assert "gaming" in industries
        assert "default" in industries
        assert len(industries) == 6
