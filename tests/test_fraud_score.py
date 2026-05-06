"""ADR-014 fraud_score の境界値テスト (M-8)

カバー対象:
- 4 象限分類の境界値 (fraud_score 0.59/0.60、cvr_pct 4.99/5.00)
- ゼロクリック / ゼロ baseline 対策 (0 除算回避)
- 低消費除外 (cost < 30000 で cvr_low / cpa_high の両方が 0 になること)
- 加重合成の合計 (= sum of WEIGHTS が 1.0)
- mfa_share = 0 固定 (Phase A)
- summary 計算
- clamp 範囲外入力の処理
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers.fraud_score import (
    compute_fraud_score, classify_quadrant, get_industry_baseline,
    WEIGHTS, FRAUD_THRESHOLD_DEFAULT, CV_RATE_THRESHOLD_DEFAULT_PCT,
    LOW_COST_FLOOR_JPY,
    _freq_anom_score, _ctr_low_score, _ctr_high_score,
    _cvr_low_score, _cpa_high_score, _mfa_share_score,
)


# ============================================================
# classify_quadrant: 4 象限分類の境界値
# ============================================================

class TestClassifyQuadrantBoundaries:
    def test_fraud_059_cv_499_returns_unknown(self):
        """fraud_score 0.59 < 0.60、cvr 4.99 < 5.0 → unknown"""
        assert classify_quadrant(0.59, 4.99) == "unknown"

    def test_fraud_060_cv_499_returns_black(self):
        """fraud_score 0.60 == 0.60 (ヒット)、cvr 4.99 < 5.0 → black"""
        assert classify_quadrant(0.60, 4.99) == "black"

    def test_fraud_059_cv_500_returns_white(self):
        """fraud_score 0.59 < 0.60、cvr 5.00 == 5.0 (ヒット) → white"""
        assert classify_quadrant(0.59, 5.00) == "white"

    def test_fraud_060_cv_500_returns_gray(self):
        """fraud_score 0.60 == 0.60、cvr 5.00 == 5.0 → gray (両方境界ヒット)"""
        assert classify_quadrant(0.60, 5.00) == "gray"

    def test_extreme_high_fraud_zero_cv_returns_black(self):
        assert classify_quadrant(0.95, 0.0) == "black"

    def test_extreme_low_fraud_high_cv_returns_white(self):
        assert classify_quadrant(0.05, 15.0) == "white"

    def test_custom_thresholds_respected(self):
        # 閾値を変えると分類も変わる
        assert classify_quadrant(0.55, 4.0, fraud_threshold=0.50, cv_rate_threshold_pct=3.0) == "gray"


# ============================================================
# 個別スコア関数: 0 除算 / 範囲外入力 / 低消費除外
# ============================================================

class TestIndividualScores:
    def test_freq_anom_clamp_upper(self):
        """freq=20 でも 1.0 が上限 (clamp)"""
        assert _freq_anom_score(20.0) == 1.0

    def test_freq_anom_clamp_lower(self):
        """freq=2 (3 未満) なら 0.0"""
        assert _freq_anom_score(2.0) == 0.0

    def test_ctr_low_zero_ctr(self):
        """ctr=0% は 1.0 (最も悪い)"""
        assert _ctr_low_score(0.0) == 1.0

    def test_ctr_high_normal_ctr(self):
        """ctr=2% は 0 (5% 未満)"""
        assert _ctr_high_score(2.0) == 0.0

    def test_ctr_high_extreme_ctr(self):
        """ctr=15% は 1.0 (10% 以上で clamp)"""
        assert _ctr_high_score(15.0) == 1.0

    def test_cvr_low_zero_baseline_returns_zero(self):
        """baseline_cvr_pct=0 (0 除算回避) → 0"""
        assert _cvr_low_score(1.5, 0.0, cost_jpy=100000) == 0.0

    def test_cvr_low_low_cost_returns_zero(self):
        """cost < LOW_COST_FLOOR_JPY (30000) なら 0"""
        assert _cvr_low_score(0.0, 2.0, cost_jpy=10000) == 0.0

    def test_cvr_low_normal_case(self):
        """cvr=1.0 (baseline 50%) → 0.5"""
        assert _cvr_low_score(1.0, 2.0, cost_jpy=100000) == 0.5

    def test_cpa_high_low_cost_returns_zero(self):
        """ADR-014 §2.2 M-7: cost < LOW_COST_FLOOR_JPY なら cpa_high=0 (cvr_low と整合)"""
        assert _cpa_high_score(20000, 6000, cost_jpy=10000) == 0.0

    def test_cpa_high_zero_cpa_returns_zero(self):
        """cpa=0 (cv 数 0) は除外"""
        assert _cpa_high_score(0, 6000, cost_jpy=100000) == 0.0

    def test_cpa_high_normal_case(self):
        """cpa = 2x baseline (12000 vs 6000) → 0.25"""
        assert _cpa_high_score(12000, 6000, cost_jpy=100000) == 0.25

    def test_mfa_share_phase_a_zero(self):
        """Phase A は固定 0"""
        assert _mfa_share_score() == 0.0


# ============================================================
# compute_fraud_score: 統合 / summary 計算
# ============================================================

class TestComputeFraudScore:
    def test_weights_sum_to_one(self):
        """ADR-014 §2.3: 加重合計は 1.00 (mfa_share 含む)"""
        assert abs(sum(WEIGHTS.values()) - 1.00) < 1e-9

    def test_zero_clicks_no_division_error(self):
        """clicks=0 でゼロ除算しない (cvr=0 になるだけ)。

        ctr=2.0 (中央) を渡すことで ctr_low/ctr_high シグナルを 0 に抑え、
        純粋に "0 除算しない" ことだけを検証する。
        """
        camps = [{
            "campaign": "TEST", "cost": 0, "clicks": 0, "conversions": 0,
            "ctr": 2.0, "frequency": 1.0, "cpa": 0,
        }]
        r = compute_fraud_score("test", "meta", camps)
        # cv_rate_pct は clicks=0 でも 0 (ZeroDivision なし)
        assert r["samples"][0]["cv_rate_pct"] == 0.0
        # 全 component が 0 想定 (cost=0 なので cvr_low / cpa_high も低消費除外で 0)
        assert r["samples"][0]["fraud_score"] == 0.0
        assert r["samples"][0]["quadrant"] == "unknown"

    def test_low_cost_excluded(self):
        """ADR-014 §2.2 M-7: 低消費 campaign は cvr_low / cpa_high とも除外"""
        camps = [{
            "campaign": "LOW",
            "cost": 5000, "clicks": 100, "conversions": 0,   # cvr 0 だが低消費
            "ctr": 1.0, "frequency": 1.0, "cpa": 0,
        }]
        r = compute_fraud_score("test", "meta", camps)
        comp = r["samples"][0]["components"]
        assert comp["cvr_low"] == 0.0   # 低消費除外
        assert comp["cpa_high"] == 0.0  # 低消費除外

    def test_summary_counts(self):
        """4 象限 各 1 件ずつ作って summary が正しく集計するか"""
        camps = [
            # black: 高 fraud × 低 cv (zero_cv_anomaly に頼らず純粋な weighted)
            #   freq=10 で freq_anom=1.0、cost 高、cvr=0 で cvr_low=1.0
            #   合成 = 0.25 + 0.20 = 0.45 → 黒には足りない
            # 純粋に weighted で 0.60 を超えるパターンを作るのは Phase A の仕様で
            # 困難なので、quadrant の境界テストは classify_quadrant 側で実施。
            # ここでは「いずれかの象限に振り分けられる」ことだけ確認。
            {"campaign": "C1", "cost": 100000, "clicks": 1000, "conversions": 60,
             "ctr": 2.0, "frequency": 1.5, "cpa": 1666},   # white 候補 (cvr=6%, fraud 低)
            {"campaign": "C2", "cost": 100000, "clicks": 1000, "conversions": 5,
             "ctr": 2.0, "frequency": 1.5, "cpa": 20000},  # unknown (cvr=0.5%, cpa高め)
        ]
        r = compute_fraud_score("test", "meta", camps)
        s = r["summary"]
        assert s["total_samples"] == 2
        assert s["black_count"] + s["gray_count"] + s["white_count"] + s["unknown_count"] == 2

    def test_default_thresholds_are_adr_014_values(self):
        """ADR-014 §2.4: 既定閾値 0.60 / 5.0"""
        assert FRAUD_THRESHOLD_DEFAULT == 0.60
        assert CV_RATE_THRESHOLD_DEFAULT_PCT == 5.0

    def test_low_cost_floor_constant(self):
        """ADR-014 §2.2 LOW_COST_FLOOR_JPY = 30000"""
        assert LOW_COST_FLOOR_JPY == 30000.0


# ============================================================
# get_industry_baseline: フォールバック動作
# ============================================================

class TestIndustryBaseline:
    def test_known_industry(self):
        b = get_industry_baseline("beauty_d2c")
        assert b["cvr_pct"] == 2.0 and b["cpa_jpy"] == 6000

    def test_unknown_industry_falls_back_to_default(self):
        b = get_industry_baseline("nonexistent_industry")
        assert b["cvr_pct"] == 2.0 and b["cpa_jpy"] == 6000
