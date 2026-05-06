"""統合通知 (本日の広告成果改善TODO) のテスト (5/8 v2 緊急修正)

カバー要件:
- 本文に「上位 0 件」が出ない (priority A 0 件時の表現)
- 本文に「運用改善アクション (」(fallback 文言) が出ない
- 本文に「本指摘」「トリガー条件」「根本要因」「生成AIに添付」等の旧文言が出ない
- X-PI1 / ANO_CPA_SPIKE / ANO_IMPRESSION_DROP が統合通知に入る
- 各主要項目に 期待効果 + 今日の確認アクション + Yes/No 質問 が含まれる
- 未定義 rule は顧客向け本文に出ず、internal log に残る
- 同 state で 2 回実行しても、顧客向けに別 rule が追加投稿されない
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def reset_messaging_cache():
    from engine.daily_todo_builder import reset_cache
    reset_cache()
    yield
    reset_cache()


# ============================================================
# build_daily_todo: 統合 + ソート + fallback 禁止
# ============================================================

class TestBuildDailyTodo:
    def test_layer_a_anomaly_rules_appear_in_today(self):
        """X-PI1 / ANO_CPA_SPIKE / ANO_IMPRESSION_DROP が統合通知に入る"""
        from engine.daily_todo_builder import build_daily_todo

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1", "ANO_CPA_SPIKE", "ANO_IMPRESSION_DROP"],
            eligible_rules=[],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 75.6, "impression_change_pct": -68.0},
        )

        # 全件 priority A (X-PI1=measurement_recovery, ANO_*=cpa_diagnosis/delivery_diagnosis)
        # → items_today に上位 3 件入る
        assert len(ctx["items_today"]) == 3
        rule_ids = [i["rule_id"] for i in ctx["items_today"]]
        assert "X-PI1" in rule_ids
        assert "ANO_CPA_SPIKE" in rule_ids
        assert "ANO_IMPRESSION_DROP" in rule_ids

        # goal_stage 順ソート: X-PI1 (measurement_recovery=1) → ANO_CPA_SPIKE (cpa_diagnosis=2) → ANO_IMPRESSION_DROP (delivery_diagnosis=3)
        assert ctx["items_today"][0]["rule_id"] == "X-PI1"
        assert ctx["items_today"][1]["rule_id"] == "ANO_CPA_SPIKE"
        assert ctx["items_today"][2]["rule_id"] == "ANO_IMPRESSION_DROP"

    def test_priority_a_zero_does_not_show_top_zero(self):
        """priority A 0 件でも「上位 0 件」が出ない (5/8 v3: B のみでも上位埋め可、本文文言だけ検証)"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=[],
            eligible_rules=[
                {"id": "F-DG-01", "daily_cap_group": "default"},
                {"id": "F-DG-02", "daily_cap_group": "default"},
            ],
            today_str="2026-05-08",
        )
        # priority B でも items_today に上位として埋められるのが新仕様
        # (基本順序: priority B でも legal_note 以外なら今日確認に出す)
        body = render("_daily_recommendations.md.j2", ctx)
        # 「上位 0 件」「上位 0」のような旧バグの文言が出ないことが要件
        assert "上位 0 件" not in body
        assert "上位0件" not in body
        # 緊急対応なし表現 OR 今日確認セクションが出る
        assert "緊急対応" in body or "今日確認してほしいこと" in body

    def test_unmapped_rules_do_not_appear_in_body(self):
        """rule_messaging.yaml 未定義 rule は本文に出ない、internal_unmapped_rules に記録"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["F-AH-04", "UNDEFINED-RULE-XXX", "ANOTHER-UNDEF"],
            eligible_rules=[],
            today_str="2026-05-08",
        )

        # 未定義 rule は internal_unmapped_rules に記録
        assert "UNDEFINED-RULE-XXX" in ctx["internal_unmapped_rules"]
        assert "ANOTHER-UNDEF" in ctx["internal_unmapped_rules"]
        # F-AH-04 は登録済なので displayed
        assert "F-AH-04" in ctx["displayed_rule_ids"]

        body = render("_daily_recommendations.md.j2", ctx)
        # 未定義 rule_id は本文に登場しない
        assert "UNDEFINED-RULE-XXX" not in body
        assert "ANOTHER-UNDEF" not in body
        # 旧 fallback 文言「運用改善アクション (」が出ない
        assert "運用改善アクション (" not in body

    def test_no_old_legacy_phrases(self):
        """旧 daily_indication / _action_steps の汎用文が顧客向け本文に出ない"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1"],
            eligible_rules=[
                {"id": "F-AH-04", "daily_cap_group": "default"},
                {"id": "F-DG-01", "daily_cap_group": "default"},
                {"id": "F-LC-01", "daily_cap_group": "adr_013_legal"},
            ],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 75.6},
        )
        body = render("_daily_recommendations.md.j2", ctx)

        forbidden = [
            "本指摘",
            "トリガー条件",
            "根本要因を解消",
            "計測・配信・コンプライアンスのいずれか",
            "生成AIに添付",
            "実装状況のスクリーンショットをご共有",
            "**",  # Markdown 強調
        ]
        for phrase in forbidden:
            assert phrase not in body, f"禁止文言「{phrase}」が本文に含まれる"

    def test_main_items_have_required_fields(self):
        """各主要項目 (items_today) に 期待効果 + 今日の確認アクション + Yes/No 質問 が含まれる"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1", "ANO_CPA_SPIKE"],
            eligible_rules=[{"id": "F-AH-04", "daily_cap_group": "default"}],
            today_str="2026-05-08",
        )
        body = render("_daily_recommendations.md.j2", ctx)

        assert "広告成果への影響" in body
        assert "今日の確認アクション" in body
        assert "ご回答" in body
        # 各 item は priority A → 「広告成果への影響」セクションに登場
        assert ctx["items_today"]
        for item in ctx["items_today"]:
            assert item["expected_effect"], f"{item['rule_id']}: expected_effect が空"
            assert item["today_action"], f"{item['rule_id']}: today_action が空"
            assert item["yes_no_question"], f"{item['rule_id']}: yes_no_question が空"

    def test_headline_anomaly_summary(self):
        """anomaly_summary が冒頭 headline に反映される"""
        from engine.daily_todo_builder import build_daily_todo

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1"],
            eligible_rules=[],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 75.6, "impression_change_pct": -68.0},
        )
        headline = ctx["headline"]
        assert "75.6" in headline
        assert "68.0" in headline


# ============================================================
# fallback 禁止: rule_messaging 未定義 rule のスキップ
# ============================================================

class TestFallbackForbidden:
    def test_resolve_rule_message_returns_none_for_undefined(self):
        from engine.daily_todo_builder import resolve_rule_message

        assert resolve_rule_message("UNDEFINED-RULE") is None
        assert resolve_rule_message("X-PI1") is not None  # 既知 rule


# ============================================================
# 二重実行で別 rule が追加されない (cap バグ修正の継続検証)
# ============================================================

class TestRepeatExecution:
    def test_same_state_no_additional_rules_visible(self, monkeypatch, tmp_path):
        """auto_proposal の collect_eligible_rules は history を見て cap counter を進める。
        同 state で 2 回 build_daily_todo すると同じ rule が出ることを確認。
        (実 history 更新は post_daily_todo の責務)
        """
        from engine.daily_todo_builder import build_daily_todo

        rules_msg_a = ["F-AH-04"]
        rules_msg_b = [
            {"id": "F-DG-01", "daily_cap_group": "default"},
            {"id": "F-DG-02", "daily_cap_group": "default"},
        ]

        ctx1 = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=rules_msg_a, eligible_rules=rules_msg_b,
            today_str="2026-05-08",
        )
        ctx2 = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=rules_msg_a, eligible_rules=rules_msg_b,
            today_str="2026-05-08",
        )
        # 同入力 → 同出力 (deterministic)
        assert ctx1["displayed_rule_ids"] == ctx2["displayed_rule_ids"]


# ============================================================
# 5/8 v2 finalize: today_action / yes_no_question が本文に正しく出る
# 旧 fallback「本項目 (RULE_ID) について現状をご共有ください。」が出ない
# ============================================================

class TestTodayActionAndYesNoInBody:
    def test_today_action_appears_in_body(self):
        """rule_messaging.yaml の today_action が本文の「今日の確認アクション」に出る"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1"],   # rule_messaging で today_action 定義済
            eligible_rules=[],
            today_str="2026-05-08",
        )
        body = render("_daily_recommendations.md.j2", ctx)

        # X-PI1 の today_action が本文に出る
        assert "Meta Events Manager で Pixel が「アクティブ」状態かを確認" in body, \
            "X-PI1 の today_action が本文に出ていない"

    def test_yes_no_question_appears_in_body(self):
        """rule_messaging.yaml の yes_no_question が本文の「ご回答」セクションに出る"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1"],
            eligible_rules=[],
            today_str="2026-05-08",
        )
        body = render("_daily_recommendations.md.j2", ctx)

        # X-PI1 の yes_no_question が本文に出る
        assert "Pixel のアクティブ受信は確認できましたか?" in body, \
            "X-PI1 の yes_no_question が本文に出ていない"

    def test_no_legacy_fallback_phrase(self):
        """禁止文言「本項目 (RULE_ID) について現状をご共有ください。」が本文に出ない"""
        from engine.daily_todo_builder import build_daily_todo
        from templates.chatwork import render

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1", "ANO_CPA_SPIKE", "ANO_IMPRESSION_DROP"],
            eligible_rules=[
                {"id": "F-AH-04", "daily_cap_group": "default"},
                {"id": "F-DG-01", "daily_cap_group": "default"},
                {"id": "F-LC-01", "daily_cap_group": "adr_013_legal"},
            ],
            today_str="2026-05-08",
        )
        body = render("_daily_recommendations.md.j2", ctx)

        assert "本項目 (" not in body, "旧 fallback「本項目 (RULE_ID)」が本文に残っている"
        assert "について現状をご共有ください" not in body, "旧 fallback フレーズが残っている"


# ============================================================
# 5/8 v2 finalize: preview スクリプトが ChatWork に投稿しない
# ============================================================

class TestSortScoreLogic:
    """5/8 v3 多軸スコア順序ロジックの検証"""

    def test_cpa_spike_pushes_measurement_above_legal(self):
        """CPA 急騰時、計測 / 切り分け系が法律より上に来る"""
        from engine.daily_todo_builder import build_daily_todo

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1", "ANO_CPA_SPIKE"],
            eligible_rules=[
                {"id": "F-AH-04", "daily_cap_group": "default", "severity": "high"},
                {"id": "F-LC-01", "daily_cap_group": "adr_013_legal", "severity": "high"},
                {"id": "F-LC-04", "daily_cap_group": "adr_013_legal", "severity": "high"},
            ],
            layer_a_rule_defs={
                "X-PI1": {"id": "X-PI1", "severity": "high"},
                "ANO_CPA_SPIKE": {"id": "ANO_CPA_SPIKE", "severity": "critical"},
            },
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 75.6},
        )

        # 上位 today: 計測 / 切り分け系が来る (法律 F-LC-* は legal_note に行く)
        today_ids = [i["rule_id"] for i in ctx["items_today"]]
        assert "X-PI1" in today_ids or "F-AH-04" in today_ids, \
            f"計測系が today に出ていない: {today_ids}"
        assert "ANO_CPA_SPIKE" in today_ids, \
            f"ANO_CPA_SPIKE が今日確認に出ていない: {today_ids}"

        # 法律 (F-LC-*) は legal_note に
        legal_ids = [i["rule_id"] for i in ctx["items_legal_note"]]
        assert "F-LC-01" in legal_ids, "F-LC-01 が法令補足に出ていない"
        assert "F-LC-04" in legal_ids, "F-LC-04 が法令補足に出ていない"

    def test_score_breakdown_attached_to_each_item(self):
        """各 item に sort_score / sort_breakdown が付与される"""
        from engine.daily_todo_builder import build_daily_todo

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["X-PI1"],
            eligible_rules=[{"id": "F-AH-04", "daily_cap_group": "default", "severity": "high"}],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 50.0},
        )
        for it in ctx["items_today"] + ctx["items_this_week"] + ctx["items_legal_note"]:
            assert "sort_score" in it, f"{it['rule_id']}: sort_score 不在"
            assert "sort_breakdown" in it, f"{it['rule_id']}: sort_breakdown 不在"
            b = it["sort_breakdown"]
            for axis in ("priority", "goal_stage", "severity", "perf_impact",
                         "today_action", "already_notified"):
                assert axis in b, f"{it['rule_id']}: breakdown.{axis} 不在"

    def test_perf_impact_only_when_anomaly_threshold_met(self):
        """anomaly が閾値 (30%) 未満なら perf_impact が発動しない"""
        from engine.daily_todo_builder import build_daily_todo

        ctx_low = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=[],
            eligible_rules=[{"id": "F-AH-04", "daily_cap_group": "default", "severity": "high"}],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 5.0},  # 閾値 30 未満
        )
        item = ctx_low["items_today"][0]
        assert item["sort_breakdown"]["perf_impact"] == 0

        ctx_high = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=[],
            eligible_rules=[{"id": "F-AH-04", "daily_cap_group": "default", "severity": "high"}],
            today_str="2026-05-08",
            anomaly_summary={"cpa_change_pct": 50.0},  # 閾値超え
        )
        item_high = ctx_high["items_today"][0]
        assert item_high["sort_breakdown"]["perf_impact"] == -50

    def test_critical_severity_outranks_high_within_same_goal_stage(self):
        """同じ goal_stage 内で critical severity は high より上位"""
        from engine.daily_todo_builder import build_daily_todo

        ctx = build_daily_todo(
            client_id="test", client_cfg={"company": {"name": "test"}},
            layer_a_rule_ids=["ANO_CPA_SPIKE"],   # critical, cpa_diagnosis
            eligible_rules=[
                {"id": "F-MF-08", "daily_cap_group": "default", "severity": "high"},  # high, measurement_recovery
            ],
            layer_a_rule_defs={"ANO_CPA_SPIKE": {"id": "ANO_CPA_SPIKE", "severity": "critical"}},
            today_str="2026-05-08",
        )
        # 同 priority A 同士で、severity critical の方が上位
        ano = next((i for i in ctx["items_today"] + ctx["items_this_week"] if i["rule_id"] == "ANO_CPA_SPIKE"), None)
        mf  = next((i for i in ctx["items_today"] + ctx["items_this_week"] if i["rule_id"] == "F-MF-08"), None)
        assert ano is not None and mf is not None
        # F-MF-08 = goal_stage=measurement_recovery (1)、ANO_CPA_SPIKE = goal_stage=cpa_diagnosis (2)
        # severity 差 (-30 vs -10) は -20、goal_stage 差は +1 → ANO のスコア = -10 + 2 + 0 + (-30) + (-5) = -43
        # F-MF-08 = 0 + 1 + (-10) + 0 + (-5) = -14
        # → ANO の方が小さい (上位)
        assert ano["sort_score"] < mf["sort_score"], \
            f"critical severity の優先性が反映されていない: ANO={ano['sort_score']} F-MF-08={mf['sort_score']}"


class TestPreviewScriptNoSideEffect:
    def test_preview_does_not_call_chatwork(self, monkeypatch):
        """preview 関数は ChatWorkClient.post_message を呼ばない"""
        from notifiers.chatwork_notifier import ChatWorkClient

        called = {"post": False}

        def hook(self, body, **kw):
            called["post"] = True
            return {"message_id": "stub"}

        monkeypatch.setattr(ChatWorkClient, "post_message", hook)

        # preview スクリプトの本体を import
        from scripts import preview_chatwork_message
        # _collect_layer_a_rule_ids は audit を呼ぶので mock
        monkeypatch.setattr(preview_chatwork_message, "_collect_layer_a_rule_ids",
                            lambda c, t, e: (["X-PI1"], {"X-PI1": {}}, {}))

        # collect_eligible_rules も mock (テスト隔離)
        from engine import auto_proposal_engine
        monkeypatch.setattr(auto_proposal_engine, "collect_eligible_rules",
                            lambda client_id, today=None: {
                                "client_id": client_id, "loaded_rules_count": 0,
                                "environment_matched_count": 0, "eligible_count": 0,
                                "selected": [], "history": {}, "client_cfg": {"company": {"name": client_id}},
                            })

        body = preview_chatwork_message.preview(
            client_id="test_client", today_str="2026-05-08",
            bypass_cap=False, no_anomaly=True, exclude_layer_a=False,
        )

        # ChatWork 投稿は呼ばれていない
        assert called["post"] is False, "preview が ChatWork に投稿してしまっている"
        # 本文に X-PI1 関連の文言が出る
        assert "X-PI1" in body or "Pixel" in body
