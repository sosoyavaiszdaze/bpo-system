"""v3 エンジン単体テスト — benchmark_compare / impact_estimator / priority_ranker。

Day 5 D11 対応。各モジュール最低3ケース（正常系・境界値・異常系）。
実行: pytest tests/test_v3_engines.py -v
"""
from __future__ import annotations

import pytest

from engine.benchmark_compare import (
    build_chart_data,
    build_health_score_3axis,
    compare_3axis,
    load_benchmarks,
)
from engine.impact_estimator import (
    aggregate_top5_impact,
    build_kpi_projection,
    estimate_for_rule,
)
from engine.priority_ranker import (
    compute_critical_alerts,
    compute_priority_score,
    compute_top_actions,
    load_all_rules,
    load_weights,
)


# =============================================================================
# benchmark_compare
# =============================================================================
class TestBenchmarkCompare:
    def test_normal_higher_is_better_above_zynect(self):
        """CTR が Zynect 推奨を超えた場合は above_zynect ステータス。"""
        bm = load_benchmarks()
        result = compare_3axis("ec_retail", "google_ads", "ctr", current=4.5, bm=bm)
        assert result["status"] == "above_zynect"
        assert result["has_data"] is True
        assert result["higher_is_better"] is True
        assert result["industry_avg"] is not None
        assert result["zynect_recommended"] is not None

    def test_normal_lower_is_better_above_industry(self):
        """CPA は小さい方が良い。Zynect 推奨は超えないが業界平均は下回るケース。"""
        bm = load_benchmarks()
        # ec_retail × google_ads × cpa: industry_avg=45.27 / zynect_rec=30.0
        result = compare_3axis("ec_retail", "google_ads", "cpa", current=35.0, bm=bm)
        assert result["status"] == "above_industry"  # 業界平均より良いがZynect推奨より悪い
        assert result["higher_is_better"] is False

    def test_boundary_no_current_value(self):
        """現状値が None の場合は has_data=False、業界平均は取得済み。"""
        bm = load_benchmarks()
        result = compare_3axis("ec_retail", "google_ads", "ctr", current=None, bm=bm)
        assert result["has_data"] is False
        assert result["industry_avg"] is not None

    def test_abnormal_unknown_industry(self):
        """未知の業界は no_benchmark / has_data=False を返す。"""
        bm = load_benchmarks()
        result = compare_3axis("unknown_industry_xyz", "google_ads", "ctr", current=5.0, bm=bm)
        assert result["status"] == "no_benchmark"
        assert result["has_data"] is False
        assert result["note"] == "業界平均データ未収集"

    def test_chart_data_unavailable_renders_default(self):
        """チャートデータ生成: ベンチマーク無しでも例外を出さない。"""
        bm = load_benchmarks()
        cmp = compare_3axis("unknown_industry_xyz", "google_ads", "ctr", current=5.0, bm=bm)
        chart = build_chart_data(cmp)
        assert chart["available"] is False
        assert chart["current_pct"] is None

    def test_health_score_3axis_with_known_industry(self):
        """Health Score 3軸が ec_retail で取得できる。"""
        result = build_health_score_3axis("ec_retail", current_score=50)
        assert result["current"] == 50
        assert result["zynect_recommended"] is not None  # 80
        # industry_avg は内部集計待ちで null の可能性あり
        assert "current_pct" in result


# =============================================================================
# impact_estimator
# =============================================================================
class TestImpactEstimator:
    def test_normal_with_expected_impact(self):
        """expected_impact がある通常ルールは試算成功。"""
        rule = {
            "id": "G27",
            "severity": "critical",
            "redesign_note": "test",
            "expected_impact": {
                "primary_metric": "spend_efficiency_pct",
                "primary_value": 12,
                "confidence": "high",
                "impact_horizon_weeks": 2,
                "rationale": "test rationale",
            },
        }
        result = estimate_for_rule(rule, monthly_spend_yen=1_000_000)
        assert result["has_estimate"] is True
        assert result["estimated_savings_yen"] == 120_000  # 12% × 1M × 1.0
        assert result["confidence"] == "high"

    def test_boundary_no_expected_impact(self):
        """expected_impact が無いルールは「効果未試算」を返す。"""
        rule = {"id": "X99", "severity": "medium", "redesign_note": "no impact"}
        result = estimate_for_rule(rule)
        assert result["has_estimate"] is False
        assert result["estimated_savings_display"] == "効果未試算"
        assert result["fallback_text"] is not None

    def test_normal_with_current_metrics_campaign(self):
        """current_metrics に campaign_cost があればキャンペーン基準試算が動く。"""
        rule = {
            "id": "G35",
            "severity": "critical",
            "expected_impact": {
                "primary_metric": "cpa_change_pct",
                "primary_value": -10,
                "confidence": "medium",
                "impact_horizon_weeks": 4,
                "rationale": "test",
            },
        }
        result = estimate_for_rule(
            rule,
            monthly_spend_yen=1_000_000,
            current_metrics={"campaign_cost": 200_000},
        )
        # 10% × 200,000 = 20,000 円（キャンペーン基準）
        assert result["estimated_savings_yen"] == 20_000
        assert result["calc_basis"] == "campaign_specific"

    def test_abnormal_invalid_primary_value(self):
        """primary_value が文字列など異常値は savings=0。"""
        rule = {
            "id": "X",
            "severity": "low",
            "expected_impact": {
                "primary_metric": "cpa_change_pct",
                "primary_value": "invalid",
                "confidence": "low",
                "impact_horizon_weeks": 2,
                "rationale": "test",
            },
        }
        result = estimate_for_rule(rule)
        assert result["estimated_savings_yen"] == 0

    def test_aggregate_top5_mixed(self):
        """Top5 の集計で confidence 内訳が正しい。"""
        estimates = [
            {"has_estimate": True, "estimated_savings_yen": 100, "confidence": "high", "impact_horizon_weeks": 4},
            {"has_estimate": True, "estimated_savings_yen": 200, "confidence": "medium", "impact_horizon_weeks": 6},
            {"has_estimate": False},
        ]
        agg = aggregate_top5_impact(estimates)
        assert agg["total_savings_yen"] == 300
        assert agg["confidence_mix"]["high"] == 1
        assert agg["confidence_mix"]["medium"] == 1
        assert agg["rules_with_estimate"] == 2
        assert agg["rules_without_estimate"] == 1
        assert agg["horizon_weeks_max"] == 6

    def test_kpi_projection_cpa_reduction(self):
        """KPI 投影で CPA が削減方向に動く。"""
        audit = {"total_cost": 750_000, "total_conversions": 100, "avg_cpa": 7500}
        agg = {"total_savings_yen": 100_000, "confidence_summary": "中 1件", "horizon_weeks_max": 4}
        proj = build_kpi_projection(audit, agg)
        assert proj["delta"]["monthly_cost"] < 0
        assert proj["projected"]["monthly_cv"] >= proj["current"]["monthly_cv"]


# =============================================================================
# priority_ranker
# =============================================================================
class TestPriorityRanker:
    def test_normal_ranking_quick_win_priority(self):
        """quick_win ありの critical ルールが Top に来る（パターンC: effort 重視）。"""
        weights = load_weights()
        rules = load_all_rules()
        # 既知の rule ID を投入: G27 (critical, qw) と G15 (medium, non-qw)
        detected = ["G27", "G15"]
        top = compute_top_actions(detected, rules, weights, monthly_spend_yen=750_000, max_n=2)
        assert len(top) == 2
        # G27 (critical+qw) が上位に来ること
        assert top[0]["rule_id"] == "G27"
        assert top[0]["quick_win"] is True

    def test_boundary_unknown_rule_skipped(self):
        """未定義の rule_id はスキップされる。"""
        weights = load_weights()
        rules = load_all_rules()
        detected = ["UNKNOWN_RULE_XYZ", "G27"]
        top = compute_top_actions(detected, rules, weights, max_n=5)
        assert len(top) == 1
        assert top[0]["rule_id"] == "G27"

    def test_critical_alerts_max_count(self):
        """Critical Alerts は max_count を超えない。"""
        weights = load_weights()
        rules = load_all_rules()
        # G27 / M01 / M02 / T01 / T12 — 5件 critical
        detected = ["G27", "M01", "M02", "T01", "T12"]
        alerts = compute_critical_alerts(detected, rules, weights, max_n=3)
        assert len(alerts) == 3
        for a in alerts:
            assert a["severity"] == "critical"

    def test_priority_score_quick_win_bonus(self):
        """quick_win=True なら quick_win_bonus が乗る。"""
        weights = load_weights()
        rules = load_all_rules()
        rule_qw = rules.get("G27")  # critical + quick_win
        assert rule_qw is not None
        score = compute_priority_score(rule_qw, weights, monthly_spend_yen=750_000)
        assert score["quick_win"] is True
        assert score["priority_score"] > 0

    def test_priority_score_no_expected_impact_uses_fallback(self):
        """expected_impact 未設定ルールでも score 計算が出来る（フォールバック）。"""
        weights = load_weights()
        # 仮想ルール: expected_impact 無し
        rule = {
            "id": "X99",
            "name": "test",
            "severity": "medium",
            "category": "structure",
            "platform": "common",
            "quick_win": False,
        }
        score = compute_priority_score(rule, weights, monthly_spend_yen=750_000)
        assert score["priority_score"] > 0
        assert score["has_expected_impact"] is False
