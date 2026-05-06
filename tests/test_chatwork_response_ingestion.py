"""ChatWork 顧客回答取り込み + 次回通知反映のテスト (5/8 v3 ingestion)

カバー要件:
- パーサ: 複数フォーマット対応 (F-AH-04 A / F-AH-04: A / A F-AH-04 / 認証済み 等)
- 複数回答 1 メッセージ ("F-AH-04 A / F-DG-01 B")
- store: status 別 expires_at 自動算出、yaml 永続化、is_suppressed
- daily_todo_builder: confirmed_done で除外、wants_help で title プレフィクス
- answer_source_preference: rule_messaging で正しく解釈
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def isolated_responses_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.chatwork_response_store.RESPONSES_DIR", tmp_path / "responses")
    return tmp_path / "responses"


@pytest.fixture
def reset_messaging_cache():
    from engine.daily_todo_builder import reset_cache
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def sample_messaging():
    """テスト用の最小 rule_messaging dict"""
    return {
        "rules": {
            "F-AH-04": {
                "customer_title": "ドメイン認証",
                "priority": "A",
                "goal_stage": "measurement_recovery",
                "performance_category": ["measurement_quality"],
                "today_action": "確認してください。",
                "yes_no_question": "認証済みですか?",
                "action_options": {"A": "認証済み", "B": "未対応", "C": "状況不明、確認したい"},
            },
            "F-DG-01": {
                "customer_title": "1st Party Data",
                "priority": "B",
                "goal_stage": "first_party_data",
                "performance_category": ["first_party_data"],
                "today_action": "活用方針を確認。",
                "yes_no_question": "活用方針はありますか?",
                "action_options": {"A": "既に活用中", "B": "未活用、検討したい", "C": "活用予定なし"},
            },
            "X-PI1": {
                "customer_title": "Pixel 実装",
                "priority": "A",
                "goal_stage": "measurement_recovery",
                "performance_category": ["measurement_quality"],
                "today_action": "Pixel 状態を確認。",
                "yes_no_question": "アクティブですか?",
                "action_options": {"A": "アクティブ確認済み", "B": "未確認、これから確認する", "C": "Pixel 不在の可能性あり"},
            },
        },
        "category_labels": {"measurement_quality": "計測精度改善", "first_party_data": "1st Party Data 活用"},
        "goal_stage_order": {"measurement_recovery": 1, "first_party_data": 4},
    }


# ============================================================
# Parser
# ============================================================

class TestResponseParser:
    def test_parse_simple_rule_id_code(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 A", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-AH-04"
        assert results[0].answer_code == "A"
        assert results[0].status == "confirmed_done"

    def test_parse_with_colon(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04: A", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-AH-04" and results[0].answer_code == "A"

    def test_parse_full_width_colon(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04:A", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-AH-04"

    def test_parse_code_first(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("A F-AH-04", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-AH-04" and results[0].answer_code == "A"

    def test_parse_label_directly(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 認証済み", sample_messaging)
        assert len(results) == 1
        assert results[0].answer_code == "A"
        assert results[0].answer_label == "認証済み"
        assert results[0].status == "confirmed_done"

    def test_parse_multi_answers_slash(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 A / F-DG-01 B", sample_messaging)
        assert len(results) == 2
        rids = {r.rule_id for r in results}
        assert rids == {"F-AH-04", "F-DG-01"}

    def test_parse_multi_answers_newline(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 A\nF-DG-01 C", sample_messaging)
        assert len(results) == 2
        f_dg = next(r for r in results if r.rule_id == "F-DG-01")
        assert f_dg.answer_code == "C"
        assert f_dg.status == "not_applicable"   # "活用予定なし" → not_applicable

    def test_parse_x_pi1_anomaly_rules(self, sample_messaging):
        """Layer A 系の rule_id (X-PI1) が認識される"""
        from engine.chatwork_response_parser import parse_message
        results = parse_message("X-PI1 A", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "X-PI1"

    def test_parse_label_wants_help(self, sample_messaging):
        """「確認したい」系の label は wants_help"""
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 状況不明、確認したい", sample_messaging)
        assert len(results) == 1
        assert results[0].status == "wants_help"


# ============================================================
# Store
# ============================================================

class TestResponseStore:
    def test_save_and_load(self, isolated_responses_dir):
        from engine.chatwork_response_store import save_response, load_responses

        rec = {
            "rule_id": "F-AH-04",
            "answer_code": "A",
            "answer_label": "認証済み",
            "status": "confirmed_done",
            "raw_message": "F-AH-04 A",
            "chatwork_message_id": "12345",
            "answered_at": "2026-05-08T10:25:00+09:00",
        }
        save_response("test_client", rec)

        data = load_responses("test_client")
        assert "F-AH-04" in data["responses"]
        saved = data["responses"]["F-AH-04"]
        assert saved["status"] == "confirmed_done"
        # expires_at が自動算出される
        assert saved["expires_at"]

    def test_expires_at_status_specific(self, isolated_responses_dir):
        from engine.chatwork_response_store import save_response, load_responses
        from datetime import datetime, timedelta, timezone
        jst = timezone(timedelta(hours=9))

        # confirmed_done = 90 日
        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "test",
            "answered_at": "2026-05-08T10:00:00+09:00",
        })
        data = load_responses("test_client")
        rec = data["responses"]["F-AH-04"]
        expires = datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00"))
        answered = datetime.fromisoformat(rec["answered_at"].replace("Z", "+00:00"))
        assert (expires - answered).days == 90

        # not_done = 7 日
        save_response("test_client", {
            "rule_id": "F-DG-01", "answer_code": "B", "answer_label": "未対応",
            "status": "not_done", "raw_message": "test",
            "answered_at": "2026-05-08T10:00:00+09:00",
        })
        data = load_responses("test_client")
        rec = data["responses"]["F-DG-01"]
        expires = datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00"))
        answered = datetime.fromisoformat(rec["answered_at"].replace("Z", "+00:00"))
        assert (expires - answered).days == 7

    def test_is_suppressed(self, isolated_responses_dir):
        from engine.chatwork_response_store import save_response, is_suppressed

        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "test",
        })
        save_response("test_client", {
            "rule_id": "F-DG-01", "answer_code": "B", "answer_label": "未対応",
            "status": "not_done", "raw_message": "test",
        })

        assert is_suppressed("test_client", "F-AH-04")        # confirmed_done → True
        assert not is_suppressed("test_client", "F-DG-01")    # not_done → False (reminder 対象)
        assert not is_suppressed("test_client", "UNKNOWN")    # 未保存 → False


# ============================================================
# daily_todo_builder integration
# ============================================================

class TestTodoBuilderResponseIntegration:
    def test_confirmed_done_excluded_from_body(
        self, isolated_responses_dir, reset_messaging_cache, monkeypatch,
    ):
        """confirmed_done な rule は本文から除外される"""
        from engine.chatwork_response_store import save_response
        from engine.daily_todo_builder import build_daily_todo

        # F-AH-04 を confirmed_done で保存
        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "F-AH-04 A",
        })

        ctx = build_daily_todo(
            client_id="test_client", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=[],
            eligible_rules=[
                {"id": "F-AH-04", "daily_cap_group": "default", "severity": "high"},
                {"id": "F-DG-01", "daily_cap_group": "default", "severity": "medium"},
            ],
            today_str="2026-05-08",
            already_notified_ids=set(),
        )

        all_displayed = ctx["displayed_rule_ids"]
        assert "F-AH-04" not in all_displayed, \
            f"confirmed_done な rule が本文に出ている: {all_displayed}"
        assert "F-DG-01" in all_displayed
        assert ctx["suppressed_by_response_count"] == 1

    def test_wants_help_adds_prefix(
        self, isolated_responses_dir, reset_messaging_cache,
    ):
        """wants_help な rule は customer_title に [詳細案内] プレフィクスが付く"""
        from engine.chatwork_response_store import save_response
        from engine.daily_todo_builder import build_daily_todo

        save_response("test_client", {
            "rule_id": "F-DG-01", "answer_code": "B", "answer_label": "未活用、検討したい",
            "status": "wants_help", "raw_message": "F-DG-01 B",
        })

        ctx = build_daily_todo(
            client_id="test_client", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=[],
            eligible_rules=[{"id": "F-DG-01", "daily_cap_group": "default", "severity": "medium"}],
            today_str="2026-05-08",
            already_notified_ids=set(),
        )

        f_dg = next(i for i in ctx["items_today"] + ctx["items_this_week"] if i["rule_id"] == "F-DG-01")
        assert f_dg["customer_title"].startswith("[詳細案内]"), \
            f"wants_help プレフィクスが付いていない: {f_dg['customer_title']}"
        assert f_dg["response_status"] == "wants_help"


# ============================================================
# answer_source_preference
# ============================================================

class TestAnswerSourcePreference:
    def test_rule_messaging_yaml_has_answer_source_preference(self):
        """主要 rule に answer_source_preference が宣言されていることを確認"""
        import yaml
        path = Path(__file__).resolve().parent.parent / "config" / "rule_messaging.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = data.get("rules") or {}

        # X-PI1 / F-AH-04 / F-MF-02 / F-DG-01 / F-LC-01 に存在
        for rid in ["X-PI1", "F-AH-04", "F-MF-02", "F-DG-01", "F-LC-01"]:
            rule = rules.get(rid) or {}
            assert "answer_source_preference" in rule, \
                f"{rid} に answer_source_preference が無い"
            pref = rule["answer_source_preference"]
            assert isinstance(pref, list) and len(pref) >= 1
            for src in pref:
                assert src in ("api", "validator", "chatwork_reply"), \
                    f"{rid}: 不正な answer_source: {src}"
