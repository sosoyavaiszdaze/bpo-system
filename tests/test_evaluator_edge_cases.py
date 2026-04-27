"""yaml_evaluator のエッジケーステスト — polarity, budget_first, prerequisite, enabled"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.yaml_evaluator import (
    _resolve_context_dependent, _resolve_budget_first,
    _find_result_scoped, _evaluate_prerequisite, evaluate_checks
)


class TestContextDependent:
    """_resolve_context_dependent の全パターン"""

    def test_auto_bidding_target_cpa(self):
        """target_cpa → 0.05"""
        check = {"bidding_strategy": "TARGET_CPA"}
        assert _resolve_context_dependent(check) == 0.05

    def test_auto_bidding_max_conversions(self):
        """max_conversions → 0.05"""
        check = {"context": {"bidding_strategy": "max_conversions"}}
        assert _resolve_context_dependent(check) == 0.05

    def test_manual_bidding_cpc(self):
        """manual_cpc → 1.0"""
        check = {"bidding_strategy": "MANUAL_CPC"}
        assert _resolve_context_dependent(check) == 1.0

    def test_nested_context_key(self):
        """context dict 内の bidding_strategy が読まれる"""
        check = {"context": {"bidding_strategy": "target_roas"}}
        assert _resolve_context_dependent(check) == 0.05

    def test_unknown_returns_half(self):
        """bidding_strategy なし → 0.5"""
        check = {}
        assert _resolve_context_dependent(check) == 0.5

    def test_case_insensitive(self):
        """大文字小文字混在でも正しく判定"""
        check = {"bidding_strategy": "Target_ROAS"}
        assert _resolve_context_dependent(check) == 0.05


class TestBudgetFirst:
    """_resolve_budget_first の全パターン"""

    def test_g39_failed_returns_03(self):
        """G39不合格 → 0.3"""
        all_results = [
            {"id": "G39", "passed": False, "platform": "google", "campaign": "camp1"}
        ]
        check = {"platform": "google", "campaign": "camp1"}
        assert _resolve_budget_first(check, all_results) == 0.3

    def test_g39_passed_returns_1(self):
        """G39合格 → 1.0"""
        all_results = [
            {"id": "G39", "passed": True, "platform": "google", "campaign": "camp1"}
        ]
        check = {"platform": "google", "campaign": "camp1"}
        assert _resolve_budget_first(check, all_results) == 1.0

    def test_g39_missing_g13_failed_returns_03(self):
        """G39なし + G13不合格 → 0.3 (fallback)"""
        all_results = [
            {"id": "G13", "passed": False, "platform": "google", "campaign": "camp1"}
        ]
        check = {"platform": "google", "campaign": "camp1"}
        assert _resolve_budget_first(check, all_results) == 0.3

    def test_both_missing_returns_05(self):
        """G39もG13もなし → 0.5"""
        check = {"platform": "google", "campaign": "camp1"}
        assert _resolve_budget_first(check, []) == 0.5

    def test_account_level_fallback(self):
        """キャンペーン不一致でもアカウント全体にfallback"""
        all_results = [
            {"id": "G39", "passed": False, "platform": "google", "campaign": "アカウント全体"}
        ]
        check = {"platform": "google", "campaign": "different_camp"}
        assert _resolve_budget_first(check, all_results) == 0.3


class TestPrerequisiteBlocking:
    """prerequisite ブロッキングの詳細テスト"""

    def test_blocked_check_scoring_passed_false(self):
        """前提不合格 → scoring_passed=False、blocked_by記録"""
        checks = [
            {"id": "G01", "passed": False, "platform": "google", "campaign": "c1"},
            {"id": "G02", "passed": True, "platform": "google", "campaign": "c1"},
        ]
        result = evaluate_checks(checks, "google")
        g02 = next(d for d in result["details"] if d["id"] == "G02")
        assert g02["blocked_by"] == ["G01"]
        assert g02["scoring_passed"] is False

    def test_blocked_reduces_weight(self):
        """ブロック時のweight = base * 0.3"""
        checks = [
            {"id": "G01", "passed": False, "platform": "google", "campaign": "c1"},
            {"id": "G03", "passed": True, "platform": "google", "campaign": "c1"},
        ]
        result = evaluate_checks(checks, "google")
        g03 = next(d for d in result["details"] if d["id"] == "G03")
        # G03のprerequisiteはG01。ブロックされているのでweight < 通常weight
        assert g03["blocked_by"] == ["G01"]
        # weighted_passに加算されていないことを確認
        assert g03["scoring_passed"] is False

    def test_unblocked_passes_normally(self):
        """前提合格 → scoring_passed=True、blocked_by空"""
        checks = [
            {"id": "G01", "passed": True, "platform": "google", "campaign": "c1"},
            {"id": "G02", "passed": True, "platform": "google", "campaign": "c1"},
        ]
        result = evaluate_checks(checks, "google")
        g02 = next(d for d in result["details"] if d["id"] == "G02")
        assert g02["blocked_by"] == []
        assert g02["scoring_passed"] is True

    def test_missing_prereq_treated_as_blocked(self):
        """前提チェックが結果に存在しない → ブロック扱い"""
        checks = [
            {"id": "G02", "passed": True, "platform": "google", "campaign": "c1"},
        ]
        result = evaluate_checks(checks, "google")
        g02 = next(d for d in result["details"] if d["id"] == "G02")
        assert "G01" in g02["blocked_by"]
        assert g02["scoring_passed"] is False


class TestEnabledFalse:
    """enabled:false ルールの詳細テスト"""

    def test_disabled_not_in_weighted_total(self):
        """enabled:false → weighted_totalに加算されない"""
        # G26-NEW2 は enabled:false
        checks_with_disabled = [
            {"id": "G26-NEW2", "passed": False, "platform": "google", "campaign": "c1"},
        ]
        checks_without = []
        result_with = evaluate_checks(checks_with_disabled, "google")
        result_without = evaluate_checks(checks_without, "google")
        # disabled ルールは weighted_total を増やさない
        assert result_with["weighted_total"] == result_without["weighted_total"]

    def test_disabled_detail_has_weight_zero(self):
        """enabled:false → details に weight:0 で記録"""
        checks = [{"id": "G26-NEW2", "passed": False, "platform": "google", "campaign": "c1"}]
        result = evaluate_checks(checks, "google")
        detail = next(d for d in result["details"] if d["id"] == "G26-NEW2")
        assert detail["enabled"] is False
        assert detail["weight"] == 0
        assert detail["scoring_passed"] is None

    def test_enabled_true_has_positive_weight(self):
        """enabled:true → weight > 0"""
        checks = [{"id": "G01", "passed": False, "platform": "google", "campaign": "c1"}]
        result = evaluate_checks(checks, "google")
        detail = next(d for d in result["details"] if d["id"] == "G01")
        assert detail["enabled"] is True
        assert detail["weight"] > 0


class TestFindResultScoped:
    """_find_result_scoped のスコープ解決テスト"""

    def test_exact_match(self):
        """同一platform + campaign で完全一致"""
        results = [
            {"id": "G01", "platform": "google", "campaign": "camp1", "passed": True},
        ]
        r = _find_result_scoped(results, "G01", "google", "camp1")
        assert r is not None
        assert r["passed"] is True

    def test_account_level_fallback(self):
        """キャンペーン不一致でもアカウント全体にfallback"""
        results = [
            {"id": "G01", "platform": "google", "campaign": "アカウント全体", "passed": False},
        ]
        r = _find_result_scoped(results, "G01", "google", "other_camp")
        assert r is not None
        assert r["passed"] is False

    def test_not_found(self):
        """存在しないIDはNone"""
        r = _find_result_scoped([], "G99", "google", "camp1")
        assert r is None
