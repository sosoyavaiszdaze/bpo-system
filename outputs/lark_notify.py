"""Lark通知モジュール — Lark Webhook (Interactive Card) 対応"""
import os
import json
import logging
import urllib.request

log = logging.getLogger("bpo")

GRADE_COLOR = {"A": "green", "B": "blue", "C": "yellow", "D": "orange", "F": "red"}


def send_lark_notification(client_id, results, config):
    """Lark Webhookに監査結果を送信

    Args:
        client_id: クライアントID
        results: パイプライン結果
        config: 通知設定 (lark_webhook_env等)
    """
    webhook_env = config.get("lark_webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "")
    if not webhook_url:
        log.warning(f"[{client_id}] Lark Webhook未設定")
        return

    card = _build_card(client_id, results)
    payload = {"msg_type": "interactive", "card": card}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(f"[{client_id}] Lark通知送信完了: {resp.status}")
    except Exception as e:
        log.error(f"[{client_id}] Lark通知失敗: {e}")


def _build_card(client_id, results):
    """Lark Interactive Card を構築"""
    audit = results.get("ads_audit") or {}
    anomalies = results.get("anomalies") or {}
    waste = results.get("waste") or {}

    score = audit.get("score", "N/A")
    grade = audit.get("grade", "?")
    client_name = results.get("client_name", client_id)
    issues = audit.get("issues", [])

    # suppressed除外
    active_issues = [i for i in issues if not i.get("suppressed")]
    critical_high = [i for i in active_issues if i.get("severity") in ("critical", "high")]

    color = GRADE_COLOR.get(grade, "grey")

    elements = []

    # スコア概要
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**Score:** {score} / 100 ({grade})\n"
                f"**チェック:** {audit.get('total_checks', 0)}件\n"
                f"**問題:** {len(active_issues)}件 "
                f"(Critical: {len([i for i in active_issues if i.get('severity') == 'critical'])})\n"
                f"**異常アラート:** {anomalies.get('alert_count', 0)}件\n"
                f"**無駄コスト:** {waste.get('potential_savings', '¥0')}"
            ),
        },
    })

    # 重大問題 Top 5
    if critical_high:
        elements.append({"tag": "hr"})
        issue_lines = []
        for i in critical_high[:5]:
            sev = i.get("severity", "")
            emoji = "🔴" if sev == "critical" else "🟠"
            note = ""
            if i.get("context_note"):
                note = f" _{i['context_note']}_"
            issue_lines.append(f"{emoji} **[{i.get('id', '')}]** {i.get('issue', '')}{note}")
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**要対応の問題:**\n" + "\n".join(issue_lines)},
        })

    # Quick Wins
    quick_wins = audit.get("quick_wins", [])
    if quick_wins:
        elements.append({"tag": "hr"})
        qw_lines = [f"• {q.get('action', '')}" for q in quick_wins[:5]]
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**改善提案 Top 5:**\n" + "\n".join(qw_lines)},
        })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 {client_name} 日次レポート"},
            "template": color,
        },
        "elements": elements,
    }

    return card
