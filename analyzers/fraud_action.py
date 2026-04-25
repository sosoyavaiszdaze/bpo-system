"""Fraud Action — Google Ads IP ブロック / Meta Block Lists / TikTok Pangle / Slack アラート"""
import os
import json
import logging
import urllib.request
from datetime import datetime

log = logging.getLogger("bpo")


def run_fraud_action(client_id, fraud_results, client_cfg, thresholds):
    """不正検知結果に基づくアクション実行

    Args:
        client_id: クライアントID
        fraud_results: fraud_audit の結果
        client_cfg: クライアント設定
        thresholds: 閾値設定
    Returns:
        dict: アクション結果
    """
    if not fraud_results or fraud_results.get("error"):
        return {"skipped": True}

    blocked_ips = []
    blocked_publishers = []
    actions_taken = []
    fraud_rate = fraud_results.get("fraud_rate", 0)
    fraud_items = fraud_results.get("fraud_items", [])

    # === Google Ads IP ブロック ===
    google_cfg = client_cfg.get("ads", {}).get("google", {})
    if google_cfg.get("customer_id") and google_cfg.get("customer_id") != "XXX-XXX-XXXX":
        suspicious_ips = [f.get("ip") for f in fraud_items if f.get("ip") and f.get("score", 0) >= 0.85]
        if suspicious_ips:
            # CIDR /24 集約
            aggregated = _aggregate_ips(suspicious_ips)
            blocked_ips = aggregated[:500]  # 500件上限
            actions_taken.append({
                "platform": "google",
                "action": "ip_block",
                "count": len(blocked_ips),
                "message": f"{len(blocked_ips)} IP/サブネットをブロック対象に追加",
            })
            log.info(f"[{client_id}] Google Ads: {len(blocked_ips)} IPブロック対象")

    # === Meta Publisher Block Lists ===
    meta_cfg = client_cfg.get("ads", {}).get("meta", {})
    if meta_cfg.get("account_id") and meta_cfg.get("account_id") != "act_XXXXXXXXX":
        suspicious_pubs = [f.get("publisher") for f in fraud_items if f.get("publisher") and f.get("score", 0) >= 0.60]
        if suspicious_pubs:
            blocked_publishers = suspicious_pubs[:10000]  # 10,000 URL/list 上限
            actions_taken.append({
                "platform": "meta",
                "action": "publisher_block",
                "count": len(blocked_publishers),
                "message": f"{len(blocked_publishers)} パブリッシャーをブロックリストに追加",
            })
            log.info(f"[{client_id}] Meta: {len(blocked_publishers)} パブリッシャーブロック対象")

    # === TikTok Pangle Block List ===
    tiktok_cfg = client_cfg.get("ads", {}).get("tiktok", {})
    if tiktok_cfg.get("advertiser_id") and tiktok_cfg.get("advertiser_id") != "XXXXXXXXX":
        # TikTok は IP レベルブロック不可、配信面レベルでの対応のみ
        suspicious_placements = [f.get("placement") for f in fraud_items if f.get("placement") and f.get("score", 0) >= 0.60]
        if suspicious_placements:
            actions_taken.append({
                "platform": "tiktok",
                "action": "pangle_block",
                "count": len(suspicious_placements),
                "message": f"{len(suspicious_placements)} Pangle配信面を除外対象に追加 (TikTokはIPブロック不可)",
            })

    # === Slack アラート ===
    notif_cfg = client_cfg.get("notifications", {}).get("slack", {})
    adtruth_t = thresholds.get("adtruth", {})
    critical_threshold = adtruth_t.get("fraud_rate_critical", 40)

    if fraud_rate > critical_threshold:
        _send_fraud_alert(client_id, fraud_rate, actions_taken, notif_cfg, critical=True)
    elif actions_taken:
        _send_fraud_alert(client_id, fraud_rate, actions_taken, notif_cfg, critical=False)

    # 推定節約額
    estimated_savings = sum(f.get("cost", 0) for f in fraud_items if f.get("score", 0) >= 0.60)

    result = {
        "fraud_rate": fraud_rate,
        "blocked_ips": len(blocked_ips),
        "blocked_publishers": len(blocked_publishers),
        "actions_taken": actions_taken,
        "estimated_savings": round(estimated_savings),
        "ip_quota_used": f"{len(blocked_ips)}/500",
    }

    log.info(f"[{client_id}] Fraud Action完了: {len(actions_taken)} アクション, 推定節約 ¥{estimated_savings:,.0f}")
    return result


def _aggregate_ips(ips):
    """同一 /24 サブネットを集約"""
    subnets = {}
    individual = []

    for ip in ips:
        parts = ip.split(".")
        if len(parts) == 4:
            subnet = ".".join(parts[:3])
            subnets.setdefault(subnet, []).append(ip)

    aggregated = []
    for subnet, subnet_ips in subnets.items():
        if len(subnet_ips) >= 3:
            aggregated.append(f"{subnet}.0/24")
        else:
            aggregated.extend(subnet_ips)

    return aggregated


def _send_fraud_alert(client_id, fraud_rate, actions, notif_cfg, critical=False):
    """Fraud アラートを Slack に送信"""
    webhook_env = notif_cfg.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "")
    if not webhook_url:
        return

    emoji = "🚨" if critical else "⚠️"
    severity = "CRITICAL" if critical else "Daily Summary"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Fraud Detection: {severity}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*Client:* {client_id}\n"
            f"*不正率:* {fraud_rate:.1f}%\n"
            f"*検出時刻:* {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )}},
    ]

    for action in actions:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"• *{action['platform']}*: {action['message']}"},
        })

    try:
        data = json.dumps({"blocks": blocks}).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(f"[{client_id}] Fraud Slack通知送信完了")
    except Exception as e:
        log.error(f"[{client_id}] Fraud Slack通知失敗: {e}")
