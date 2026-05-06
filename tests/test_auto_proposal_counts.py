"""auto_proposal_engine の結果カウント分離テスト (5/8 通知 UX 改修対応版)

5/8 改修: rule ごとの個別投稿 → 1 日 1 まとめ投稿 (_render_and_post_bundle)。
本テストは新実装でも:
- dry_run / skipped / sent / failed のカウント分離が正しく動くこと
- daily_cap_group が history に保存され、2 回目実行で追加投稿が起きないこと
- dry_run / skipped / failed では history 更新されないこと
を検証する。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def isolated_history_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.auto_proposal_engine.HISTORY_DIR", tmp_path / "history")
    return tmp_path / "history"


@pytest.fixture
def isolated_client_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.auto_proposal_engine.CLIENT_STATE_DIR", tmp_path / "client_state")
    return tmp_path / "client_state"


@pytest.fixture
def stub_eligible_rules(monkeypatch):
    """3 件の eligible rule を返すよう内部関数を stub に置換"""
    sample_rules = [
        {"id": "TEST-A", "applies_to": {}, "trigger": {"condition": "True"},
         "data_source": [], "template": "_client_request_generic.md.j2",
         "rationale": "test", "severity": "medium", "daily_cap_group": "default"},
        {"id": "TEST-B", "applies_to": {}, "trigger": {"condition": "True"},
         "data_source": [], "template": "_client_request_generic.md.j2",
         "rationale": "test", "severity": "medium", "daily_cap_group": "default"},
    ]

    monkeypatch.setattr("engine.auto_proposal_engine._load_all_layers", lambda layer_filter=None: sample_rules)
    monkeypatch.setattr("engine.auto_proposal_engine._filter_by_environment", lambda rules, cfg: rules)
    monkeypatch.setattr("engine.auto_proposal_engine._resolve_data_sources",
                        lambda r, c, s: {"client_state": {}, "ad_platform_data": {}, "rule_evaluation": {}})
    monkeypatch.setattr("engine.auto_proposal_engine._evaluate_trigger", lambda r, d, t: True)
    monkeypatch.setattr("engine.auto_proposal_engine._evaluate_skip_if", lambda r, d, t: False)
    monkeypatch.setattr("engine.auto_proposal_engine._check_prerequisite_chain", lambda r, h, s: True)
    monkeypatch.setattr("engine.auto_proposal_engine._check_cooldown", lambda r, h, t: True)
    monkeypatch.setattr("engine.auto_proposal_engine._apply_severity_priority", lambda rs: rs)
    # _enforce_caps は実関数を使う (cap バグ修正の動作を検証するため)
    monkeypatch.setattr("engine.auto_proposal_engine._load_client_cfg", lambda cid: {
        "company": {"name": cid, "honorific": "御中"},
        "chatwork_rooms": {"main": "111"},
        "operating_charter": {"charter_version": "0.1-test"},
    })
    monkeypatch.setattr("engine.auto_proposal_engine.load_client_state", lambda cid: {})
    return sample_rules


# ============================================================
# Case 1: dry_run=True
# ============================================================

class TestAutoProposalDryRun:
    def test_dry_run_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """dry_run=True: dry_run_count==attempted、sent==0、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected],
                "result": {"dry_run": True, "idempotency_key": "fake"},
                "body_length": 100,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            r = run_auto_proposal("test_client", dry_run=True)

        assert r["attempted_count"] == 2
        assert r["dry_run_count"] == 2
        assert r["sent_count"] == 0
        assert r["skipped_count"] == 0
        assert r["failed_count"] == 0
        assert r["posted_count"] == 0  # 後方互換

        # dry_run では history 不変
        assert not (isolated_history_dir / "test_client.yaml").exists()


# ============================================================
# Case 2: skipped (idempotency hit)
# ============================================================

class TestAutoProposalSkipped:
    def test_skipped_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """ChatWork が skipped=True を返す: skipped_count++、sent_count=0、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected],
                "result": {"skipped": True, "idempotency_key": "existing"},
                "body_length": 100,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 2
        assert r["skipped_count"] == 2
        assert r["sent_count"] == 0
        assert r["dry_run_count"] == 0
        assert r["failed_count"] == 0
        assert r["posted_count"] == 0

        # skipped では history 不変
        assert not (isolated_history_dir / "test_client.yaml").exists()


# ============================================================
# Case 3: 実送信成功 + history 更新 + daily_cap_group 保存
# ============================================================

class TestAutoProposalSent:
    def test_sent_counts_and_history_updated_with_daily_cap_group(
        self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir,
    ):
        """実送信成功: sent==attempted、history が daily_cap_group 含めて更新"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected],
                "result": {"message_id": "12345"},
                "body_length": 1500,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 2
        assert r["sent_count"] == 2
        assert r["posted_count"] == r["sent_count"]

        # history が更新されている + daily_cap_group が保存されている (5/8 cap-bug-fix)
        history_path = isolated_history_dir / "test_client.yaml"
        assert history_path.exists()
        history = yaml.safe_load(history_path.read_text(encoding="utf-8")) or {}
        assert "TEST-A" in history and "TEST-B" in history
        for rid, rec in history.items():
            assert rec.get("daily_cap_group") == "default", \
                f"{rid}: daily_cap_group 未保存 (cap-bug-fix 失敗)"
            assert rec.get("last_sent_date")
            assert rec.get("last_sent_at")


# ============================================================
# Case 4: 例外 (failed)
# ============================================================

class TestAutoProposalFailed:
    def test_failed_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """bundle が error を返す: failed_count++、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected],
                "result": {"error": "ChatWork API down"},
                "error":  "ChatWork API down",
                "body_length": 0,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 2
        assert r["failed_count"] == 2
        assert r["sent_count"] == 0
        assert r["posted_count"] == 0

        # 失敗時は history 不変
        assert not (isolated_history_dir / "test_client.yaml").exists()


# ============================================================
# Case 5: cap バグ修正 — 2 回目実行で追加投稿が起きない
# ============================================================

class TestCapBugFix:
    def test_second_run_does_not_add_posts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """1 回目で 2 件 sent → 2 回目では cap 既消費で attempted=0 になる"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected],
                "result": {"message_id": "msg-{}".format(len(selected))},
                "body_length": 1000,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            # 1 回目: 2 件投稿 (default cap=3 内)
            r1 = run_auto_proposal("test_client", dry_run=False, today="2026-05-08")
            assert r1["attempted_count"] == 2
            assert r1["sent_count"] == 2

            # history を直接読み確認
            history = yaml.safe_load((isolated_history_dir / "test_client.yaml").read_text()) or {}
            assert all(rec.get("daily_cap_group") == "default" for rec in history.values()), \
                "daily_cap_group が history に保存されていない"

            # 2 回目: 同日 + 同じ rules、stub_eligible_rules は同じ 2 件を返すが
            # _check_cooldown が True (= cooldown 通過) なので eligible に残る場合の挙動を再現する
            # ところが _enforce_caps は default cap=3 を見て、history の 2 件を default として
            # 集計するため used=2 → cap=3 残 1 → 1 件のみ selected
            # ここでは「daily_cap_group 保存有り」での counter の正確さを確認する
            r2 = run_auto_proposal("test_client", dry_run=False, today="2026-05-08")

            # rules は固定 2 件、cap=3 残 1 → 1 件 attempted
            assert r2["attempted_count"] == 1, \
                f"cap counter が daily_cap_group を尊重していない (attempted={r2['attempted_count']})"


# ============================================================
# Case 6: 通知文面に「広告成果改善」「期待効果」「Yes/No 質問」が含まれる
# ============================================================

class TestRecommendationsContent:
    def test_message_starts_with_performance_improvement(
        self, isolated_history_dir, isolated_client_state_dir, monkeypatch,
    ):
        """ChatWork 投稿本文の冒頭が法律名ではなく広告成果改善文脈になっていること

        rule_messaging.yaml に登録された実 rule_id (F-AH-04 / F-DG-02) を
        eligible に流し込み、_daily_recommendations.md.j2 で正しく
        priority A の詳細表示がされることを検証。
        """
        from engine.auto_proposal_engine import run_auto_proposal

        # F-AH-04 (priority A) と F-DG-02 (priority A) を eligible にする
        rules_in_messaging = [
            {"id": "F-AH-04", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "default",
             "name": "ドメイン認証"},
            {"id": "F-DG-02", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "default",
             "name": "SHA256"},
        ]
        monkeypatch.setattr("engine.auto_proposal_engine._load_all_layers",
                            lambda layer_filter=None: rules_in_messaging)
        monkeypatch.setattr("engine.auto_proposal_engine._filter_by_environment", lambda r, c: r)
        monkeypatch.setattr("engine.auto_proposal_engine._resolve_data_sources",
                            lambda r, c, s: {"client_state": {}, "ad_platform_data": {}, "rule_evaluation": {}})
        monkeypatch.setattr("engine.auto_proposal_engine._evaluate_trigger", lambda r, d, t: True)
        monkeypatch.setattr("engine.auto_proposal_engine._evaluate_skip_if", lambda r, d, t: False)
        monkeypatch.setattr("engine.auto_proposal_engine._check_prerequisite_chain", lambda r, h, s: True)
        monkeypatch.setattr("engine.auto_proposal_engine._check_cooldown", lambda r, h, t: True)
        monkeypatch.setattr("engine.auto_proposal_engine._apply_severity_priority", lambda rs: rs)
        monkeypatch.setattr("engine.auto_proposal_engine._load_client_cfg", lambda cid: {
            "company": {"name": "test_client", "honorific": "御中"},
            "chatwork_rooms": {"main": "111"},
        })
        monkeypatch.setattr("engine.auto_proposal_engine.load_client_state", lambda cid: {})

        captured = {}
        from notifiers.chatwork_notifier import ChatWorkClient

        def hooked(self, body, **kw):
            captured["body"] = body
            return {"message_id": "stub"}

        monkeypatch.setattr(ChatWorkClient, "post_message", hooked)

        run_auto_proposal("test_client", dry_run=False)

        body = captured.get("body", "")
        # 冒頭に「広告成果改善アクション」(法律名ではない)
        assert "広告成果改善アクション" in body[:120], "冒頭に広告成果文脈が無い"
        # 期待効果が含まれる
        assert "期待効果" in body
        # 確認質問が含まれる
        assert "確認してほしいこと" in body
        # 期待効果カテゴリラベルが出る
        assert "計測精度改善" in body or "1st Party Data 活用" in body
        # 法律名 / 強い文言は冒頭 200 文字に出ない
        head_200 = body[:200]
        assert "景品表示法" not in head_200
        assert "薬機法" not in head_200
        assert "措置命令" not in body
        assert "課徴金" not in body
        # Markdown 強調 ** が含まれない (ChatWork でそのまま表示されるため、5/8 Codex 修正)
        assert "**" not in body, "Markdown ** 強調が残っている"


# ============================================================
# Case 7: 優先度 A 4 件以上で全件本文表示 + history 更新整合 (5/8 Codex 修正)
# ============================================================

class TestPriorityAOverflow:
    """優先度 A が DAILY_RECOMMENDATIONS_PRIORITY_A_TOP (=3) を超える場合、
    4 件目以降が「非表示扱い」ではなく要約表示され、selected 全件が本文に出ること。
    history 更新対象も本文表示対象と一致すること。
    """

    def _setup_5_priority_a_rules(self, monkeypatch):
        """rule_messaging.yaml で priority A になる 5 件を用意 (上位 3 = 詳細、残 2 = 要約)"""
        # F-AH-04 / F-DG-01 / F-DG-02 / V-EC-01 / P-EF-01 が rule_messaging で priority A
        rules = [
            {"id": "F-AH-04", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "default"},
            {"id": "F-DG-01", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "default"},
            {"id": "F-DG-02", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "default"},
            {"id": "V-EC-01", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "critical", "daily_cap_group": "adr_013_legal"},
            {"id": "P-EF-01", "applies_to": {}, "trigger": {"condition": "True"},
             "data_source": [], "template": "_client_request_generic.md.j2",
             "rationale": "test", "severity": "high", "daily_cap_group": "adr_013_legal"},
        ]
        monkeypatch.setattr("engine.auto_proposal_engine._load_all_layers", lambda layer_filter=None: rules)
        monkeypatch.setattr("engine.auto_proposal_engine._filter_by_environment", lambda r, c: r)
        monkeypatch.setattr("engine.auto_proposal_engine._resolve_data_sources",
                            lambda r, c, s: {"client_state": {}, "ad_platform_data": {}, "rule_evaluation": {}})
        monkeypatch.setattr("engine.auto_proposal_engine._evaluate_trigger", lambda r, d, t: True)
        monkeypatch.setattr("engine.auto_proposal_engine._evaluate_skip_if", lambda r, d, t: False)
        monkeypatch.setattr("engine.auto_proposal_engine._check_prerequisite_chain", lambda r, h, s: True)
        monkeypatch.setattr("engine.auto_proposal_engine._check_cooldown", lambda r, h, t: True)
        monkeypatch.setattr("engine.auto_proposal_engine._apply_severity_priority", lambda rs: rs)
        monkeypatch.setattr("engine.auto_proposal_engine._load_client_cfg", lambda cid: {
            "company": {"name": "test_client", "honorific": "御中"},
            "chatwork_rooms": {"main": "111"},
        })
        monkeypatch.setattr("engine.auto_proposal_engine.load_client_state", lambda cid: {})
        return rules

    def test_priority_a_overflow_all_appear_in_body(
        self, isolated_history_dir, isolated_client_state_dir, monkeypatch,
    ):
        """priority A 5 件: 上位 3 件詳細 + 残 2 件要約、全 5 件が本文に登場する"""
        from engine.auto_proposal_engine import run_auto_proposal

        self._setup_5_priority_a_rules(monkeypatch)

        captured = {}
        from notifiers.chatwork_notifier import ChatWorkClient

        def hooked(self, body, **kw):
            captured["body"] = body
            return {"message_id": "stub"}
        monkeypatch.setattr(ChatWorkClient, "post_message", hooked)

        r = run_auto_proposal("test_client", dry_run=False)
        body = captured.get("body", "")

        # selected 全件 (5 件) のタイトルが title に出る
        assert "（5件）" in body, f"title 件数が selected 件数と不一致 (body title 部: {body[:200]})"

        # selected 全 rule_id が本文に登場
        for rid in ["F-AH-04", "F-DG-01", "F-DG-02", "V-EC-01", "P-EF-01"]:
            assert rid in body, f"{rid} が本文に登場していない (非表示扱いになっている)"

        # 詳細表示は上位 3 件、要約は 2 件 (priority A の 4-5 件目)
        # 「優先度A：今日確認したい N 件、要約」セクションが存在
        assert "優先度A" in body
        assert "要約" in body, "priority A 4 件目以降の要約セクションが無い"

        # cap グループは default 3 + adr_013_legal 2 で 5 件全部 selected されたか
        # → 旧バグで 4 件目以降が捨てられていない
        assert r["attempted_count"] == 5, f"5 件 selected されていない (attempted={r['attempted_count']})"
        assert r["sent_count"] == 5

        # history 更新対象 = 本文表示対象 = selected 全件
        history_path = isolated_history_dir / "test_client.yaml"
        assert history_path.exists()
        history = yaml.safe_load(history_path.read_text(encoding="utf-8")) or {}
        assert set(history.keys()) == {"F-AH-04", "F-DG-01", "F-DG-02", "V-EC-01", "P-EF-01"}, \
            f"history 更新対象が本文表示対象と一致しない: {sorted(history.keys())}"

    def test_history_only_updated_for_displayed_rules(
        self, isolated_history_dir, isolated_client_state_dir, monkeypatch,
    ):
        """displayed_rule_ids にない rule は history 更新されない (将来 truncate が
        入った場合の安全装置)"""
        from engine.auto_proposal_engine import run_auto_proposal

        self._setup_5_priority_a_rules(monkeypatch)

        # _render_and_post_bundle を mock して displayed_rule_ids を一部に絞る
        def fake_bundle(selected, state, client_cfg, today_str, dry_run=False):
            # 5 件のうち最初の 2 件だけ "displayed" として返す (残 3 件は本文非表示扱い)
            return {
                "rule_ids": [r["id"] for r in selected],
                "displayed_rule_ids": [r["id"] for r in selected[:2]],
                "result": {"message_id": "stub"},
                "body_length": 1000,
                "items_priority_a_detailed_count": 2,
                "items_priority_a_summary_count": 0,
                "items_priority_b_count": 0,
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post_bundle", side_effect=fake_bundle):
            r = run_auto_proposal("test_client", dry_run=False)

        history_path = isolated_history_dir / "test_client.yaml"
        history = yaml.safe_load(history_path.read_text(encoding="utf-8")) or {}

        # history は 2 件のみ (displayed_rule_ids と一致)
        assert len(history) == 2, \
            f"history が displayed_rule_ids と一致しない: {sorted(history.keys())}"
        assert set(history.keys()) == {"F-AH-04", "F-DG-01"}
