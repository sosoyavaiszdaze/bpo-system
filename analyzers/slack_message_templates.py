"""Slack Block Kit テンプレート（3カテゴリ: CV不正判断, 新種パターン, 入札リセット）"""
import json
from datetime import datetime


def build_cv_fraud_judgment_message(client_id, placement_id, platform, fraud_rate,
                                     monthly_cv_raw, true_cv_count, fake_cv_count,
                                     fake_ratio, cv_quality_avg, fraud_score,
                                     top_fraud_signals, cpa_current, monthly_spend):
    """カテゴリA: CV付き不正の判断依頼メッセージ"""
    judgment_id = f"cvj_{client_id}_{placement_id}_{int(datetime.now().timestamp())}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "CV付き配信面の不正判断依頼"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*クライアント:*\n{client_id}"},
            {"type": "mrkdwn", "text": f"*プラットフォーム:*\n{platform}"},
            {"type": "mrkdwn", "text": f"*配信面:*\n`{placement_id}`"},
            {"type": "mrkdwn", "text": f"*不正スコア:*\n{fraud_score:.2f}"},
        ]},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*不正率:*\n{fraud_rate*100:.1f}%"},
            {"type": "mrkdwn", "text": f"*月間CV（生）:*\n{monthly_cv_raw}件"},
            {"type": "mrkdwn", "text": f"*真正CV:*\n{true_cv_count}件"},
            {"type": "mrkdwn", "text": f"*偽CV:*\n{fake_cv_count}件"},
        ]},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*偽CV率:*\n{fake_ratio*100:.1f}%"},
            {"type": "mrkdwn", "text": f"*CV品質平均:*\n{cv_quality_avg:.2f}"},
            {"type": "mrkdwn", "text": f"*現CPA:*\n{cpa_current:,.0f}"},
            {"type": "mrkdwn", "text": f"*月間消化:*\n{monthly_spend:,.0f}"},
        ]},
        {"type": "divider"},
        {"type": "actions", "block_id": f"judgment_{judgment_id}", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Block"}, "style": "danger",
             "value": json.dumps({"judgment_id": judgment_id, "action": "block", "category": "cv_fraud_judgment"}),
             "action_id": "fraud_judgment_block"},
            {"type": "button", "text": {"type": "plain_text", "text": "Reduce 50%"},
             "value": json.dumps({"judgment_id": judgment_id, "action": "reduce", "category": "cv_fraud_judgment"}),
             "action_id": "fraud_judgment_reduce"},
            {"type": "button", "text": {"type": "plain_text", "text": "Monitor"}, "style": "primary",
             "value": json.dumps({"judgment_id": judgment_id, "action": "monitor", "category": "cv_fraud_judgment"}),
             "action_id": "fraud_judgment_monitor"},
            {"type": "button", "text": {"type": "plain_text", "text": "Investigate"},
             "value": json.dumps({"judgment_id": judgment_id, "action": "investigate", "category": "cv_fraud_judgment"}),
             "action_id": "fraud_judgment_investigate"},
        ]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"24h未回答 → Monitor継続 | ID: `{judgment_id}`"}
        ]},
    ]

    return {
        "text": f"[判断依頼] {client_id}/{placement_id} 不正率{fraud_rate*100:.1f}% 真正CV{true_cv_count}件",
        "blocks": blocks,
        "metadata": {
            "judgment_id": judgment_id, "category": "cv_fraud_judgment",
            "client_id": client_id, "placement_id": placement_id,
            "platform": platform, "created_at": datetime.now().isoformat(),
        },
    }


def build_new_pattern_message(client_id, pattern_type, confidence,
                               affected_campaigns, metric_changes, suggested_action):
    """カテゴリB: 新種パターン確認メッセージ"""
    judgment_id = f"npj_{client_id}_{pattern_type}_{int(datetime.now().timestamp())}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "新種パターン検出 - 確認依頼"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*クライアント:*\n{client_id}"},
            {"type": "mrkdwn", "text": f"*パターン:*\n`{pattern_type}`"},
            {"type": "mrkdwn", "text": f"*確信度:*\n{confidence*100:.0f}%"},
            {"type": "mrkdwn", "text": f"*影響CP:*\n{len(affected_campaigns)}件"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*推奨:*\n{suggested_action}"}},
        {"type": "divider"},
        {"type": "actions", "block_id": f"judgment_{judgment_id}", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "不正確定"}, "style": "danger",
             "value": json.dumps({"judgment_id": judgment_id, "action": "confirm_fraud", "category": "new_pattern_confirmation"}),
             "action_id": "pattern_confirm_fraud"},
            {"type": "button", "text": {"type": "plain_text", "text": "PF変更"},
             "value": json.dumps({"judgment_id": judgment_id, "action": "platform_change", "category": "new_pattern_confirmation"}),
             "action_id": "pattern_platform_change"},
            {"type": "button", "text": {"type": "plain_text", "text": "監視継続"}, "style": "primary",
             "value": json.dumps({"judgment_id": judgment_id, "action": "monitor", "category": "new_pattern_confirmation"}),
             "action_id": "pattern_monitor"},
        ]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"48h未回答 → 監視継続 | ID: `{judgment_id}`"}
        ]},
    ]

    return {
        "text": f"[新種パターン] {client_id}/{pattern_type} 確信度{confidence*100:.0f}%",
        "blocks": blocks,
        "metadata": {
            "judgment_id": judgment_id, "category": "new_pattern_confirmation",
            "client_id": client_id, "pattern_type": pattern_type,
            "created_at": datetime.now().isoformat(),
        },
    }


def build_bid_reset_message(client_id, platform, campaign_ids, fraud_rate,
                             current_cpa, target_cpa, recommendation_level):
    """カテゴリC: 入札リセット承認メッセージ"""
    judgment_id = f"brj_{client_id}_{platform}_{int(datetime.now().timestamp())}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "入札戦略リセット承認依頼"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*クライアント:*\n{client_id}"},
            {"type": "mrkdwn", "text": f"*PF:*\n{platform}"},
            {"type": "mrkdwn", "text": f"*対象CP:*\n{len(campaign_ids)}件"},
            {"type": "mrkdwn", "text": f"*不正率:*\n{fraud_rate*100:.1f}%"},
        ]},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*現CPA:*\n{current_cpa:,.0f}"},
            {"type": "mrkdwn", "text": f"*目標CPA:*\n{target_cpa:,.0f}"},
            {"type": "mrkdwn", "text": f"*推奨:*\n`{recommendation_level}`"},
        ]},
        {"type": "divider"},
        {"type": "actions", "block_id": f"judgment_{judgment_id}", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "承認"}, "style": "primary",
             "value": json.dumps({"judgment_id": judgment_id, "action": "approve_reset", "category": "bid_reset_approval"}),
             "action_id": "bid_reset_approve"},
            {"type": "button", "text": {"type": "plain_text", "text": "1週間延期"},
             "value": json.dumps({"judgment_id": judgment_id, "action": "delay_1week", "category": "bid_reset_approval"}),
             "action_id": "bid_reset_delay"},
            {"type": "button", "text": {"type": "plain_text", "text": "不要"}, "style": "danger",
             "value": json.dumps({"judgment_id": judgment_id, "action": "reject_reset", "category": "bid_reset_approval"}),
             "action_id": "bid_reset_reject"},
        ]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"72h未回答 → リセットせず現状維持 | ID: `{judgment_id}`"}
        ]},
    ]

    return {
        "text": f"[入札リセット] {client_id}/{platform} {recommendation_level}",
        "blocks": blocks,
        "metadata": {
            "judgment_id": judgment_id, "category": "bid_reset_approval",
            "client_id": client_id, "platform": platform,
            "created_at": datetime.now().isoformat(),
        },
    }


def build_timeout_notification(judgment_id, category, default_action):
    """タイムアウト時の結果通知"""
    return {
        "text": f"判断タイムアウト: `{judgment_id}` → `{default_action}` 自動適用",
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f"*判断タイムアウト*\nID: `{judgment_id}` | `{category}` | 自動適用: `{default_action}`"}}],
    }


def build_escalation_message(judgment_id, level, original_summary):
    """エスカレーション通知"""
    return {
        "text": f"エスカレーション ({level}): {judgment_id}",
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f"*エスカレーション: {level}*\n前レベルで回答なし。判断をお願いします。\n\n{original_summary}"}}],
    }
