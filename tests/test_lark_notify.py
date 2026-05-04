"""Lark通知モジュールのテスト"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLarkCard:
    """Lark Interactive Card 構築テスト"""

    def test_build_card_basic(self):
        from outputs.lark_notify import _build_card
        results = {
            "client_name": "Test Client",
            "ads_audit": {
                "score": 75, "grade": "B",
                "total_checks": 50, "issues": [
                    {"id": "C01", "severity": "high", "issue": "CTR低", "platform": "google"},
                    {"id": "C02", "severity": "critical", "issue": "CVゼロ", "platform": "meta"},
                ],
                "quick_wins": [{"action": "CTR改善"}, {"action": "予算調整"}],
            },
            "anomalies": {"alert_count": 1},
            "waste": {"potential_savings": "¥10,000"},
        }
        card = _build_card("test", results)
        assert card["header"]["title"]["content"] == "📊 Test Client 日次レポート"
        assert card["header"]["template"] == "blue"
        assert len(card["elements"]) >= 1

    def test_suppressed_excluded(self):
        from outputs.lark_notify import _build_card
        results = {
            "ads_audit": {
                "score": 50, "grade": "C",
                "total_checks": 10, "issues": [
                    {"id": "C01", "severity": "critical", "issue": "問題1"},
                    {"id": "C05", "severity": "high", "issue": "抑止済み", "suppressed": True},
                ],
                "quick_wins": [],
            },
            "anomalies": {"alert_count": 0},
            "waste": {},
        }
        card = _build_card("test", results)
        # suppressed はカード内容に含まれない
        card_text = str(card)
        assert "抑止済み" not in card_text

    def test_empty_issues(self):
        from outputs.lark_notify import _build_card
        results = {
            "ads_audit": {"score": 95, "grade": "A", "total_checks": 10, "issues": [], "quick_wins": []},
            "anomalies": {"alert_count": 0},
            "waste": {},
        }
        card = _build_card("test", results)
        assert card["header"]["template"] == "green"

    def test_context_note_included(self):
        from outputs.lark_notify import _build_card
        results = {
            "ads_audit": {
                "score": 60, "grade": "C",
                "total_checks": 10, "issues": [
                    {"id": "G12", "severity": "high", "issue": "Search Partners",
                     "context_note": "意図的設定: 東京限定"},
                ],
                "quick_wins": [],
            },
            "anomalies": {"alert_count": 0},
            "waste": {},
        }
        card = _build_card("test", results)
        card_text = str(card)
        assert "東京限定" in card_text


class TestLarkNotifyNoWebhook:
    """Webhook未設定時の安全動作"""

    def test_no_crash_without_webhook(self):
        from outputs.lark_notify import send_lark_notification
        assert send_lark_notification("test", {}, {"webhook_env": ""}) is None
        assert send_lark_notification("test", {}, {"lark_webhook_env": ""}) is None

    def test_pipeline_notification_routing(self):
        """pipeline.pyの通知ルーティングがlark対応していること"""
        # pipeline.pyにlark_notifyのimportがあることを確認
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline.py")) as f:
            content = f.read()
        assert "lark_notify" in content
        assert "send_lark_notification" in content
