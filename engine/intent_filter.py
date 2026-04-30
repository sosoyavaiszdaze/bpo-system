"""Intent Override 抑止フィルター — クライアント意図による通知・スコア制御"""
import os
import logging
import yaml
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def load_intent_overrides(client_id):
    """clients.yaml から該当クライアントの intent_overrides を読み込む"""
    path = os.path.join(CONFIG_DIR, "clients.yaml")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        client_data = cfg.get("clients", {}).get(client_id, {})
        overrides = client_data.get("intent_overrides", [])
        return [o for o in overrides if _is_active(o)]
    except Exception as e:
        log.warning(f"intent_overrides読み込みエラー: {e}")
        return []


def filter_checks(client_id, checks):
    """チェック結果にintent_overridesを適用

    suppress_action:
      - skip_notification: check["suppressed"] = True
      - downgrade_severity: severity を1段下げる
      - add_context_note: check["context_note"] = 理由テキスト
    """
    overrides = load_intent_overrides(client_id)
    if not overrides:
        return checks

    override_map = {}
    for o in overrides:
        for rule_id in o.get("rule_ids", []):
            override_map[rule_id] = o

    for check in checks:
        check_id = check.get("id", "")
        if check_id not in override_map:
            continue

        override = override_map[check_id]
        action = override.get("suppress_action", "")
        reason = override.get("reason", "")

        if action == "skip_notification":
            check["suppressed"] = True
            check["suppressed_reason"] = reason
        elif action == "downgrade_severity":
            check["severity"] = _downgrade_severity(check.get("severity", "medium"))
            check["context_note"] = f"意図的設定によりseverity低下: {reason}"
        elif action == "add_context_note":
            check["context_note"] = f"意図的設定: {reason}"

    return checks


def get_expiring_overrides(client_id, days=30):
    """expires_at が今日から days 日以内の override を返す"""
    path = os.path.join(CONFIG_DIR, "clients.yaml")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        client_data = cfg.get("clients", {}).get(client_id, {})
        overrides = client_data.get("intent_overrides", [])
    except Exception:
        return []

    cutoff = datetime.now() + timedelta(days=days)
    expiring = []
    for o in overrides:
        expires = o.get("expires_at")
        if expires is None:
            continue  # 無期限は除外
        try:
            exp_date = datetime.fromisoformat(str(expires))
            if datetime.now() <= exp_date <= cutoff:
                expiring.append(o)
        except (ValueError, TypeError):
            continue
    return expiring


def _is_active(override):
    """expires_at が None または未来なら True"""
    expires = override.get("expires_at")
    if expires is None:
        return True
    try:
        return datetime.fromisoformat(str(expires)) > datetime.now()
    except (ValueError, TypeError):
        return True


def _downgrade_severity(severity):
    """severity を1段下げる: critical→high→medium→low→info"""
    try:
        idx = SEVERITY_ORDER.index(severity)
        return SEVERITY_ORDER[min(idx + 1, len(SEVERITY_ORDER) - 1)]
    except ValueError:
        return severity
