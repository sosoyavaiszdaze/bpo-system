"""BPO System テスト — CSV アダプタ + バリデータ + スコアリング"""
import os
import sys
import pytest

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCSVAdapter:
    """CSV アダプタのテスト"""

    def test_load_csv_basic(self):
        """基本的なCSV読み込み"""
        from adapters.csv_adapter import load_csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        data = load_csv(csv_path)
        assert data is not None
        assert data["source"] == "csv"
        assert len(data["campaigns"]) == 9

    def test_load_csv_fields(self):
        """必須フィールドが存在するか"""
        from adapters.csv_adapter import load_csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        data = load_csv(csv_path)
        camp = data["campaigns"][0]

        required = ["campaign", "platform", "clicks", "impressions", "cost", "conversions", "cpa", "ctr"]
        for field in required:
            assert field in camp, f"Missing field: {field}"

    def test_load_csv_extended_fields(self):
        """拡張 unified format フィールド"""
        from adapters.csv_adapter import load_csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        data = load_csv(csv_path)
        camp = data["campaigns"][0]

        extended = ["status", "bidding_strategy", "daily_budget", "ad_count", "learning_phase"]
        for field in extended:
            assert field in camp, f"Missing extended field: {field}"

    def test_load_csv_numeric(self):
        """数値フィールドが正しい型か"""
        from adapters.csv_adapter import load_csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        data = load_csv(csv_path)
        camp = data["campaigns"][0]

        assert isinstance(camp["cost"], float)
        assert isinstance(camp["conversions"], float)
        assert isinstance(camp["clicks"], float)

    def test_load_csv_totals(self):
        """totals 計算"""
        from adapters.csv_adapter import load_csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        data = load_csv(csv_path)
        totals = data["totals"]

        assert totals["campaign_count"] == 9
        assert totals["total_cost"] > 0
        assert totals["total_conversions"] > 0

    def test_load_csv_not_found(self):
        """存在しないファイル"""
        from adapters.csv_adapter import load_csv
        with pytest.raises(FileNotFoundError):
            load_csv("/nonexistent.csv")


class TestValidator:
    """データバリデータのテスト"""

    def test_validate_none(self):
        """None入力"""
        from adapters.validator import validate_data
        result = validate_data(None)
        assert result is None

    def test_validate_missing_fields(self):
        """不足フィールド補完"""
        from adapters.validator import validate_data
        data = {
            "campaigns": [{"campaign": "test", "cost": 100, "conversions": 5}],
        }
        result = validate_data(data)
        camp = result["campaigns"][0]

        assert "platform" in camp
        assert "cpa" in camp
        assert camp["cpa"] == 20.0  # 100 / 5

    def test_validate_auto_calc_ctr(self):
        """CTR自動計算"""
        from adapters.validator import validate_data
        data = {
            "campaigns": [{"campaign": "test", "clicks": 50, "impressions": 1000}],
        }
        result = validate_data(data)
        assert result["campaigns"][0]["ctr"] == 5.0

    def test_validate_totals_recalc(self):
        """totals 再計算"""
        from adapters.validator import validate_data
        data = {
            "campaigns": [
                {"campaign": "A", "cost": 100, "conversions": 5, "clicks": 50, "impressions": 1000},
                {"campaign": "B", "cost": 200, "conversions": 10, "clicks": 80, "impressions": 2000},
            ],
        }
        result = validate_data(data)
        assert result["totals"]["total_cost"] == 300
        assert result["totals"]["campaign_count"] == 2


class TestScorer:
    """スコアリングエンジンのテスト"""

    def test_grade_boundaries(self):
        """グレード判定境界"""
        from engine.scorer import _get_grade
        assert _get_grade(95) == "A"
        assert _get_grade(90) == "A"
        assert _get_grade(89) == "B"
        assert _get_grade(75) == "B"
        assert _get_grade(60) == "C"
        assert _get_grade(40) == "D"
        assert _get_grade(39) == "F"
        assert _get_grade(0) == "F"

    def test_platform_score(self):
        """プラットフォームスコア計算"""
        from engine.scorer import calc_platform_score
        eval_result = {
            "weighted_pass": 80,
            "weighted_total": 100,
            "by_category": {
                "structure": {"pass": 30, "total": 40, "checks": 5, "passed": 3},
                "bidding": {"pass": 50, "total": 60, "checks": 4, "passed": 4},
            },
        }
        result = calc_platform_score(eval_result)
        assert result["score"] == 80.0
        assert result["grade"] == "B"

    def test_cross_platform_score(self):
        """クロスプラットフォーム集計"""
        from engine.scorer import calc_cross_platform_score
        scores = {
            "google": {"score": 80},
            "meta": {"score": 60},
        }
        result = calc_cross_platform_score(scores)
        assert result["score"] == 70.0  # (80+60)/2
        assert result["grade"] == "C"

    def test_budget_shares(self):
        """予算シェア計算"""
        from engine.scorer import calc_budget_shares
        data = {
            "campaigns": [
                {"platform": "google", "cost": 300},
                {"platform": "meta", "cost": 200},
            ]
        }
        shares = calc_budget_shares(data)
        assert shares["google"] == 0.6
        assert shares["meta"] == 0.4


class TestYAMLEvaluator:
    """YAML ルール評価エンジンのテスト"""

    def test_load_google_rules(self):
        """Googleルール読み込み"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("google")
        assert "rules" in rules
        assert len(rules["rules"]) > 0

    def test_load_meta_rules(self):
        """Metaルール読み込み"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("meta")
        assert len(rules["rules"]) > 0

    def test_load_tiktok_rules(self):
        """TikTokルール読み込み"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("tiktok")
        assert len(rules["rules"]) > 0

    def test_evaluate_checks(self):
        """チェック結果評価"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [
            {"id": "G01", "passed": True, "platform": "google"},
            {"id": "G05", "passed": False, "platform": "google"},
        ]
        result = evaluate_checks(checks, "google")
        assert result["weighted_total"] > 0
        assert len(result["details"]) == 2


class TestCommonChecks:
    """共通チェックのテスト"""

    def test_ctr_check(self):
        """CTR最低基準チェック"""
        from analyzers.checks.common import run_common_checks
        campaigns = [{"campaign": "test", "platform": "google", "ctr": 0.5, "impressions": 1000,
                       "cost": 100, "conversions": 0}]
        thresholds = {"common": {"ctr_min": 1.0}}
        results = run_common_checks(campaigns, thresholds)
        ctr_checks = [r for r in results if r["id"] == "C01"]
        assert len(ctr_checks) > 0
        assert ctr_checks[0]["passed"] is False

    def test_zero_cv_check(self):
        """ゼロCVチェック"""
        from analyzers.checks.common import run_common_checks
        campaigns = [{"campaign": "test", "platform": "google", "ctr": 2.0, "impressions": 1000,
                       "cost": 10000, "conversions": 0}]
        thresholds = {"common": {"cv_zero_cost_min": 5000}}
        results = run_common_checks(campaigns, thresholds)
        cv_checks = [r for r in results if r["id"] == "C02"]
        assert len(cv_checks) > 0
        assert cv_checks[0]["passed"] is False


class TestAdsAuditOrchestrator:
    """ads_audit オーケストレータのテスト"""

    def test_full_audit(self):
        """フル監査実行"""
        from adapters.csv_adapter import load_csv
        from analyzers.ads_audit import run_audit
        import yaml

        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "yamamoto_demo_test.csv")
        thr_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "thresholds.yaml")

        data = load_csv(csv_path)
        with open(thr_path, "r") as f:
            thresholds = yaml.safe_load(f)

        result = run_audit("test_client", data, thresholds)

        assert "score" in result
        assert "grade" in result
        assert "issues" in result
        assert 0 <= result["score"] <= 100
        assert result["grade"] in ["A", "B", "C", "D", "F"]
        assert result["total_checks"] > 0

    def test_empty_data(self):
        """空データでの監査"""
        from analyzers.ads_audit import run_audit
        result = run_audit("test", {"campaigns": []}, {})
        assert result["grade"] == "F"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
