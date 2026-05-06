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
        """priority A 0 件でも「上位 0 件」が出ない"""
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

        # priority B のみ → items_today=0、items_this_week=2
        assert len(ctx["items_today"]) == 0
        assert len(ctx["items_this_week"]) == 2

        body = render("_daily_recommendations.md.j2", ctx)
        # 「上位 0 件」「上位 0」のような旧バグの文言が出ない
        assert "上位 0 件" not in body
        assert "上位0件" not in body
        # 緊急対応なし旨の表現が含まれる
        assert "緊急対応" in body or "今週中に確認" in body

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
