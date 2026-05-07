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
            "F-LC-10": {
                "customer_title": "ランディング法令表記",
                "priority": "A",
                "goal_stage": "legal_review",
                "performance_category": ["legal_compliance"],
                "today_action": "LP の特商法表記を確認。",
                "yes_no_question": "全項目表示済ですか?",
                "action_options": {
                    "A": "全項目表示済",
                    "B": "一部不足",
                    "C": "状況不明、確認したい",
                },
            },
            "F-MF-02": {
                "customer_title": "CV イベントの動作確認",
                "priority": "A",
                "goal_stage": "measurement_recovery",
                "performance_category": ["measurement_quality"],
                "today_action": "CV イベントを確認。",
                "yes_no_question": "テストイベントで CV シグナルが届きましたか?",
                "action_options": {"A": "届いた", "B": "届かない", "C": "未確認"},
            },
            "F-MF-08": {
                "customer_title": "アトリビューション設定の確認",
                "priority": "A",
                "goal_stage": "measurement_recovery",
                "performance_category": ["measurement_quality"],
                "today_action": "設定を確認。",
                "yes_no_question": "アトリビューション設定が整合していますか?",
                "action_options": {"A": "整合済み", "B": "見直したい", "C": "現状未確認"},
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

class TestResponseStoreMonotonic:
    """5/8 P3: 古い回答が新しい回答を上書きしない (単調性)"""

    def test_old_after_new_does_not_overwrite(self, isolated_responses_dir):
        """先に新しい A を保存、後から古い B を保存しても A のまま"""
        from engine.chatwork_response_store import save_response, get_active_response

        # 1. 新しい answered_at で A を保存
        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done",
            "raw_message": "F-AH-04 A",
            "chatwork_message_id": "2103768587398557696",
            "answered_at": "2026-05-08T15:00:00+09:00",
        })

        # 2. 古い answered_at で B を保存 (上書きされないはず)
        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "B", "answer_label": "未対応",
            "status": "not_done",
            "raw_message": "F-AH-04 B",
            "chatwork_message_id": "2103700000000000000",
            "answered_at": "2026-05-08T10:00:00+09:00",
        })

        rec = get_active_response("test_client", "F-AH-04")
        assert rec["answer_code"] == "A", \
            f"古い B で上書きされてしまった: {rec['answer_code']}"
        assert rec["status"] == "confirmed_done"

    def test_new_after_old_overwrites(self, isolated_responses_dir):
        """先に古い B を保存、後から新しい A を保存すると A に更新される"""
        from engine.chatwork_response_store import save_response, get_active_response

        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "B", "answer_label": "未対応",
            "status": "not_done",
            "raw_message": "F-AH-04 B",
            "answered_at": "2026-05-08T10:00:00+09:00",
        })
        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done",
            "raw_message": "F-AH-04 A",
            "answered_at": "2026-05-08T15:00:00+09:00",
        })

        rec = get_active_response("test_client", "F-AH-04")
        assert rec["answer_code"] == "A"
        assert rec["status"] == "confirmed_done"

    def test_message_id_tiebreak_when_answered_at_equal(self, isolated_responses_dir):
        """answered_at が同じなら chatwork_message_id (数値) で tie-break"""
        from engine.chatwork_response_store import save_response, get_active_response

        same_ts = "2026-05-08T10:00:00+09:00"
        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done",
            "chatwork_message_id": "2103768587398557696",   # 大 = 新
            "answered_at": same_ts,
        })
        # 同じ timestamp、小さい message_id (古い) → skip されるはず
        save_response("test_client", {
            "rule_id": "F-AH-04",
            "answer_code": "B", "answer_label": "未対応",
            "status": "not_done",
            "chatwork_message_id": "2103700000000000000",   # 小 = 古
            "answered_at": same_ts,
        })

        rec = get_active_response("test_client", "F-AH-04")
        assert rec["answer_code"] == "A", "message_id tie-break が効いていない"

    def test_ingest_sorts_messages_ascending(self, monkeypatch):
        """ingest_chatwork_responses が messages を send_time/message_id 昇順でソートする"""
        from scripts import ingest_chatwork_responses
        from notifiers.chatwork_notifier import ChatWorkClient

        # mock messages: 意図的に降順で渡す → ingest 内でソートされるはず
        messages_unsorted = [
            {"message_id": "300", "send_time": 3000, "body": "F-DG-01 A", "account": {}},
            {"message_id": "100", "send_time": 1000, "body": "F-DG-01 B", "account": {}},
            {"message_id": "200", "send_time": 2000, "body": "F-DG-01 C", "account": {}},
        ]
        sorted_received = []

        def fake_fetch(self, room_id=None, force=1):
            return messages_unsorted

        from engine.chatwork_response_parser import parse_messages_bulk as _orig_parse
        def hooked_parse(messages, rule_messaging, **kwargs):
            # ingest が渡してきた順序を記録
            sorted_received.extend([m["message_id"] for m in messages])
            return _orig_parse(messages, rule_messaging, **kwargs)

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)
        monkeypatch.setattr(ingest_chatwork_responses, "load_latest_context", lambda client_id: {
            "message_id": "0",
            "displayed_rule_ids": ["F-DG-01"],
        })
        monkeypatch.setattr(
            "scripts.ingest_chatwork_responses.parse_messages_bulk", hooked_parse,
        )

        ingest_chatwork_responses.ingest("pilotton", dry_run=True)

        # parse_messages_bulk に渡される時点で 100, 200, 300 の昇順
        assert sorted_received == ["100", "200", "300"], \
            f"messages が昇順ソートされていない: {sorted_received}"


class TestParserStatusOrder:
    """5/8 P2 修正: 「未活用、検討したい」が wants_help に正しく分類される"""

    def test_consultation_label_maps_to_wants_help(self, sample_messaging):
        """「未活用、検討したい」は wants_help (旧バグでは not_done に誤分類)"""
        from engine.chatwork_response_parser import parse_message

        results = parse_message("F-DG-01 B", sample_messaging)
        # F-DG-01 B = "未活用、検討したい" → wants_help (検討したい優先)
        assert len(results) == 1
        assert results[0].rule_id == "F-DG-01"
        assert results[0].answer_code == "B"
        assert results[0].status == "wants_help", \
            f"「未活用、検討したい」が wants_help でない: {results[0].status}"

    def test_label_directly_consultation(self, sample_messaging):
        """label を直接書いたケース (例: 「F-DG-01 検討したい」) も wants_help"""
        from engine.chatwork_response_parser import parse_message

        results = parse_message("F-DG-01 検討したい", sample_messaging)
        assert len(results) == 1
        assert results[0].status == "wants_help"


class TestFetchMessagesPropagatesError:
    """5/8 P1-A 修正: ChatWork API エラーは握りつぶさず raise / ingest 失敗"""

    def test_fetch_messages_raises_on_http_error(self, monkeypatch):
        """ChatWorkClient._request が ChatWorkError を raise したら、
        fetch_messages は黙って空 list を返さず例外を上位に伝播する"""
        from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError

        client = ChatWorkClient(api_token="DUMMY", room_id="111")

        def fake_request(method, path, **kw):
            raise ChatWorkError(f"401 Unauthorized: {path}")

        monkeypatch.setattr(client, "_request", fake_request)

        with pytest.raises(ChatWorkError):
            client.fetch_messages()

    def test_ingest_returns_ok_false_on_api_error(self, monkeypatch, tmp_path):
        """ingest スクリプトの ingest() は API エラー時に ok=False を返す"""
        from scripts import ingest_chatwork_responses
        from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError

        # clients.yaml は実物を使う (pilotton chatwork_rooms.main がある前提)。
        # ChatWorkClient.fetch_messages を mock して ChatWorkError を raise
        def fake_fetch(self, room_id=None, force=1):
            raise ChatWorkError("Mock API down")

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)

        summary = ingest_chatwork_responses.ingest("pilotton", dry_run=True)
        assert summary["ok"] is False, \
            f"API エラー時に ok=True で返している (旧バグ): {summary}"
        assert summary["errors"], "errors が空 (API 障害が通知されない)"
        assert "Mock API down" in summary.get("fetch_error", ""), \
            f"fetch_error にエラー詳細が記録されていない: {summary}"


class TestAnswerSourcePreferenceResolver:
    """5/8 P1-B 修正: answer_source_preference が実行経路に乗る"""

    def test_resolve_chatwork_reply_confirmed_done(self, isolated_responses_dir):
        """chatwork_reply で confirmed_done な回答があれば resolved=True"""
        from engine.chatwork_response_store import save_response
        from engine.answer_resolver import resolve_rule_answer

        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "F-AH-04 A",
        })

        msg_def = {"answer_source_preference": ["api", "validator", "chatwork_reply"]}
        result = resolve_rule_answer("test_client", "F-AH-04", msg_def)
        assert result["status"] == "resolved"
        assert result["source"] == "chatwork_reply"
        assert "confirmed_done" in result["reason"] or result["value"] == "confirmed_done"

    def test_resolve_unanswered_returns_manual_required(self, isolated_responses_dir):
        """どの source でも解決できない場合は manual_required"""
        from engine.answer_resolver import resolve_rule_answer

        msg_def = {"answer_source_preference": ["api", "chatwork_reply"]}
        result = resolve_rule_answer("test_client", "UNKNOWN-RULE", msg_def)
        assert result["status"] == "manual_required"
        assert "未解決" in result["reason"]

    def test_resolve_no_preference_returns_unknown(self, isolated_responses_dir):
        """answer_source_preference 未宣言なら unknown"""
        from engine.answer_resolver import resolve_rule_answer

        result = resolve_rule_answer("test_client", "X", {})
        assert result["status"] == "unknown"

    def test_should_suppress_question_when_resolved(self, isolated_responses_dir):
        """resolved な rule は本文から除外 (should_suppress=True)"""
        from engine.chatwork_response_store import save_response
        from engine.answer_resolver import should_suppress_question

        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "test",
        })
        msg_def = {"answer_source_preference": ["chatwork_reply"]}
        suppress, reason = should_suppress_question("test_client", "F-AH-04", msg_def)
        assert suppress is True
        assert "resolved via chatwork_reply" in reason

    def test_daily_todo_uses_resolver(self, isolated_responses_dir, reset_messaging_cache):
        """build_daily_todo が answer_resolver で suppress された rule を本文除外"""
        from engine.chatwork_response_store import save_response
        from engine.daily_todo_builder import build_daily_todo

        save_response("test_client", {
            "rule_id": "F-AH-04", "answer_code": "A", "answer_label": "認証済み",
            "status": "confirmed_done", "raw_message": "test",
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
        assert "F-AH-04" not in ctx["displayed_rule_ids"], \
            "F-AH-04 が answer_resolver で除外されていない"
        # ctx に suppressed_by_resolver_ids が記録されている
        assert "F-AH-04" in ctx.get("suppressed_by_resolver_ids", []) \
            or "F-AH-04" in ctx.get("suppressed_by_response_ids", []), \
            f"suppressed source が記録されていない: {ctx.get('suppressed_by_resolver_ids')}"


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


# ============================================================
# 5/7 P3: Bot 自身の自動通知本文を回答として誤取り込みしない
# ============================================================

# 実本番 Bot 通知本文の代表サンプル (テンプレ抜粋、改変なし)
_BOT_AUTO_NOTIFICATION_BODY = """[info][title]【株式会社パイロットン御中】本日の広告成果改善TODO 2026-05-07（5件）[/title]
CPAが+76.0%上昇、インプレッション-67.9%低下が観測されています。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 今日確認してほしいこと（3 件）
━━━━━━━━━━━━━━━━━━━━━━━━━━━
　【1】Meta ドメイン認証の状態確認

　▼ 期待効果（計測精度改善 / 配信学習安定化）
　　確実: 月 +¥27,194 改善余地

　▼ 今日の確認アクション
　　Meta Business Manager で確認してください。

　▼ ご回答（本スレッドへの返信で 1 文字でご回答ください）
　　Meta Business Manager でドメインが「認証済み」になっていますか?
　　[A] 認証済み
　　[B] 未対応
　　[C] 状況不明、確認したい
　─────────────────────
　ルール ID: F-AH-04（自動診断）
[/info]"""


class TestBotMessageFilter:
    """parse_messages_bulk が Bot 自身の自動通知本文を取り込まない"""

    def test_bot_notification_body_yields_zero_answers(self, sample_messaging):
        """Bot 自動通知本文 (F-AH-04 / [A] 認証済み 等を含む) を渡しても 0 件"""
        from engine.chatwork_response_parser import parse_messages_bulk

        bot_msg = {
            "message_id": "9999",
            "send_time": 1715000000,
            "body": _BOT_AUTO_NOTIFICATION_BODY,
            "account": {"account_id": 12345678, "name": "Zynect Auto-Reporter"},
        }
        results = parse_messages_bulk([bot_msg], sample_messaging)
        assert results == [], \
            f"Bot 通知から回答が抽出されてしまった: {[(r.rule_id, r.answer_code) for r in results]}"

    def test_bot_account_id_filter(self, sample_messaging):
        """body に marker が無くても account_id 一致で Bot と判定されて 0 件"""
        from engine.chatwork_response_parser import parse_messages_bulk

        bot_msg = {
            "message_id": "9999",
            "body": "F-AH-04 A",   # 顧客返信そっくりの本文だが Bot 投稿
            "account": {"account_id": 12345678},
        }
        results = parse_messages_bulk([bot_msg], sample_messaging, bot_account_ids={12345678})
        assert results == [], "account_id 一致で Bot 判定されていない"

    def test_customer_reply_still_parsed_after_bot_filter(self, sample_messaging):
        """Bot 通知 + 顧客返信が混在 → 顧客返信のみ 1 件 parsed"""
        from engine.chatwork_response_parser import parse_messages_bulk

        bot_msg = {
            "message_id": "9999",
            "send_time": 1715000000,
            "body": _BOT_AUTO_NOTIFICATION_BODY,
            "account": {"account_id": 12345678},
        }
        customer_msg = {
            "message_id": "10001",
            "send_time": 1715100000,
            "body": "F-AH-04 A",
            "account": {"account_id": 99999999, "name": "顧客 山田"},
        }
        results = parse_messages_bulk(
            [bot_msg, customer_msg], sample_messaging,
            bot_account_ids={12345678},
        )
        assert len(results) == 1
        assert results[0].rule_id == "F-AH-04"
        assert results[0].answer_code == "A"
        assert results[0].chatwork_message_id == "10001"

    def test_is_bot_message_helper(self):
        """is_bot_message が account_id / body marker のいずれでも True"""
        from engine.chatwork_response_parser import is_bot_message

        # account_id 一致
        assert is_bot_message(
            {"body": "noop", "account": {"account_id": 1}},
            bot_account_ids={1, 2},
        ) is True
        # body marker (▼ ご回答)
        assert is_bot_message(
            {"body": "前略\n▼ ご回答\n[A] 認証済み", "account": {"account_id": 99}},
        ) is True
        # 純粋な顧客返信
        assert is_bot_message(
            {"body": "F-AH-04 A", "account": {"account_id": 99}},
        ) is False

    def test_bot_marker_ruleid_in_body(self, sample_messaging):
        """Bot 通知の「ルール ID: F-AH-04」行から rule_id が誤検出されない"""
        from engine.chatwork_response_parser import parse_messages_bulk

        msg = {
            "message_id": "1",
            "body": "ルール ID: F-AH-04（自動診断）\n[A] 認証済み",
            "account": {"account_id": 0},
        }
        # Body marker (▼ ご回答 が無いがそれでも引っかからないと駄目なので
        # この場合は marker 単体のテスト用に「━━」を含める)
        msg_with_marker = dict(msg)
        msg_with_marker["body"] = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + msg["body"]
        )
        results = parse_messages_bulk([msg_with_marker], sample_messaging)
        assert results == []


# ============================================================
# 5/7 P4: 1 文字返信を直近通知の表示順へ割り当てる
# ============================================================

class TestContextualOneLetterReplies:
    """通知文どおりの `C` / `C、C` 返信を displayed_rule_ids に割り当てる"""

    def test_single_code_maps_to_first_displayed_rule(self, sample_messaging):
        from engine.chatwork_response_parser import parse_messages_bulk

        context = {
            "message_id": "100",
            "displayed_rule_ids": ["F-MF-02", "F-MF-08"],
        }
        msg = {"message_id": "101", "send_time": 1715000001, "body": "C", "account": {"account_id": 9}}
        results = parse_messages_bulk([msg], sample_messaging, reply_context=context)

        assert len(results) == 1
        assert results[0].rule_id == "F-MF-02"
        assert results[0].answer_code == "C"
        assert results[0].answer_label == "未確認"
        assert results[0].status == "not_done"

    def test_multiple_codes_map_in_display_order(self, sample_messaging):
        from engine.chatwork_response_parser import parse_messages_bulk

        context = {
            "message_id": "100",
            "displayed_rule_ids": ["F-MF-02", "F-MF-08"],
        }
        msg = {"message_id": "102", "send_time": 1715000002, "body": "C、C", "account": {"account_id": 9}}
        results = parse_messages_bulk([msg], sample_messaging, reply_context=context)

        assert [(r.rule_id, r.answer_code, r.answer_label) for r in results] == [
            ("F-MF-02", "C", "未確認"),
            ("F-MF-08", "C", "現状未確認"),
        ]

    def test_code_only_before_context_message_is_ignored(self, sample_messaging):
        from engine.chatwork_response_parser import parse_messages_bulk

        context = {
            "message_id": "100",
            "displayed_rule_ids": ["F-MF-02"],
        }
        msg = {"message_id": "99", "send_time": 1715000000, "body": "C", "account": {"account_id": 9}}
        results = parse_messages_bulk([msg], sample_messaging, reply_context=context)

        assert results == []


# ============================================================
# 5/7 P3: F-LC-10 詳細 / F-LC-10 C → wants_help
# ============================================================

class TestIntentFallbackWantsHelp:
    """構造的回答が無くても自由記述の意図表現は wants_help に取り込む"""

    def test_keyword_詳細_maps_to_wants_help(self, sample_messaging):
        """`F-LC-10 詳細` → wants_help (intent fallback)"""
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-LC-10 詳細", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-LC-10"
        assert results[0].status == "wants_help"

    def test_explicit_C_with_action_option_label(self, sample_messaging):
        """`F-LC-10 C` (action_options C = 「状況不明、確認したい」) → wants_help"""
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-LC-10 C", sample_messaging)
        assert len(results) == 1
        assert results[0].rule_id == "F-LC-10"
        assert results[0].answer_code == "C"
        assert results[0].status == "wants_help"

    def test_keyword_相談したい_maps_to_wants_help(self, sample_messaging):
        from engine.chatwork_response_parser import parse_message
        results = parse_message("F-AH-04 相談したい", sample_messaging)
        assert len(results) == 1
        assert results[0].status == "wants_help"


# ============================================================
# 5/7 P3: store は .bak.* を読まない (load_responses は exact filename)
# ============================================================

class TestStoreIgnoresBakFiles:
    def test_bak_file_not_loaded(self, tmp_path, monkeypatch):
        """outputs/chatwork_responses/{client}.yaml.bak.* は load_responses の対象外"""
        import engine.chatwork_response_store as store

        responses_dir = tmp_path / "responses"
        responses_dir.mkdir()
        # bak ファイルだけ存在 (本ファイル は無い)
        bak = responses_dir / "pilotton.yaml.bak.false-positive-20260506-1938"
        bak.write_text(
            "client_id: pilotton\nresponses:\n"
            "  F-AH-04: {rule_id: F-AH-04, answer_code: A, status: confirmed_done}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(store, "RESPONSES_DIR", responses_dir)

        data = store.load_responses("pilotton")
        # bak から復元されていない (空 responses)
        assert data["responses"] == {}, \
            f".bak ファイルから復元されてしまった: {data['responses']}"


# ============================================================
# 5/7 P3: daily_chatwork_check は通知前に ingest を呼ぶ
# ============================================================

class TestDailyCheckIngestsBeforeNotify:
    """daily_chatwork_check.py が通知生成前に ingest_chatwork_responses.ingest を呼ぶ"""

    def test_ingest_called_before_audit(self, monkeypatch, tmp_path):
        """ingest が fetch_audit_results より先に呼ばれる"""
        from scripts import daily_chatwork_check as daily

        call_order: list[str] = []

        def fake_ingest(client_id, dry_run=False, since_id=""):
            call_order.append("ingest")
            return {
                "ok": True, "client_id": client_id,
                "fetched_messages": 0, "parsed_answers": 0, "saved_responses": 0,
                "skipped_by_since_id": 0, "errors": [], "answers_summary": [],
            }

        def fake_fetch(client_id):
            call_order.append("fetch_audit")
            # data_available=False で短絡 (以降の処理は走らないが ingest は先に呼ばれている)
            return {"data_available": False}

        # ingest と audit を mock
        from scripts import ingest_chatwork_responses
        monkeypatch.setattr(ingest_chatwork_responses, "ingest", fake_ingest)
        monkeypatch.setattr(daily, "fetch_audit_results", fake_fetch)

        # IndicationState を tmp_path で隔離
        from engine import indication_state
        monkeypatch.setattr(indication_state, "STATE_DIR", tmp_path / "state")

        daily.run_daily_check(client_id="pilotton", dry_run=True, today="2026-05-07")

        assert "ingest" in call_order, "ingest が呼ばれていない"
        assert "fetch_audit" in call_order, "fetch_audit が呼ばれていない"
        assert call_order.index("ingest") < call_order.index("fetch_audit"), \
            f"ingest が fetch_audit の後に呼ばれている: {call_order}"

    def test_ingest_failure_aborts_notification(self, monkeypatch, tmp_path):
        """ingest が ok=False で返った場合、後段の通知に進まず errors を返す"""
        from scripts import daily_chatwork_check as daily

        post_called = {"v": False}

        def fake_ingest(client_id, dry_run=False, since_id=""):
            return {
                "ok": False,
                "client_id": client_id,
                "fetched_messages": 0, "parsed_answers": 0, "saved_responses": 0,
                "skipped_by_since_id": 0,
                "errors": ["ChatWork API 401 Unauthorized"],
                "fetch_error": "401 Unauthorized",
                "answers_summary": [],
            }

        def fake_fetch(client_id):
            post_called["v"] = True
            return {"data_available": True, "ads_audit": {}, "anomalies": {}, "fraud_audit": {}}

        from scripts import ingest_chatwork_responses
        monkeypatch.setattr(ingest_chatwork_responses, "ingest", fake_ingest)
        monkeypatch.setattr(daily, "fetch_audit_results", fake_fetch)

        from engine import indication_state
        monkeypatch.setattr(indication_state, "STATE_DIR", tmp_path / "state")

        result = daily.run_daily_check(client_id="pilotton", dry_run=True, today="2026-05-07")

        assert post_called["v"] is False, \
            "ingest 失敗時に fetch_audit (通知前段の analyzer) に進んでしまっている"
        assert result.get("errors"), "errors が空 (ingest 失敗が伝わっていない)"
        assert any("ingest" in e for e in result["errors"]), \
            f"errors に ingest 関連が無い: {result['errors']}"

    def test_dry_run_passes_through_to_ingest(self, monkeypatch, tmp_path):
        """daily_check の dry_run=True は ingest にも dry_run=True で伝わり、保存しない"""
        from scripts import daily_chatwork_check as daily

        captured_dry_run = {"v": None}

        def fake_ingest(client_id, dry_run=False, since_id=""):
            captured_dry_run["v"] = dry_run
            return {
                "ok": True, "client_id": client_id,
                "fetched_messages": 0, "parsed_answers": 0, "saved_responses": 0,
                "skipped_by_since_id": 0, "errors": [], "answers_summary": [],
            }

        def fake_fetch(client_id):
            return {"data_available": False}

        from scripts import ingest_chatwork_responses
        monkeypatch.setattr(ingest_chatwork_responses, "ingest", fake_ingest)
        monkeypatch.setattr(daily, "fetch_audit_results", fake_fetch)

        from engine import indication_state
        monkeypatch.setattr(indication_state, "STATE_DIR", tmp_path / "state")

        daily.run_daily_check(client_id="pilotton", dry_run=True, today="2026-05-07")
        assert captured_dry_run["v"] is True, \
            f"daily_check の dry_run=True が ingest に伝わっていない: {captured_dry_run['v']}"

    def test_ingest_dry_run_does_not_save(self, monkeypatch, tmp_path):
        """ingest(dry_run=True) は parse できても save_response を呼ばない"""
        from scripts import ingest_chatwork_responses
        from notifiers.chatwork_notifier import ChatWorkClient
        import engine.chatwork_response_store as store

        responses_dir = tmp_path / "responses"
        monkeypatch.setattr(store, "RESPONSES_DIR", responses_dir)

        def fake_fetch(self, room_id=None, force=1):
            return [
                {"message_id": "1", "send_time": 1715000000, "body": "F-AH-04 A",
                 "account": {"account_id": 99999999}},
            ]

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)
        monkeypatch.setattr(ingest_chatwork_responses, "load_latest_context", lambda client_id: {
            "message_id": "0",
            "displayed_rule_ids": ["F-AH-04"],
        })

        summary = ingest_chatwork_responses.ingest("pilotton", dry_run=True)
        # 1 件 parse、0 件 save (dry_run のため)
        assert summary["parsed_answers"] == 1
        assert summary["saved_responses"] == 0
        # yaml ファイル自体が作られていない
        assert not (responses_dir / "pilotton.yaml").exists(), \
            "dry_run なのに yaml に保存されてしまった"

    def test_ingest_without_reply_context_skips_history(self, monkeypatch, tmp_path):
        """直近TODO文脈が無い場合、古い明示 rule_id 返信を再取り込みしない"""
        from scripts import ingest_chatwork_responses
        from notifiers.chatwork_notifier import ChatWorkClient
        import engine.chatwork_response_store as store

        monkeypatch.setattr(store, "RESPONSES_DIR", tmp_path / "responses")

        def fake_fetch(self, room_id=None, force=1):
            return [
                {"message_id": "2000", "send_time": 1715000000, "body": "F-MF-02 C",
                 "account": {"account_id": 99999999}},
            ]

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)
        monkeypatch.setattr(ingest_chatwork_responses, "load_latest_context", lambda client_id: None)

        summary = ingest_chatwork_responses.ingest("pilotton", dry_run=False)

        assert summary["fetched_messages"] == 1
        assert summary["skipped_by_since_id"] == 1
        assert summary["parsed_answers"] == 0
        assert summary["saved_responses"] == 0
        assert not (tmp_path / "responses" / "pilotton.yaml").exists()


# ============================================================
# 5/7 P4: 顧客回答取り込み後の ACK 自動返信
# ============================================================

class TestChatWorkResponseAck:
    """ingest が保存済み顧客回答に受領返信を返す"""

    def _setup_ack_test(self, monkeypatch, tmp_path, sample_messaging, messages):
        from scripts import ingest_chatwork_responses
        from notifiers.chatwork_notifier import ChatWorkClient
        import engine.chatwork_response_store as response_store
        import engine.chatwork_response_ack_store as ack_store

        monkeypatch.setattr(response_store, "RESPONSES_DIR", tmp_path / "responses")
        monkeypatch.setattr(ack_store, "ACK_DIR", tmp_path / "acks")
        monkeypatch.setattr(ingest_chatwork_responses, "load_messaging", lambda: sample_messaging)
        monkeypatch.setattr(ingest_chatwork_responses, "load_latest_context", lambda client_id: {
            "message_id": "1000",
            "displayed_rule_ids": ["F-AH-04", "F-DG-01", "X-PI1"],
        })

        def fake_fetch(self, room_id=None, force=1):
            return messages

        posted = []

        def fake_post(self, body, room_id=None, idempotency_key=None, self_unread=0):
            posted.append({"body": body, "idempotency_key": idempotency_key})
            return {"message_id": "ack-1"}

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)
        monkeypatch.setattr(ChatWorkClient, "post_message", fake_post)
        return ingest_chatwork_responses, posted, ack_store

    def test_ingest_posts_ack_for_one_letter_reply(self, monkeypatch, tmp_path, sample_messaging):
        """`C、B、C` を保存したら、同じ内容の ACK を 1 通返す"""
        messages = [
            {"message_id": "2000", "send_time": 1715000000, "body": "C、B、C",
             "account": {"account_id": 99999999}},
        ]
        ingest_mod, posted, ack_store = self._setup_ack_test(
            monkeypatch, tmp_path, sample_messaging, messages,
        )

        summary = ingest_mod.ingest("pilotton", dry_run=False)

        assert summary["parsed_answers"] == 3
        assert summary["saved_responses"] == 3
        assert summary["ack_sent"] == 1
        assert len(posted) == 1
        body = posted[0]["body"]
        assert "ご回答ありがとうございます" in body
        assert "ドメイン認証 → 状況不明、確認したい" in body
        assert "1st Party Data → 未活用、検討したい" in body
        assert "Pixel 実装 → Pixel 不在の可能性あり" in body
        assert "2000" in posted[0]["idempotency_key"]
        assert ack_store.load_acked_message_ids("pilotton") == {"2000"}

    def test_ingest_dry_run_does_not_post_ack(self, monkeypatch, tmp_path, sample_messaging):
        """dry-run は保存も ACK 投稿も ACK 済み記録もしない"""
        messages = [
            {"message_id": "2001", "send_time": 1715000000, "body": "F-AH-04 C",
             "account": {"account_id": 99999999}},
        ]
        ingest_mod, posted, ack_store = self._setup_ack_test(
            monkeypatch, tmp_path, sample_messaging, messages,
        )

        summary = ingest_mod.ingest("pilotton", dry_run=True)

        assert summary["parsed_answers"] == 1
        assert summary["saved_responses"] == 0
        assert summary["ack_sent"] == 0
        assert posted == []
        assert ack_store.load_acked_message_ids("pilotton") == set()

    def test_already_acked_message_is_not_posted_again(self, monkeypatch, tmp_path, sample_messaging):
        """同じ ChatWork message_id は再 ingest しても ACK を返さない"""
        messages = [
            {"message_id": "2002", "send_time": 1715000000, "body": "F-AH-04 C",
             "account": {"account_id": 99999999}},
        ]
        ingest_mod, posted, ack_store = self._setup_ack_test(
            monkeypatch, tmp_path, sample_messaging, messages,
        )
        ack_store.mark_acked_message_ids("pilotton", ["2002"])

        summary = ingest_mod.ingest("pilotton", dry_run=False)

        assert summary["parsed_answers"] == 1
        assert summary["ack_sent"] == 0
        assert summary["ack_skipped"] == 1
        assert posted == []


# ============================================================
# 5/7 P3 P2: ingest / audit 失敗時に ChatWork 自己監視通知が飛ぶ
# ============================================================

class TestSelfAlertOnErrors:
    """main() の result.errors 分岐で post_self_alert が呼ばれるか"""

    def _setup_main(self, monkeypatch, sys_argv, run_result):
        """main() を呼ぶ準備: 環境変数 / argv / run_daily_check を mock"""
        from scripts import daily_chatwork_check as daily

        monkeypatch.setenv("CHATWORK_API_TOKEN", "dummy")
        monkeypatch.setenv("CHATWORK_ROOM_ID_PILOTTON", "111")
        monkeypatch.setattr("sys.argv", sys_argv)

        called = {"alert": [], "alert_dry_run": []}

        def fake_run(client_id, dry_run, test_prefix, today):
            return run_result

        def fake_self_alert(message, dry_run=False):
            called["alert"].append(message)
            called["alert_dry_run"].append(dry_run)

        monkeypatch.setattr(daily, "run_daily_check", fake_run)
        monkeypatch.setattr(daily, "post_self_alert", fake_self_alert)
        return daily, called

    def test_ingest_failure_triggers_self_alert(self, monkeypatch):
        """非 dry-run + ingest_failed エラー → post_self_alert 呼ばれる"""
        run_result = {
            "errors": ["ingest_failed: 401 Unauthorized"],
            "posted_indications": 0, "posted_completions": 0,
        }
        daily, called = self._setup_main(
            monkeypatch,
            sys_argv=["daily_chatwork_check.py", "--client", "pilotton"],
            run_result=run_result,
        )
        rc = daily.main()

        assert rc == 3, f"errors あり時 exit code は 3: {rc}"
        assert len(called["alert"]) == 1, \
            f"post_self_alert が呼ばれていない (呼ばれた: {len(called['alert'])} 回)"
        assert "ingest_failed" in called["alert"][0], \
            f"自己監視メッセージに ingest_failed が含まれない: {called['alert'][0]}"
        assert called["alert_dry_run"][0] is False, \
            "self_alert が dry_run=False で呼ばれていない (本番送信されない)"

    def test_dry_run_does_not_self_alert(self, monkeypatch):
        """dry_run + errors → post_self_alert は呼ばれない (副作用ゼロ)"""
        run_result = {
            "errors": ["ingest_failed: 401 Unauthorized"],
            "posted_indications": 0, "posted_completions": 0,
        }
        daily, called = self._setup_main(
            monkeypatch,
            sys_argv=["daily_chatwork_check.py", "--client", "pilotton", "--dry-run"],
            run_result=run_result,
        )
        rc = daily.main()

        assert rc == 3
        assert called["alert"] == [], \
            f"dry_run なのに self_alert が呼ばれた: {called['alert']}"

    def test_no_errors_does_not_self_alert(self, monkeypatch):
        """errors 空 → post_self_alert は呼ばれない (正常系)"""
        run_result = {
            "errors": [],
            "posted_indications": 1, "posted_completions": 0,
        }
        daily, called = self._setup_main(
            monkeypatch,
            sys_argv=["daily_chatwork_check.py", "--client", "pilotton"],
            run_result=run_result,
        )
        rc = daily.main()

        assert rc == 0
        assert called["alert"] == [], \
            f"errors 空なのに self_alert が呼ばれた: {called['alert']}"
