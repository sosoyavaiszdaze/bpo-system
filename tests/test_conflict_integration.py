"""conflict_detector 統合テスト — トレードオフ検出・解決・軸矛盾の検証"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDetectConflicts:
    """detect_conflicts のテスト"""

    def test_cpa_vs_volume_detection(self):
        """cpa_vs_volume グループが2件以上で検出される"""
        from engine.conflict_detector import detect_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "Campaign_A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "Campaign_B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_group"] == "cpa_vs_volume"
        assert conflicts[0]["issue_count"] == 2
        assert conflicts[0]["auto_resolved"] is False

    def test_no_conflict_with_single_issue(self):
        """同一グループが1件だけでは矛盾として検出されない"""
        from engine.conflict_detector import detect_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 0

    def test_cross_platform_multiplier(self):
        """複数媒体にまたがるとimpact_scoreが1.5倍になる"""
        from engine.conflict_detector import detect_conflicts, SEVERITY_SCORE
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "meta", "severity": "high"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 1
        # high=6, 2件=12, 2媒体で1.5倍=18
        expected = int((SEVERITY_SCORE["high"] * 2) * 1.5)
        assert conflicts[0]["impact_score"] == expected
        assert set(conflicts[0]["affected_platforms"]) == {"google", "meta"}

    def test_multiple_conflict_groups(self):
        """複数のconflict_groupが同時に検出される"""
        from engine.conflict_detector import detect_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
                {"id": "M-ST3", "conflict_group": "learning_vs_testing",
                 "campaign": "C", "platform": "meta", "severity": "critical"},
                {"id": "M-CR4", "conflict_group": "learning_vs_testing",
                 "campaign": "D", "platform": "meta", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 2
        groups = {c["conflict_group"] for c in conflicts}
        assert groups == {"cpa_vs_volume", "learning_vs_testing"}

    def test_no_conflict_group_field(self):
        """conflict_groupフィールドがないissueは無視される"""
        from engine.conflict_detector import detect_conflicts
        audit = {
            "issues": [
                {"id": "G01", "campaign": "A", "platform": "google", "severity": "low"},
                {"id": "G03", "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        assert len(conflicts) == 0


class TestResolveConflicts:
    """resolve_conflicts のテスト"""

    def test_cpa_minimize_auto_resolves(self):
        """objective=cpa_minimize で自動解決される"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {"objective": "cpa_minimize"})
        assert len(resolved) == 1
        assert resolved[0]["auto_resolved"] is True
        assert resolved[0]["resolution"] is not None
        assert "CPA" in resolved[0]["resolution"]

    def test_cv_maximize_auto_resolves(self):
        """objective=cv_maximize で自動解決される"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {"objective": "cv_maximize"})
        assert resolved[0]["auto_resolved"] is True
        assert "CV" in resolved[0]["resolution"]

    def test_roas_target_auto_resolves(self):
        """objective=roas_target で自動解決される"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {"objective": "roas_target"})
        assert resolved[0]["auto_resolved"] is True
        assert "ROAS" in resolved[0]["resolution"]

    def test_balanced_needs_slack(self):
        """objective=balanced ではSlack経由の確認が必要"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {"objective": "balanced"})
        assert resolved[0]["auto_resolved"] is False
        assert "Slack" in resolved[0]["resolution"]

    def test_default_objective_needs_slack(self):
        """objectiveが未設定の場合もSlack経由の確認が必要"""
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        audit = {
            "issues": [
                {"id": "C05", "conflict_group": "cpa_vs_volume",
                 "campaign": "A", "platform": "google", "severity": "high"},
                {"id": "C07", "conflict_group": "cpa_vs_volume",
                 "campaign": "B", "platform": "google", "severity": "medium"},
            ]
        }
        conflicts = detect_conflicts(audit, {})
        resolved = resolve_conflicts(conflicts, {})
        assert resolved[0]["auto_resolved"] is False


class TestDetectAxisConflicts:
    """detect_axis_conflicts のテスト"""

    def test_hard_conflict_detection(self):
        """不合格のleft/right対立でhard conflict検出"""
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
        assert result["hard"][0]["requires_resolution"] is True
        assert "G31" in result["hard"][0]["left_items"]
        assert "G30" in result["hard"][0]["right_items"]

    def test_potential_when_passed(self):
        """片方がpassedの場合はpotential conflictになる"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G31", "passed": True, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "left"},
            {"id": "G30", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "right"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0
        assert len(result["potential"]) == 1
        assert result["potential"][0]["axis"] == "TO-01"
        assert result["potential"][0]["requires_resolution"] is False

    def test_disabled_excluded(self):
        """enabled=False のルールは矛盾検出から除外される"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G31", "passed": False, "enabled": False,
             "primary_axis": "TO-01", "axis_position": "left"},
            {"id": "G30", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "right"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0

    def test_neutral_no_conflict(self):
        """neutral同士では矛盾にならない"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            {"id": "G01", "passed": False, "enabled": True,
             "primary_axis": "TO-11", "axis_position": "neutral"},
            {"id": "G02", "passed": False, "enabled": True,
             "primary_axis": "TO-11", "axis_position": "neutral"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 0
        assert len(result["potential"]) == 0

    def test_multiple_axes(self):
        """複数の軸でそれぞれ矛盾を検出する"""
        from engine.conflict_detector import detect_axis_conflicts
        details = [
            # TO-01 に hard conflict
            {"id": "R1", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "left"},
            {"id": "R2", "passed": False, "enabled": True,
             "primary_axis": "TO-01", "axis_position": "right"},
            # TO-05 に hard conflict
            {"id": "R3", "passed": False, "enabled": True,
             "primary_axis": "TO-05", "axis_position": "left"},
            {"id": "R4", "passed": False, "enabled": True,
             "primary_axis": "TO-05", "axis_position": "right"},
        ]
        result = detect_axis_conflicts(details)
        assert len(result["hard"]) == 2
        axes = {h["axis"] for h in result["hard"]}
        assert axes == {"TO-01", "TO-05"}

    def test_empty_details(self):
        """空の詳細リストでクラッシュしない"""
        from engine.conflict_detector import detect_axis_conflicts
        result = detect_axis_conflicts([])
        assert result == {"hard": [], "potential": []}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
