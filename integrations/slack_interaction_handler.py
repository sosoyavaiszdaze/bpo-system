"""Slack Interaction Handler — ボタン押下時のリクエスト受信（Flask）"""
import json
import hashlib
import hmac
import time
import logging
import os

log = logging.getLogger("bpo")


def create_app():
    """Flask app factory（slack_sdk不要 — 軽量実装）"""
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        log.warning("flask 未インストール。Slack interaction handler は無効。")
        return None

    app = Flask(__name__)
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

    @app.route("/slack/interactions", methods=["POST"])
    def handle_interaction():
        """Slackからのインタラクションペイロードを受信"""
        if signing_secret and not _verify_signature(request, signing_secret):
            return jsonify({"error": "Invalid signature"}), 403

        payload = json.loads(request.form.get("payload", "{}"))
        if payload.get("type") != "block_actions":
            return jsonify({"ok": True}), 200

        from analyzers.slack_judgment import handle_judgment_response

        user = payload.get("user", {})
        for action in payload.get("actions", []):
            value = json.loads(action.get("value", "{}"))
            jid = value.get("judgment_id")
            act = value.get("action")
            cat = value.get("category")
            if jid and act and cat:
                handle_judgment_response(
                    judgment_id=jid, action=act, category=cat,
                    user_id=user.get("id", ""), user_name=user.get("username", ""),
                )

        return jsonify({"ok": True}), 200

    @app.route("/slack/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    return app


def _verify_signature(req, secret):
    """Slack署名検証"""
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    signature = req.headers.get("X-Slack-Signature", "")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    sig_basestring = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    my_sig = "v0=" + hmac.new(secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(my_sig, signature)
