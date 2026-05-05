"""ADR-009 CV 保全モニタリングのテスト (5 ケース)"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cv_preservation_monitor import (
    record_block_event,
    monitor_post_block_metrics,
    detect_cv_drop,
    auto_relax_threshold,
    generate_preservation_report,
)


@pytest.fixture
def tmp_output(tmp_path, monkeypatch):
    """OUTPUT_DIR を tmp に差し替え"""
    monkeypatch.setattr("engine.cv_preservation_monitor.OUTPUT_DIR", tmp_path)
    return tmp_path


class TestRecordBlockEvent:
    def test_record_creates_event_id(self, tmp_output):
        """event_id 自動付与 + recorded_at 記録"""
        event = {"media": "meta", "method": "custom_audience_exclusion", "blocked_count": 100}
        result = record_block_event("test_client", event)
        assert "event_id" in result
        assert result["event_id"].startswith("BE-")
        assert "recorded_at" in result

    def test_record_persists_to_yaml(self, tmp_output):
        """outputs/{client}/block_events.yaml に保存"""
        record_block_event("test_client", {"media": "meta", "blocked_count": 50})
        path = tmp_output / "test_client" / "block_events.yaml"
        assert path.exists()
        content = path.read_text()
        assert "events:" in content
        assert "blocked_count: 50" in content


class TestDetectCVDrop:
    def test_detect_cv_drop_below_threshold(self):
        """CV -20% は閾値 15% 超 → True"""
        metrics = {"delta": {"cv_pct": -20, "cpa_pct": 15, "roas_pct": -10}}
        assert detect_cv_drop(metrics, threshold_pct=15) is True

    def test_no_drop_when_within_threshold(self):
        """CV -10% は閾値 15% 以内 → False"""
        metrics = {"delta": {"cv_pct": -10, "cpa_pct": 5, "roas_pct": -3}}
        assert detect_cv_drop(metrics, threshold_pct=15) is False


class TestAutoRelaxThreshold:
    def test_auto_relax_proposes_higher_threshold(self, tmp_output):
        """CV 損失検出時、閾値を厳格化 (高く) する提案"""
        record_block_event("test_client", {
            "event_id": "BE-TEST-001",
            "media": "meta",
            "fraud_score_threshold": 0.70,
        })
        result = auto_relax_threshold("test_client", "BE-TEST-001")
        assert result["current_threshold"] == 0.70
        assert result["suggested_threshold"] > 0.70   # 厳格化方向
        assert result["auto_applied"] is False        # Phase A は提案のみ
