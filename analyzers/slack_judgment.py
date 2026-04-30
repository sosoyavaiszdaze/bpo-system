"""Slack人間判断フロー — メインオーケストレーター"""
import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

# 設定読み込み（slack_sdk不要 — Webhook方式で動作）
try:
    from config.slack_config import (
        CHANNEL_FRAUD_JUDGMENT, CHANNEL_FRAUD_LOG,
        ESCALATION_LEVELS, TIMEOUT_DEFAULTS,
        JUDGMENT_TIMEOUT_MINUTES, REMINDER_INTERVALS,
    )
except ImportError:
    CHANNEL_FRAUD_JUDGMENT = "#fraud-judgment"
    CHANNEL_FRAUD_LOG = "#fraud-log"
    ESCALATION_LEVELS = {"L1": {"mention": "", "timeout_minutes": 60}}
    TIMEOUT_DEFAULTS = {"cv_fraud_judgment": "flag_continue", "new_pattern_confirmation": "monitor", "bid_reset_approval": "no_reset"}
    JUDGMENT_TIMEOUT_MINUTES = {"cv_fraud_judgment": 1440, "new_pattern_confirmation": 2880, "bid_reset_approval": 4320}
    REMINDER_INTERVALS = [60, 240, 720]

from analyzers.judgment_db import JudgmentDB
from analyzers.slack_message_templates import (
    build_cv_fraud_judgment_message, build_new_pattern_message,
    build_bid_reset_message,
)

_db = JudgmentDB()


def request_cv_fraud_judgment(client_id, placement_id, platform, fraud_rate,
                               monthly_cv_raw, true_cv_count, fake_cv_count,
                               fake_ratio, cv_quality_avg, fraud_score,
                               top_fraud_signals, cpa_current, monthly_spend):
    """カテゴリA: CV付き不正の判断をSlackで依頼"""
    msg = build_cv_fraud_judgment_message(
        client_id=client_id, placement_id=placement_id, platform=platform,
        fraud_rate=fraud_rate, monthly_cv_raw=monthly_cv_raw,
        true_cv_count=true_cv_count, fake_cv_count=fake_cv_count,
        fake_ratio=fake_ratio, cv_quality_avg=cv_quality_avg,
        fraud_score=fraud_score, top_fraud_signals=top_fraud_signals,
        cpa_current=cpa_current, monthly_spend=monthly_spend,
    )
    return _send_judgment_request(msg, "cv_fraud_judgment")


def request_new_pattern_confirmation(client_id, pattern_type, confidence,
                                      affected_campaigns, metric_changes, suggested_action):
    """カテゴリB: 新種パターンの確認をSlackで依頼"""
    msg = build_new_pattern_message(
        client_id=client_id, pattern_type=pattern_type, confidence=confidence,
        affected_campaigns=affected_campaigns, metric_changes=metric_changes,
        suggested_action=suggested_action,
    )
    return _send_judgment_request(msg, "new_pattern_confirmation")


def request_bid_reset_approval(client_id, platform, campaign_ids, fraud_rate,
                                current_cpa, target_cpa, recommendation_level):
    """カテゴリC: 入札リセットの承認をSlackで依頼"""
    msg = build_bid_reset_message(
        client_id=client_id, platform=platform, campaign_ids=campaign_ids,
        fraud_rate=fraud_rate, current_cpa=current_cpa,
        target_cpa=target_cpa, recommendation_level=recommendation_level,
    )
    return _send_judgment_request(msg, "bid_reset_approval")


def _send_judgment_request(msg, category):
    """共通送信処理（Webhook方式）"""
    judgment_id = msg["metadata"]["judgment_id"]
    webhook_url = os.environ.get("SLACK_FRAUD_WEBHOOK", "")

    slack_ts = ""
    if webhook_url:
        try:
            payload = json.dumps({"text": msg["text"], "blocks": msg["blocks"]}).encode("utf-8")
            req = urllib.request.Request(webhook_url, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10):
                slack_ts = datetime.now().isoformat()
                log.info(f"Judgment request sent: {judgment_id}")
        except Exception as e:
            log.error(f"Slack judgment送信失敗: {e}")
    else:
        log.warning("SLACK_FRAUD_WEBHOOK未設定。判断依頼をローカル記録のみ。")

    timeout_at = (datetime.now() + timedelta(minutes=JUDGMENT_TIMEOUT_MINUTES.get(category, 1440))).isoformat()
    _db.create_judgment(
        judgment_id=judgment_id, category=category, metadata=msg["metadata"],
        slack_ts=slack_ts, slack_channel=CHANNEL_FRAUD_JUDGMENT,
        timeout_at=timeout_at, escalation_level="L1",
    )
    return judgment_id


def check_and_escalate():
    """定期実行: 未回答の判断をエスカレーション or タイムアウト処理"""
    pending = _db.get_pending_judgments()
    now = datetime.now()

    for judgment in pending:
        judgment_id = judgment["judgment_id"]
        category = judgment["category"]
        created_at = datetime.fromisoformat(judgment["created_at"])
        timeout_at = datetime.fromisoformat(judgment["timeout_at"])
        current_level = judgment.get("escalation_level", "L1")

        if now >= timeout_at:
            default_action = TIMEOUT_DEFAULTS.get(category, "monitor")
            _db.resolve_judgment(
                judgment_id=judgment_id, action=default_action,
                judge="SYSTEM_TIMEOUT", reason="タイムアウト自動適用",
                resolved_at=now.isoformat(),
            )
            log.info(f"Judgment timeout: {judgment_id} → {default_action}")
            continue

        elapsed_minutes = (now - created_at).total_seconds() / 60
        levels = list(ESCALATION_LEVELS.keys())
        current_idx = levels.index(current_level) if current_level in levels else 0
        cumulative = 0
        for i, level in enumerate(levels):
            cumulative += ESCALATION_LEVELS[level]["timeout_minutes"]
            if elapsed_minutes >= cumulative and i > current_idx:
                _db.update_escalation_level(judgment_id, level)
                log.info(f"Escalated {judgment_id} to {level}")
                break

        last_reminder = judgment.get("last_reminder_minutes", 0)
        for interval in REMINDER_INTERVALS:
            if elapsed_minutes >= interval and last_reminder < interval:
                _db.update_last_reminder(judgment_id, interval)
                log.info(f"Reminder sent for {judgment_id} at {interval}min")
                break


def handle_judgment_response(judgment_id, action, category, user_id="", user_name=""):
    """Slackボタン押下時のハンドラー"""
    judgment = _db.get_judgment(judgment_id)
    if not judgment:
        log.warning(f"Judgment not found: {judgment_id}")
        return
    if judgment["status"] != "pending":
        log.info(f"Judgment already resolved: {judgment_id}")
        return

    _db.resolve_judgment(
        judgment_id=judgment_id, action=action,
        judge=user_name or user_id, reason=f"Slack判断 by {user_name}",
        resolved_at=datetime.now().isoformat(),
    )
    log.info(f"Judgment resolved: {judgment_id} → {action} by {user_name}")
