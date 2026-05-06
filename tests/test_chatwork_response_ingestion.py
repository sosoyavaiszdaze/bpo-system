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
        def hooked_parse(messages, rule_messaging):
            # ingest が渡してきた順序を記録
            sorted_received.extend([m["message_id"] for m in messages])
            return _orig_parse(messages, rule_messaging)

        monkeypatch.setattr(ChatWorkClient, "fetch_messages", fake_fetch)
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
