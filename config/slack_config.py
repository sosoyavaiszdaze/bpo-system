"""Slack人間判断フロー設定"""
import os

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")

CHANNEL_FRAUD_JUDGMENT = "#fraud-judgment"
CHANNEL_FRAUD_ALERTS = "#fraud-alerts"
CHANNEL_FRAUD_LOG = "#fraud-log"

ESCALATION_LEVELS = {
    "L1": {"mention": "<@U_MEDIA_OPS>", "timeout_minutes": 60},
    "L2": {"mention": "<@U_TEAM_LEAD>", "timeout_minutes": 120},
    "L3": {"mention": "<@U_MANAGER>", "timeout_minutes": 240},
}

TIMEOUT_DEFAULTS = {
    "cv_fraud_judgment": "flag_continue",
    "new_pattern_confirmation": "monitor",
    "bid_reset_approval": "no_reset",
}

JUDGMENT_TIMEOUT_MINUTES = {
    "cv_fraud_judgment": 1440,
    "new_pattern_confirmation": 2880,
    "bid_reset_approval": 4320,
}

REMINDER_INTERVALS = [60, 240, 720]
