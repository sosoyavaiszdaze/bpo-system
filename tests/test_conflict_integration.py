"""conflict_detector の統合テスト"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.conflict_detector import detect_conflicts, resolve_conflicts, detect_axis_conflicts


class TestDetectConflicts:

    def test_detects_cpa_vs_volume(self):
        audit_results = {"issues": [
            {"id": "C05", "severity": "high", "platform": "google", "conflict_group": "cpa_vs_volume"},
            {"id": "C07", "severity": "medium", "platform": "google", "conflict_group": "cpa_vs_volume"},
        ]}
        conflicts = detect_conflicts(audit_results, {})
        assert len(conflicts) >= 1
        assert conflicts[0]["conflict_group"] == "cpa_vs_volume"

    def test_no_conflict_with_single_issue(self):
        audit_results = {"issues": [
            {"id": "C05", "severity": "high", "platform": "google", "conflict_group": "cpa_vs_volume"},
        ]}
        assert len(detect_conflicts(audit_results, {})) == 0

    def test_cross_platform_multiplier(self):
        audit_results = {"issues": [
            {"id": "C05", "severity": "high", "platform": "google", "conflict_group": "cpa_vs_volume"},
            {"id": "C07", "severity": "high", "platform": "meta", "conflict_group": "cpa_vs_volume"},
        ]}
        conflicts = detect_conflicts(audit_results, {})
        assert conflicts[0]["impact_score"] > 12


class TestResolveConflicts:

    def test_cpa_minimize_auto_resolves(self):
        conflicts = [{"conflict_group": "cpa_vs_volume", "auto_resolved": False, "resolution": None}]
        resolved = resolve_conflicts(conflicts, {"objective": "cpa_minimize"})
        assert resolved[0]["auto_resolved"] is True

    def test_balanced_needs_slack(self):
        conflicts = [{"conflict_group": "cpa_vs_volume", "auto_resolved": False, "resolution": None}]
        resolved = resolve_conflicts(conflicts, {"objective": "balanced"})
        assert resolved[0]["auto_resolved"] is False
        assert "Slack" in resolved[0]["resolution"]


class TestDetectAxisConflicts:

    def test_hard_conflict_detection(self):
        details = [
            {"id": "G30", "passed": False, "enabled": True, "primary_axis": "TO-01", "axis_position": "right"},
            {"id": "G31", "passed": False, "enabled": True, "primary_axis": "TO-01", "axis_position": "left"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 1

    def test_potential_conflict_when_passed(self):
        details = [
            {"id": "G30", "passed": True, "enabled": True, "primary_axis": "TO-01", "axis_position": "right"},
            {"id": "G31", "passed": True, "enabled": True, "primary_axis": "TO-01", "axis_position": "left"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0
        assert len(result["potential"]) == 1

    def test_disabled_excluded(self):
        details = [
            {"id": "G30", "passed": False, "enabled": False, "primary_axis": "TO-01", "axis_position": "right"},
            {"id": "G31", "passed": False, "enabled": True, "primary_axis": "TO-01", "axis_position": "left"},
        ]
        assert len(detect_axis_conflicts(details)["hard"]) == 0
