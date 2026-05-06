"""Slack判断フローのテスト"""
import os
import sys
import shutil
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

JUDGMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "judgments")


@pytest.fixture(autouse=True)
def clean_judgments():
    """テスト前後にjudgmentsディレクトリをクリーン"""
    if os.path.exists(JUDGMENTS_DIR):
        shutil.rmtree(JUDGMENTS_DIR)
    os.makedirs(JUDGMENTS_DIR, exist_ok=True)
    yield
    if os.path.exists(JUDGMENTS_DIR):
        shutil.rmtree(JUDGMENTS_DIR)


class TestJudgmentDB:
    """judgment_db.py のテスト"""

    def test_create_and_get(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        db.create_judgment("test_001", "cv_fraud_judgment",
                           {"client_id": "client_a"}, "ts1", "#ch", "2099-01-01T00:00:00")
        record = db.get_judgment("test_001")
        assert record is not None
        assert record["status"] == "pending"
        assert record["category"] == "cv_fraud_judgment"

    def test_resolve(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        db.create_judgment("test_002", "cv_fraud_judgment",
                           {"client_id": "client_a"}, "", "", "2099-01-01T00:00:00")
        db.resolve_judgment("test_002", "block", "user1", "test", "2026-01-01T00:00:00")
        record = db.get_judgment("test_002")
        assert record["status"] == "resolved"
        assert record["action"] == "block"

    def test_pending_list(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        db.create_judgment("p1", "cv_fraud_judgment", {}, "", "", "2099-01-01")
        db.create_judgment("p2", "cv_fraud_judgment", {}, "", "", "2099-01-01")
        db.resolve_judgment("p2", "monitor", "sys", "test", "2026-01-01")
        pending = db.get_pending_judgments()
        assert len(pending) == 1
        assert pending[0]["judgment_id"] == "p1"

    def test_learning_stats(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        for i in range(5):
            db.create_judgment(f"s{i}", "cv_fraud_judgment", {"client_id": "c1"}, "", "", "2099-01-01")
            db.resolve_judgment(f"s{i}", "block", "user", "test", "2026-01-01")
        stats = db.get_learning_stats()
        assert stats["cv_fraud_judgment"]["block"] == 5

    def test_auto_suggestion_insufficient(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        result = db.get_auto_suggestion("cv_fraud_judgment", {"client_id": "c1"})
        assert result is None

    def test_auto_suggestion_with_pattern(self):
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        for i in range(12):
            db.create_judgment(f"a{i}", "cv_fraud_judgment", {"client_id": "c1"}, "", "", "2099-01-01")
            db.resolve_judgment(f"a{i}", "block", "user", "test", "2026-01-01")
        suggestion = db.get_auto_suggestion("cv_fraud_judgment", {"client_id": "c1"})
        assert suggestion == "block"


class TestSlackMessageTemplates:
    """メッセージテンプレートのテスト"""

    def test_cv_fraud_message(self):
        from analyzers.slack_message_templates import build_cv_fraud_judgment_message
        msg = build_cv_fraud_judgment_message(
            "client1", "pub_123", "meta", 0.25, 30, 15, 15, 0.50, 0.55, 0.88,
            ["high_ctr", "low_cvr"], 3000, 100000,
        )
        assert "judgment_id" in msg["metadata"]
        assert msg["metadata"]["category"] == "cv_fraud_judgment"
        assert len(msg["blocks"]) > 0

    def test_new_pattern_message(self):
        from analyzers.slack_message_templates import build_new_pattern_message
        msg = build_new_pattern_message(
            "client1", "click_flood", 0.55, ["camp1", "camp2"],
            {"ctr": 15.0, "cvr": -8.0}, "配信面ブロック推奨",
        )
        assert msg["metadata"]["category"] == "new_pattern_confirmation"

    def test_bid_reset_message(self):
        from analyzers.slack_message_templates import build_bid_reset_message
        msg = build_bid_reset_message(
            "client1", "google", ["c1", "c2"], 0.22, 5000, 3000, "aggressive",
        )
        assert msg["metadata"]["category"] == "bid_reset_approval"


class TestSlackJudgmentOrchestrator:
    """slack_judgment.py の統合テスト"""

    def test_request_cv_fraud_creates_record(self):
        from analyzers.slack_judgment import request_cv_fraud_judgment
        from analyzers.judgment_db import JudgmentDB
        jid = request_cv_fraud_judgment(
            "c1", "pub1", "meta", 0.25, 30, 10, 20, 0.67, 0.45, 0.88,
            ["sig1"], 3000, 50000,
        )
        db = JudgmentDB()
        record = db.get_judgment(jid)
        assert record is not None
        assert record["status"] == "pending"

    def test_handle_response_resolves(self):
        from analyzers.slack_judgment import request_cv_fraud_judgment, handle_judgment_response
        from analyzers.judgment_db import JudgmentDB
        jid = request_cv_fraud_judgment(
            "c1", "pub1", "meta", 0.25, 30, 10, 20, 0.67, 0.45, 0.88,
            ["sig1"], 3000, 50000,
        )
        handle_judgment_response(jid, "block", "cv_fraud_judgment", "U123", "tester")
        db = JudgmentDB()
        record = db.get_judgment(jid)
        assert record["status"] == "resolved"
        assert record["action"] == "block"


class TestSlackInteractionHandler:
    """slack_interaction_handler.py のテスト"""

    def test_app_creation(self):
        """Flask app が作成できる（flask インストール済みの場合）"""
        try:
            from integrations.slack_interaction_handler import create_app
            app = create_app()
            # flask がインストールされていればappは非None
            if app is not None:
                assert hasattr(app, "test_client")
        except ImportError:
            pytest.skip("flask 未インストール")


class TestTwentyCRMNoCredentials:
    """Twenty CRM 未設定時の安全動作テスト"""

    def test_save_action_log_returns_none(self):
        from notifiers.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        assert crm.save_action_log("c1", "test", "google", "title") is None

    def test_get_client_actions_returns_empty(self):
        from notifiers.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        assert crm.get_client_actions("c1") == []

    def test_generate_monthly_report_no_crash(self):
        from notifiers.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        report = crm.generate_monthly_report("c1", "2026-04")
        assert report["client_id"] == "c1"
