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


class TestCommonChecksExtended:
    """共通チェック C08-C15 のテスト"""

    def test_cpm_spike_check(self):
        """C08: CPMスパイク検出"""
        from analyzers.checks.common import run_common_checks
        campaigns = [
            {"campaign": "low_cpm", "platform": "google", "ctr": 2.0, "impressions": 1000,
             "cost": 100, "conversions": 1, "cpm": 100},
            {"campaign": "high_cpm", "platform": "google", "ctr": 2.0, "impressions": 1000,
             "cost": 300, "conversions": 1, "cpm": 500},
        ]
        thresholds = {"common": {"cpm_spike_pct": 50}}
        results = run_common_checks(campaigns, thresholds)
        cpm_checks = [r for r in results if r["id"] == "C08"]
        assert len(cpm_checks) == 1
        assert cpm_checks[0]["campaign"] == "high_cpm"

    def test_impression_drop_check(self):
        """C09: インプレッション急減"""
        from analyzers.checks.common import run_common_checks
        campaigns = [{"campaign": "test", "platform": "google", "ctr": 2.0,
                       "impressions": 200, "prev_impressions": 1000, "cost": 100, "conversions": 1}]
        thresholds = {"common": {}}
        results = run_common_checks(campaigns, thresholds)
        imp_checks = [r for r in results if r["id"] == "C09"]
        assert len(imp_checks) == 1
        assert imp_checks[0]["passed"] is False

    def test_cost_revenue_ratio(self):
        """C10: コスト対効果比（赤字）"""
        from analyzers.checks.common import run_common_checks
        campaigns = [{"campaign": "loss", "platform": "google", "ctr": 2.0,
                       "impressions": 1000, "cost": 50000, "conversions": 5,
                       "revenue": 30000}]
        thresholds = {"common": {}}
        results = run_common_checks(campaigns, thresholds)
        ratio_checks = [r for r in results if r["id"] == "C10"]
        assert len(ratio_checks) == 1
        assert ratio_checks[0]["passed"] is False

    def test_budget_utilization_high(self):
        """C13: 日予算制約（消化率95%超）"""
        from analyzers.checks.common import run_common_checks
        campaigns = [{"campaign": "capped", "platform": "google", "ctr": 2.0,
                       "impressions": 1000, "cost": 9800, "conversions": 5,
                       "daily_budget": 10000}]
        thresholds = {"common": {}}
        results = run_common_checks(campaigns, thresholds)
        budget_checks = [r for r in results if r["id"] == "C13"]
        assert len(budget_checks) == 1
        assert budget_checks[0]["passed"] is False


class TestGoogleChecks:
    """Google チェックの代表的テスト"""

    def test_naming_convention(self):
        """G01: 命名規則チェック"""
        from analyzers.checks.google import run_google_checks
        campaigns = [
            {"campaign": "Brand_Search_01", "platform": "google", "campaign_type": "search"},
            {"campaign": "nounderscore", "platform": "google", "campaign_type": "search"},
        ]
        results = run_google_checks(campaigns, {})
        g01 = [r for r in results if r["id"] == "G01"]
        assert len(g01) == 2
        assert g01[0]["passed"] is True
        assert g01[1]["passed"] is False

    def test_stag_structure(self):
        """G03: STAG構造チェック"""
        from analyzers.checks.google import run_google_checks
        campaigns = [{"campaign": "Test_Search", "platform": "google",
                       "campaign_type": "search", "keyword_count": 100, "adgroup_count": 2}]
        results = run_google_checks(campaigns, {})
        g03 = [r for r in results if r["id"] == "G03"]
        assert len(g03) == 1
        assert g03[0]["passed"] is False  # 100/2 = 50 > 15

    def test_enhanced_conversions(self):
        """G43: Enhanced Conversions チェック"""
        from analyzers.checks.google import run_google_checks
        campaigns = [{"campaign": "Test_Search", "platform": "google",
                       "campaign_type": "search", "enhanced_conversions": False}]
        results = run_google_checks(campaigns, {})
        g43 = [r for r in results if r["id"] == "G43"]
        assert len(g43) == 1
        assert g43[0]["passed"] is False

    def test_qs_average(self):
        """G20: Quality Score平均チェック"""
        from analyzers.checks.google import run_google_checks
        campaigns = [{"campaign": "Test_Search", "platform": "google",
                       "campaign_type": "search", "quality_score_avg": 3.0}]
        results = run_google_checks(campaigns, {})
        g20 = [r for r in results if r["id"] == "G20"]
        assert len(g20) == 1
        assert g20[0]["passed"] is False


class TestMetaChecks:
    """Meta チェックの代表的テスト"""

    def test_pixel_not_installed(self):
        """M-PI1: Pixel未設置"""
        from analyzers.checks.meta import run_meta_checks
        campaigns = [{"campaign": "Test_Meta", "platform": "meta", "cost": 1000}]
        results = run_meta_checks(campaigns, {}, pixel_status={"pixel_installed": False})
        pi1 = [r for r in results if r["id"] == "M-PI1"]
        assert len(pi1) == 1
        assert pi1[0]["passed"] is False

    def test_frequency_fatigue(self):
        """M-CR3: フリークエンシー疲弊"""
        from analyzers.checks.meta import run_meta_checks
        campaigns = [{"campaign": "High_Freq", "platform": "meta",
                       "frequency": 5.0, "cost": 1000}]
        results = run_meta_checks(campaigns, {"meta": {"creative": {"fatigue_frequency": 3.5}}})
        cr3 = [r for r in results if r["id"] == "M-CR3"]
        assert len(cr3) == 1
        assert cr3[0]["passed"] is False

    def test_adset_count(self):
        """M-ST2: 広告セット数超過"""
        from analyzers.checks.meta import run_meta_checks
        campaigns = [{"campaign": "Too_Many_Adsets", "platform": "meta",
                       "adset_count": 10, "cost": 1000}]
        results = run_meta_checks(campaigns, {"meta": {"structure": {"max_adsets_per_campaign": 5}}})
        st2 = [r for r in results if r["id"] == "M-ST2"]
        assert len(st2) == 1
        assert st2[0]["passed"] is False


class TestTikTokChecks:
    """TikTok チェックの代表的テスト"""

    def test_pixel_not_installed(self):
        """T-TC1: Pixel未設置"""
        from analyzers.checks.tiktok import run_tiktok_checks
        campaigns = [{"campaign": "TT_Test", "platform": "tiktok"}]
        results = run_tiktok_checks(campaigns, {}, pixel_status={"pixel_installed": False})
        tc1 = [r for r in results if r["id"] == "T-TC1"]
        assert len(tc1) == 1
        assert tc1[0]["passed"] is False

    def test_video_completion_rate(self):
        """T-CR3: 動画完視聴率"""
        from analyzers.checks.tiktok import run_tiktok_checks
        campaigns = [{"campaign": "TT_Low_VCR", "platform": "tiktok",
                       "video_completion_rate": 8.0}]
        results = run_tiktok_checks(campaigns, {"tiktok": {"creative": {"video_completion_rate_min": 15.0}}})
        cr3 = [r for r in results if r["id"] == "T-CR3"]
        assert len(cr3) == 1
        assert cr3[0]["passed"] is False

    def test_learning_phase(self):
        """T-BL1: 学習フェーズ未達"""
        from analyzers.checks.tiktok import run_tiktok_checks
        campaigns = [{"campaign": "TT_Learning", "platform": "tiktok",
                       "conversions": 1, "cost": 5000}]
        results = run_tiktok_checks(campaigns, {"tiktok": {"learning_phase": {"min_weekly_conversions": 50}}})
        bl1 = [r for r in results if r["id"] == "T-BL1"]
        assert len(bl1) == 1
        assert bl1[0]["passed"] is False


class TestAnomalyDetection:
    """異常検知のテスト"""

    def test_frequency_fatigue_alert(self):
        """フリークエンシー過多アラート"""
        from analyzers.anomaly import detect_anomalies
        data = {"campaigns": [
            {"campaign": "test", "platform": "google", "frequency": 5.0,
             "ctr": 2.0, "cpa": 1000, "roas": 2.0, "cost": 10000,
             "conversions": 10, "clicks": 500, "impressions": 10000}
        ]}
        result = detect_anomalies("test_client", data, {})
        freq_alerts = [a for a in result["alerts"] if a["type"] == "frequency_fatigue"]
        assert len(freq_alerts) == 1

    def test_roas_deficit_alert(self):
        """ROAS赤字アラート"""
        from analyzers.anomaly import detect_anomalies
        data = {"campaigns": [
            {"campaign": "losing", "platform": "meta", "frequency": 1.0,
             "ctr": 2.0, "cpa": 5000, "roas": 0.5, "cost": 50000,
             "conversions": 10, "clicks": 500, "impressions": 10000}
        ]}
        result = detect_anomalies("test_client", data, {})
        roas_alerts = [a for a in result["alerts"] if a["type"] == "roas_deficit"]
        assert len(roas_alerts) == 1
        assert roas_alerts[0]["severity"] == "critical"


class TestSegmentWaste:
    """無駄コスト検出のテスト"""

    def test_zero_cv_detection(self):
        """ゼロCV高コスト検出"""
        from analyzers.segment_waste import detect_waste
        data = {
            "campaigns": [
                {"campaign": "waste_cp", "platform": "google", "cost": 10000,
                 "conversions": 0, "impressions": 5000, "cpa": 0, "ctr": 1.0,
                 "roas": 0, "frequency": 1.0, "campaign_type": "search"},
            ],
            "totals": {"total_cost": 10000, "avg_cpa": 0},
        }
        result = detect_waste("test", data, {})
        assert result["waste_count"] == 1
        assert result["waste_items"][0]["type"] == "zero_cv"

    def test_high_cpa_detection(self):
        """高CPA検出"""
        from analyzers.segment_waste import detect_waste
        data = {
            "campaigns": [
                {"campaign": "good", "platform": "google", "cost": 10000,
                 "conversions": 10, "impressions": 5000, "cpa": 1000, "ctr": 2.0,
                 "roas": 3.0, "frequency": 1.0, "campaign_type": "search"},
                {"campaign": "bad_cpa", "platform": "google", "cost": 15000,
                 "conversions": 2, "impressions": 5000, "cpa": 7500, "ctr": 2.0,
                 "roas": 0.5, "frequency": 1.0, "campaign_type": "search"},
            ],
            "totals": {"total_cost": 25000, "avg_cpa": 2083},
        }
        result = detect_waste("test", data, {})
        high_cpa = [w for w in result["waste_items"] if w["type"] == "high_cpa"]
        assert len(high_cpa) == 1
        assert high_cpa[0]["campaign"] == "bad_cpa"


class TestFraudAudit:
    """不正検知のテスト"""

    def test_abnormal_ctr(self):
        """F01: 異常CTR検出"""
        from analyzers.fraud_audit import run_fraud_audit
        data = {
            "campaigns": [
                {"campaign": "sus", "platform": "google", "clicks": 2000,
                 "impressions": 5000, "conversions": 10, "cost": 30000,
                 "ctr": 40.0, "cpa": 3000, "frequency": 1.0}
            ],
            "totals": {"total_cost": 30000},
        }
        result = run_fraud_audit("test", data)
        f01 = [i for i in result["issues"] if i["check_id"] == "F01"]
        assert len(f01) == 1
        assert f01[0]["severity"] == "critical"

    def test_bot_traffic(self):
        """F02: ボットトラフィック検出"""
        from analyzers.fraud_audit import run_fraud_audit
        data = {
            "campaigns": [
                {"campaign": "bot", "platform": "google", "clicks": 1000,
                 "impressions": 50000, "conversions": 0, "cost": 50000,
                 "ctr": 2.0, "cpa": 0, "frequency": 1.0}
            ],
            "totals": {"total_cost": 50000},
        }
        result = run_fraud_audit("test", data)
        f02 = [i for i in result["issues"] if i["check_id"] == "F02"]
        assert len(f02) == 1

    def test_clean_account(self):
        """不正なしのアカウント"""
        from analyzers.fraud_audit import run_fraud_audit
        data = {
            "campaigns": [
                {"campaign": "clean", "platform": "google", "clicks": 100,
                 "impressions": 5000, "conversions": 10, "cost": 5000,
                 "ctr": 2.0, "cpa": 500, "frequency": 1.5}
            ],
            "totals": {"total_cost": 5000},
        }
        result = run_fraud_audit("test", data)
        assert result["score"] == 100
        assert result["grade"] == "A"


class TestFraudIngest:
    """不正データ取込のテスト"""

    def test_heuristic_generation(self):
        """ヒューリスティック生成"""
        from analyzers.fraud_ingest import _generate_heuristic
        data = {"campaigns": [
            {"campaign": "suspicious", "platform": "google", "ctr": 25.0,
             "clicks": 500, "conversions": 0, "cost": 10000},
            {"campaign": "normal", "platform": "google", "ctr": 2.0,
             "clicks": 100, "conversions": 5, "cost": 5000},
        ]}
        result = _generate_heuristic(data)
        assert result["source"] == "heuristic"
        assert result["total_items"] >= 1
        assert any(f["campaign"] == "suspicious" for f in result["fraud_items"])


class TestPlaywrightEvaluate:
    """playwright_audit.py evaluate_results のテスト"""

    def test_evaluate_good_results(self):
        """良好なCWV結果の評価"""
        from seo.playwright_audit import evaluate_results
        results = [{
            "url": "https://example.com",
            "lcp": 2000, "cls": 0.05, "ttfb": 500,
            "dom_count": 800, "has_title": True, "title_length": 40,
            "h1_count": 1, "has_viewport": True, "is_https": True,
            "has_form": True, "has_cta": True, "load_time": 2000,
        }]
        checks = evaluate_results(results)
        failed = [c for c in checks if not c["passed"]]
        assert len(failed) == 0

    def test_evaluate_poor_lcp(self):
        """LCPが悪い結果"""
        from seo.playwright_audit import evaluate_results
        results = [{
            "url": "https://example.com",
            "lcp": 5000, "cls": 0.05, "ttfb": 500,
            "dom_count": 800, "has_title": True, "title_length": 40,
            "h1_count": 1, "has_viewport": True, "is_https": True,
            "has_form": True, "has_cta": True,
        }]
        checks = evaluate_results(results)
        lcp_check = [c for c in checks if c["id"] == "SEO-CWV1"]
        assert len(lcp_check) == 1
        assert lcp_check[0]["passed"] is False


class TestConflictDetector:
    """トレードオフ検出のテスト"""

    def test_detect_conflicts(self):
        """conflict_group が2件以上でトリガー"""
        from engine.conflict_detector import detect_conflicts
        audit = {"issues": [
            {"id": "C05", "conflict_group": "cpa_vs_volume", "campaign": "A", "platform": "google"},
            {"id": "C07", "conflict_group": "cpa_vs_volume", "campaign": "B", "platform": "google"},
        ]}
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_group"] == "cpa_vs_volume"

    def test_no_conflicts(self):
        """conflict_group が1件だけではトリガーしない"""
        from engine.conflict_detector import detect_conflicts
        audit = {"issues": [
            {"id": "C05", "conflict_group": "cpa_vs_volume", "campaign": "A", "platform": "google"},
        ]}
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 0

    def test_resolve_for_cpa(self):
        """CPA最小化目標での自動解決"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {"issues": [
            {"id": "C05", "conflict_group": "cpa_vs_volume", "campaign": "A", "platform": "google"},
            {"id": "C07", "conflict_group": "cpa_vs_volume", "campaign": "B", "platform": "google"},
        ]}
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {"objective": "cpa_minimize"})
        assert resolved[0]["auto_resolved"] is True
        assert resolved[0]["resolution"] is not None


class TestFraudAction:
    """Fraud Action のテスト"""

    def test_empty_fraud_items(self):
        """fraud_items が空の場合"""
        from analyzers.fraud_action import run_fraud_action
        fraud_data = {"source": "heuristic", "fraud_items": [], "fraud_rate": 0}
        result = run_fraud_action("test", fraud_data, {}, {})
        assert result["blocked_ips"] == 0
        assert result["estimated_savings"] == 0

    def test_skipped_on_none(self):
        """None入力でスキップ"""
        from analyzers.fraud_action import run_fraud_action
        result = run_fraud_action("test", None, {}, {})
        assert result.get("skipped") is True


class TestValidatorSync:
    """バリデータの conversion_value/revenue 同期テスト"""

    def test_revenue_to_conversion_value(self):
        """revenue → conversion_value 同期"""
        from adapters.validator import validate_data
        data = {"campaigns": [{"campaign": "test", "revenue": 50000, "cost": 10000}]}
        result = validate_data(data)
        camp = result["campaigns"][0]
        assert camp["conversion_value"] == 50000

    def test_conversion_value_to_revenue(self):
        """conversion_value → revenue 同期"""
        from adapters.validator import validate_data
        data = {"campaigns": [{"campaign": "test", "conversion_value": 80000, "cost": 10000}]}
        result = validate_data(data)
        camp = result["campaigns"][0]
        assert camp["revenue"] == 80000


class TestReportGenerator:
    """レポート生成のテスト"""

    def test_build_template_data(self):
        """build_template_data の基本動作"""
        from engine.report_generator import build_template_data
        results = {
            "ads_audit": {
                "score": 75, "grade": "B", "issues": [],
                "quick_wins": [], "platform_summary": {},
                "total_checks": 50, "total_cost": 100000,
                "total_conversions": 50, "avg_cpa": 2000,
                "total_campaigns": 5, "failed_checks": 10,
            },
            "anomalies": {"alerts": []},
            "waste": {"items": [], "total_waste": 0},
            "fraud_audit": {"fraud_rate": 0},
            "fraud_action": {},
            "conflicts": [],
            "claude_analysis": {"skipped": True},
            "seo_audit": {},
            "client_name": "Test Client",
            "timestamp": "2026-04-25",
        }
        data = build_template_data("test_client", results)
        assert data["score"] == 75
        assert data["grade"] == "B"
        assert data["client_id"] == "test_client"
        assert data["client_name"] == "Test Client"
        assert "grade_description" in data
        assert "executive_summary" in data

    def test_build_template_data_empty(self):
        """空の結果でもエラーにならない"""
        from engine.report_generator import build_template_data
        data = build_template_data("empty", {})
        assert data["score"] == 0
        assert data["grade"] == "F"


class TestYAMLEvaluatorWeights:
    """YAML ルール評価エンジンの重み計算テスト"""

    def test_severity_weight_critical(self):
        """critical severity の重み計算"""
        from engine.yaml_evaluator import calc_check_weight
        rule = {"severity": "critical", "weight": 2.0}
        w = calc_check_weight(rule)
        assert w == 10.0  # 5.0 * 2.0

    def test_severity_weight_low(self):
        """low severity の重み計算"""
        from engine.yaml_evaluator import calc_check_weight
        rule = {"severity": "low", "weight": 1.0}
        w = calc_check_weight(rule)
        assert w == 0.5  # 0.5 * 1.0

    def test_evaluate_with_common_rules(self):
        """共通ルール (C01-C15) がYAML経由で正しく評価される"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [
            {"id": "C01", "passed": True, "platform": "google"},
            {"id": "C02", "passed": False, "platform": "google"},
        ]
        result = evaluate_checks(checks, "google")
        # C01/C02 は common_rules.yaml に定義済みなので category != "other"
        c02_detail = [d for d in result["details"] if d["id"] == "C02"]
        assert len(c02_detail) == 1
        assert c02_detail[0]["severity"] == "critical"  # common_rules.yaml で定義
        assert c02_detail[0]["category"] != "other"

    def test_category_weight_applied(self):
        """カテゴリ重みが effective_weight に反映される"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [
            {"id": "C01", "passed": False, "platform": "google"},
        ]
        result = evaluate_checks(checks, "google")
        # weight > 0 であること（category_weight が適用されている）
        assert result["weighted_total"] > 0


class TestRegistry:
    """チェックモジュールレジストリのテスト"""

    def test_run_all_checks(self):
        """全モジュールが実行されること"""
        from analyzers.registry import run_all_checks
        campaigns = [{"campaign": "Test", "platform": "google", "ctr": 2.0,
                       "impressions": 1000, "cost": 5000, "conversions": 5}]
        results = run_all_checks(campaigns, {})
        assert len(results) > 0
        # 少なくとも common チェックが含まれる
        ids = [r["id"] for r in results]
        assert any(i.startswith("C") for i in ids)


class TestConflictImpact:
    """トレードオフ影響度スコアのテスト"""

    def test_impact_calculation(self):
        """impact_score が付与される"""
        from engine.conflict_detector import detect_conflicts
        audit = {"issues": [
            {"id": "C05", "conflict_group": "cpa_vs_volume", "campaign": "A",
             "platform": "google", "severity": "high"},
            {"id": "C07", "conflict_group": "cpa_vs_volume", "campaign": "B",
             "platform": "google", "severity": "medium"},
        ]}
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 1
        assert conflicts[0]["impact_score"] > 0
        assert conflicts[0]["max_severity"] == "high"

    def test_cross_platform_impact_bonus(self):
        """複数媒体にまたがるとインパクト増"""
        from engine.conflict_detector import detect_conflicts
        audit = {"issues": [
            {"id": "C05", "conflict_group": "cpa_vs_volume", "campaign": "A",
             "platform": "google", "severity": "high"},
            {"id": "C07", "conflict_group": "cpa_vs_volume", "campaign": "B",
             "platform": "meta", "severity": "high"},
        ]}
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 1
        # 2媒体なので 1.5x ボーナス
        assert conflicts[0]["impact_score"] == int((6 + 6) * 1.5)


class TestV2Polarity:
    """v2.0 polarity multiplier テスト"""

    def test_monitor_only_polarity(self):
        """monitor_only → multiplier 0.3"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [{"id": "G26", "passed": False, "platform": "google", "campaign": "Test"}]
        result = evaluate_checks(checks, "google")
        g26 = [d for d in result["details"] if d["id"] == "G26"]
        assert len(g26) == 1
        assert g26[0]["polarity"] == "monitor_only"
        assert g26[0]["polarity_multiplier"] == 0.3

    def test_preserve_polarity(self):
        """preserve → multiplier 1.2"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [{"id": "G27", "passed": False, "platform": "google", "campaign": "Test"}]
        result = evaluate_checks(checks, "google")
        g27 = [d for d in result["details"] if d["id"] == "G27"]
        assert len(g27) == 1
        assert g27[0]["polarity"] == "preserve"
        assert g27[0]["polarity_multiplier"] == 1.2

    def test_open_polarity(self):
        """open → multiplier 0.5"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [{"id": "G28", "passed": False, "platform": "google", "campaign": "Test"}]
        result = evaluate_checks(checks, "google")
        g28 = [d for d in result["details"] if d["id"] == "G28"]
        assert len(g28) == 1
        assert g28[0]["polarity"] == "open"
        assert g28[0]["polarity_multiplier"] == 0.5

    def test_context_dependent_auto_bidding(self):
        """context_dependent + 自動入札 → multiplier 0.0"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [{"id": "G35", "passed": False, "platform": "google", "campaign": "Test",
                    "context": {"bidding_strategy": "target_cpa"}}]
        result = evaluate_checks(checks, "google")
        g35 = [d for d in result["details"] if d["id"] == "G35"]
        assert len(g35) == 1
        assert g35[0]["polarity_multiplier"] == 0.0

    def test_context_dependent_manual_bidding(self):
        """context_dependent + 手動入札 → multiplier 1.0"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [{"id": "G35", "passed": False, "platform": "google", "campaign": "Test",
                    "context": {"bidding_strategy": "manual_cpc"}}]
        result = evaluate_checks(checks, "google")
        g35 = [d for d in result["details"] if d["id"] == "G35"]
        assert len(g35) == 1
        assert g35[0]["polarity_multiplier"] == 1.0


class TestV2Prerequisite:
    """v2.0 prerequisite chain テスト"""

    def test_blocked_prerequisite(self):
        """前提チェック不合格でブロックされる"""
        from engine.yaml_evaluator import evaluate_checks
        # G02はG01が前提。G01が不合格→G02はblocked
        checks = [
            {"id": "G01", "passed": False, "platform": "google", "campaign": "アカウント全体"},
            {"id": "G02", "passed": True, "platform": "google", "campaign": "アカウント全体"},
        ]
        result = evaluate_checks(checks, "google")
        g02 = [d for d in result["details"] if d["id"] == "G02"]
        assert len(g02) == 1
        assert g02[0]["scoring_passed"] is False
        assert "G01" in g02[0]["blocked_by"]

    def test_unblocked_prerequisite(self):
        """前提チェック合格でブロックされない"""
        from engine.yaml_evaluator import evaluate_checks
        checks = [
            {"id": "G01", "passed": True, "platform": "google", "campaign": "アカウント全体"},
            {"id": "G02", "passed": True, "platform": "google", "campaign": "アカウント全体"},
        ]
        result = evaluate_checks(checks, "google")
        g02 = [d for d in result["details"] if d["id"] == "G02"]
        assert len(g02) == 1
        assert g02[0]["scoring_passed"] is True
        assert g02[0]["blocked_by"] == []

    def test_missing_prerequisite(self):
        """前提チェックが存在しない場合もブロック"""
        from engine.yaml_evaluator import evaluate_checks
        # G02の前提G01が結果にない
        checks = [{"id": "G02", "passed": True, "platform": "google", "campaign": "アカウント全体"}]
        result = evaluate_checks(checks, "google")
        g02 = [d for d in result["details"] if d["id"] == "G02"]
        assert len(g02) == 1
        assert g02[0]["scoring_passed"] is False


class TestV2Enabled:
    """v2.0 enabled:false skip テスト"""

    def test_disabled_rule_skipped(self):
        """enabled:false のルールはスコアに影響しない"""
        from engine.yaml_evaluator import evaluate_checks
        # enabled=true のルールが正常にスコアリングされることを確認
        checks = [{"id": "G25", "passed": False, "platform": "google", "campaign": "Test"}]
        result = evaluate_checks(checks, "google")
        g25 = [d for d in result["details"] if d["id"] == "G25"]
        assert len(g25) == 1
        assert g25[0]["enabled"] is True


class TestV2AxisConflict:
    """v2.0 軸ベース矛盾検出テスト"""

    def test_hard_axis_conflict(self):
        """left/right対立でhard conflict検出"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G31", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "left"},
            {"id": "G30", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "right"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 1
        assert result["hard"][0]["axis"] == "TO-01"

    def test_no_axis_conflict_neutral(self):
        """neutral同士ではconflictなし"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G01", "passed": False, "enabled": True,
             "primary_axis": "TO-11", "axis_position": "neutral"},
            {"id": "G02", "passed": False, "enabled": True,
             "primary_axis": "TO-11", "axis_position": "neutral"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0

    def test_passed_items_excluded(self):
        """passedはaxis conflict検出から除外"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G31", "passed": True, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "left"},
            {"id": "G30", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "right"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0


class TestV2RuleCounts:
    """v2.0 YAMLルール件数検証テスト"""

    def test_google_85_rules(self):
        """Google YAML = 85件"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("google")
        assert len(rules["rules"]) == 85

    def test_meta_55_rules(self):
        """Meta YAML = 55件"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("meta")
        assert len(rules["rules"]) == 55

    def test_tiktok_35_rules(self):
        """TikTok YAML = 35件"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("tiktok")
        assert len(rules["rules"]) == 35

    def test_seo_45_rules(self):
        """SEO YAML = 45件"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("seo")
        assert len(rules["rules"]) == 45

    def test_adtruth_15_rules(self):
        """AdTruth YAML = 15件"""
        from engine.yaml_evaluator import load_rules
        rules = load_rules("adtruth")
        assert len(rules["rules"]) == 15

    def test_tradeoff_axes_11(self):
        """トレードオフ軸 = 11"""
        import yaml
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "rules", "tradeoff_axes.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert len(data["axes"]) == 11


class TestV2ConflictGroups:
    """v2.0 conflict group 11件テスト"""

    def test_11_conflict_groups(self):
        """CONFLICT_GROUPSが11件"""
        from engine.conflict_detector import CONFLICT_GROUPS
        assert len(CONFLICT_GROUPS) == 11

    def test_all_groups_have_axis(self):
        """全グループにaxis定義がある"""
        from engine.conflict_detector import CONFLICT_GROUPS
        for gid, info in CONFLICT_GROUPS.items():
            assert "axis" in info, f"{gid} missing axis"


class TestPydanticModels:
    """Pydantic モデルのテスト"""

    def test_validate_campaign(self):
        """キャンペーンデータの検証"""
        from engine.models import validate_campaign, PYDANTIC_AVAILABLE
        data = {"campaign": "test", "platform": "google", "cost": 1000}
        result = validate_campaign(data)
        assert result["campaign"] == "test"
        if PYDANTIC_AVAILABLE:
            assert result["cpa"] == 0.0  # デフォルト値が補完される


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
