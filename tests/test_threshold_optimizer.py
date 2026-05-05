"""ADR-006/009 閾値最適化エンジンのテスト (5 ケース)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.threshold_optimizer import (
    analyze_fraud_cv_correlation,
    classify_segments,
    optimize_threshold,
    suggest_threshold_update,
)


class TestCorrelationAnalysis:
    def test_analyze_with_empty_data(self):
        """サンプル不在時は summary 全 0"""
        result = analyze_fraud_cv_correlation("test_client", "meta", sample_data=[])
        assert result["summary"]["total_samples"] == 0
        assert result["summary"]["fraud_score_mean"] == 0.0

    def test_analyze_with_sample_data(self):
        """注入サンプルから正しく集計"""
        samples = [
            {"fraud_score": 0.8, "cv_rate": 2},
            {"fraud_score": 0.4, "cv_rate": 8},
            {"fraud_score": 0.9, "cv_rate": 12},
        ]
        result = analyze_fraud_cv_correlation("test_client", "meta", sample_data=samples)
        assert result["summary"]["total_samples"] == 3
        assert 0.69 < result["summary"]["fraud_score_mean"] < 0.71  # 平均 0.7
        assert 7.32 < result["summary"]["cv_rate_mean"] < 7.34


class TestClassifySegments:
    def test_classify_4_quadrants(self):
        """4 象限 (黒/灰/白/無) に分類"""
        correlation_data = {
            "samples": [
                {"fraud_score": 0.8, "cv_rate": 2},   # 黒 (fraud 高 × CV 低)
                {"fraud_score": 0.85, "cv_rate": 10}, # 灰 (両方高)
                {"fraud_score": 0.3, "cv_rate": 8},   # 白 (CV 高、fraud 低)
                {"fraud_score": 0.2, "cv_rate": 1},   # 無
            ]
        }
        result = classify_segments(correlation_data, fraud_threshold=0.6, cv_threshold_pct=5.0)
        assert len(result["black"]) == 1
        assert len(result["grey"]) == 1
        assert len(result["white"]) == 1
        assert len(result["unknown"]) == 1


class TestOptimizeThreshold:
    def test_optimize_with_no_black_returns_safe_default(self):
        """黒サンプル不在時は保守的な default"""
        correlation_data = {"samples": [{"fraud_score": 0.3, "cv_rate": 8}]}
        threshold = optimize_threshold("test", "meta", correlation_data=correlation_data)
        assert threshold == 0.85

    def test_optimize_with_black_and_grey(self):
        """黒と灰の境界線探索 (CV 保全寄り)"""
        correlation_data = {
            "samples": [
                {"fraud_score": 0.85, "cv_rate": 1},  # 黒
                {"fraud_score": 0.90, "cv_rate": 0.5},  # 黒
                {"fraud_score": 0.75, "cv_rate": 12},  # 灰
                {"fraud_score": 0.78, "cv_rate": 10},  # 灰
            ]
        }
        threshold = optimize_threshold("test", "meta", correlation_data=correlation_data)
        # 黒最小 0.85 と灰最大 0.78 の中間 + 安全マージン → 0.81-0.85 程度
        assert 0.80 <= threshold <= 0.95
