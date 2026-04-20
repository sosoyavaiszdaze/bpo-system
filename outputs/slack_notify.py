"""Slack通知 - Block Kit リッチフォーマット"""
import os
import json
import logging
import urllib.request

log = logging.getLogger("bpo")

EMOJI = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}


def send_notification(client_id, results, config):
    webhook_env = config.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "")
    if not webhook_url:
        webhook_url = config.get("webhook_url", "")
    if not webhook_url:
        log.warning(f"[{client_id}] Slack Webhook未設定")
        return

    blocks = _build_blocks(client_id, results)
    payload = {"blocks": blocks}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            log.info(f"[{client_id}] Slack通知送信完了: {resp.status}")
    except Exception as e:
        log.error(f"[{client_id}] Slack通知失敗: {e}")


def _build_blocks(client_id, results):
    client_name = results.get("client_name", client_id)
    timestamp = results.get("timestamp", "")[:10]
    audit = results.get("ads_audit") or {}
    anomalies = results.get("anomalies") or {}
    waste = results.get("waste") or {}

    score = audit.get("score", "N/A")
    grade = audit.get("grade", "?")
    emoji = EMOJI.get(grade, "⚪")

    blocks = []

    # ヘッダー
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"📊 {client_name} 日次レポート", "emoji": True}
    })

    # スコア
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": (
            f"*{emoji} Health Score: {score} / 100 ({grade})*\n"
            f"📅 {timestamp}\n"
            f"キャンペーン数: {audit.get('total_campaigns', 0)} | "
            f"総コスト: ¥{audit.get('total_cost', 0):,.0f} | "
            f"総CV: {audit.get('total_conversions', 0):.0f} | "
            f"平均CPA: ¥{audit.get('avg_cpa', 0):,.0f}"
        )}
    })

    # Critical Issues
    issues = audit.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]
    if critical:
        blocks.append({"type": "divider"})
        issue_text = "*🚨 重大な問題*\n"
        for i in critical[:5]:
            issue_text += f"• *{i['campaign']}*: {i['issue']}\n  → {i['action']}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": issue_text}})

    # 異常検知
    alerts = anomalies.get("alerts", [])
    if alerts:
        blocks.append({"type": "divider"})
        alert_text = "*⚠️ 異常検知*\n"
        for a in alerts[:5]:
            alert_text += f"• *{a.get('campaign', '全体')}*: {a['message']}\n  原因: {a['cause']}\n  → {a['action']}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": alert_text}})

    # 無駄コスト
    waste_items = waste.get("waste_items", [])
    if waste_items:
        blocks.append({"type": "divider"})
        waste_text = f"*💸 無駄コスト検出: {waste.get('potential_savings', '¥0')}*\n"
        for w in waste_items[:5]:
            waste_text += f"• *{w['campaign']}*: {w['message']}\n  → {w['action']}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": waste_text}})

    # Quick Wins
    quick_wins = audit.get("quick_wins", [])
    if quick_wins:
        blocks.append({"type": "divider"})
        qw_text = "*✅ Quick Wins（すぐ実行可能）*\n"
        for q in quick_wins[:3]:
            qw_text += f"• *{q['campaign']}*: {q['action']}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": qw_text}})

    # フッター
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "🤖 BPO System v1.0 | 自動生成レポート"}]
    })

    return blocks
