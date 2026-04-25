"""Slack Bot — Event API + Socket Mode ベースのインタラクティブボット"""
import os
import json
import logging
import urllib.request

log = logging.getLogger("bpo")


def handle_command(command, args, client_id=None):
    """Slackコマンドハンドラ

    Args:
        command: コマンド名 ("run", "status", "help")
        args: コマンド引数
        client_id: 対象クライアントID
    Returns:
        dict: レスポンス
    """
    handlers = {
        "run": _handle_run,
        "status": _handle_status,
        "help": _handle_help,
    }

    handler = handlers.get(command, _handle_unknown)
    return handler(args, client_id)


def _handle_run(args, client_id):
    """監査実行トリガー"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    target = args[0] if args else client_id or "all"
    log.info(f"Slack経由で監査トリガー: {target}")

    try:
        from pipeline import load_config, load_thresholds, run_client
        cfg = load_config()
        thr = load_thresholds()
        clients = cfg.get("clients", {})

        if target == "all":
            targets = {k: v for k, v in clients.items() if v.get("active")}
        elif target in clients:
            targets = {target: clients[target]}
        else:
            return {"text": f"❌ クライアント '{target}' が見つかりません"}

        results = {}
        for cid, ccfg in targets.items():
            results[cid] = run_client(cid, ccfg, thr)

        summary = "✅ 監査完了:\n"
        for cid, r in results.items():
            score = r.get("ads_audit", {}).get("score", "N/A") if r.get("ads_audit") else "N/A"
            grade = r.get("ads_audit", {}).get("grade", "?") if r.get("ads_audit") else "?"
            summary += f"• {cid}: Score {score} ({grade})\n"

        return {"text": summary}

    except Exception as e:
        return {"text": f"❌ 実行エラー: {e}"}


def _handle_status(args, client_id):
    """直近結果表示"""
    return {"text": "📊 ステータス機能は実装予定です"}


def _handle_help(args, client_id):
    """ヘルプ表示"""
    return {"text": (
        "🤖 *BPO System コマンド一覧*\n\n"
        "• `/audit run <client_id|all>` — 監査実行\n"
        "• `/audit status` — 直近結果表示\n"
        "• `/audit help` — このヘルプ\n"
    )}


def _handle_unknown(args, client_id):
    """不明コマンド"""
    return {"text": "❌ 不明なコマンドです。`/audit help` でコマンド一覧を確認してください。"}


def send_tradeoff_question(client_id, conflict, notif_cfg):
    """トレードオフ質問を Slack に送信"""
    webhook_env = notif_cfg.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "")
    if not webhook_url:
        return

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔄 トレードオフ検出"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*Client:* {client_id}\n"
            f"*矛盾:* {conflict.get('name', '')}\n"
            f"*説明:* {conflict.get('description', '')}"
        )}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "CPA最小化を優先"},
             "value": "cpa_minimize", "action_id": "tradeoff_cpa"},
            {"type": "button", "text": {"type": "plain_text", "text": "CV量を優先"},
             "value": "cv_maximize", "action_id": "tradeoff_cv"},
            {"type": "button", "text": {"type": "plain_text", "text": "ROAS目標を優先"},
             "value": "roas_target", "action_id": "tradeoff_roas"},
        ]},
    ]

    try:
        data = json.dumps({"blocks": blocks}).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(f"[{client_id}] トレードオフ質問送信完了")
    except Exception as e:
        log.error(f"[{client_id}] トレードオフ質問送信失敗: {e}")
