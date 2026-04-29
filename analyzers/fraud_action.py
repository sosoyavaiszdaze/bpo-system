"""
fraud_action.py — AdTruth 不正検知アクションエンジン v2.0
=========================================================
変更点:
  - パブリッシャーブロック閾値を 0.60 → 0.85（高CV配信面）に引き上げ
  - デフォルトアクションを「flag_and_monitor」に変更
  - CV数×不正率の複合判定ロジック追加
  - ブロック前後の効果測定 & 自動アンブロック機構
"""

import os
import json
import logging
import datetime

log = logging.getLogger("bpo")

# ============================================================
# 定数・閾値
# ============================================================
IP_BLOCK_THRESHOLD = 0.85
PUBLISHER_BLOCK_THRESHOLD_HIGH_CV = 0.85
PUBLISHER_BLOCK_THRESHOLD_NO_CV = 0.85
PLACEMENT_BLOCK_THRESHOLD = 0.85

FRAUD_RATE_BLOCK_THRESHOLD = 0.20
CV_SAFE_THRESHOLD = 50
CV_ZERO_AUTOBLOCK = True

FRAUD_RATE_CRITICAL = 0.40

IP_EXCLUSION_LIMIT = 500
PUBLISHER_EXCLUSION_LIMIT = 10_000

POST_BLOCK_EVAL_DAYS = 7
CPA_DETERIORATION_THRESHOLD = 1.20
CV_DETERIORATION_THRESHOLD = 0.70
REACH_DETERIORATION_THRESHOLD = 0.70


# ============================================================
# メイン関数
# ============================================================
def run_fraud_action(client_id, fraud_results, client_config, thresholds=None,
                     campaign_metrics=None):
    """不正検知結果に基づきアクションを決定する。

    Args:
        client_id: クライアントID
        fraud_results: fraud_ingest or fraud_audit の結果
        client_config: クライアント設定
        thresholds: 閾値設定（後方互換用）
        campaign_metrics: キャンペーン指標（効果測定用）
    Returns:
        dict: アクション結果
    """
    if not fraud_results or fraud_results.get("error"):
        return {"skipped": True}

    # 業界別閾値の適用
    try:
        from analyzers.industry_thresholds import apply_dynamic_thresholds
        dynamic_t = apply_dynamic_thresholds(client_config)
        if dynamic_t:
            log.info(f"[{client_id}] 業界別閾値適用: {dynamic_t.get('industry', 'default')}")
    except ImportError:
        pass

    actions = []
    flagged_items = []
    blocked_items = []
    pre_block_snapshots = {}

    fraud_items = fraud_results.get("fraud_items", fraud_results.get("issues", []))
    fraud_rate = fraud_results.get("fraud_rate", 0)
    overall_score = fraud_results.get("score", 100)

    # --- Google Ads IP ブロック判定 ---
    google_actions = _evaluate_google_ip_block(
        fraud_items, campaign_metrics, pre_block_snapshots
    )
    actions.extend(google_actions["actions"])
    flagged_items.extend(google_actions["flagged"])
    blocked_items.extend(google_actions["blocked"])

    # --- Meta パブリッシャーブロック判定 ---
    meta_actions = _evaluate_meta_publisher_block(
        fraud_items, campaign_metrics, pre_block_snapshots
    )
    actions.extend(meta_actions["actions"])
    flagged_items.extend(meta_actions["flagged"])
    blocked_items.extend(meta_actions["blocked"])

    # --- TikTok / Pangle 配信面除外判定 ---
    tiktok_actions = _evaluate_tiktok_placement_block(
        fraud_items, campaign_metrics, pre_block_snapshots
    )
    actions.extend(tiktok_actions["actions"])
    flagged_items.extend(tiktok_actions["flagged"])
    blocked_items.extend(tiktok_actions["blocked"])

    # --- Slack 通知 ---
    _send_fraud_notification(
        client_id, fraud_rate, actions, flagged_items, blocked_items, client_config
    )

    # --- 効果測定スナップショット保存 ---
    if pre_block_snapshots:
        _save_pre_block_snapshot(client_id, pre_block_snapshots)

    # --- 既存ブロックの効果測定 & 自動アンブロック ---
    unblocked = _evaluate_existing_blocks(client_id, campaign_metrics)

    estimated_savings = sum(
        item.get("cost", 0)
        for item in fraud_items
        if item.get("score", 0) >= IP_BLOCK_THRESHOLD
    )

    result = {
        "client_id": client_id,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "fraud_rate": fraud_rate,
        "overall_score": overall_score,
        "total_actions": len(actions),
        "blocked_count": len(blocked_items),
        "blocked_ips": sum(1 for b in blocked_items if b.get("platform") == "google"),
        "blocked_publishers": sum(1 for b in blocked_items if b.get("platform") == "meta"),
        "flagged_count": len(flagged_items),
        "auto_unblocked": unblocked,
        "actions_taken": actions,
        "flagged_for_review": flagged_items,
        "blocked_items": blocked_items,
        "estimated_savings": round(estimated_savings),
        "ip_quota_used": f"{sum(1 for a in actions if a.get('platform') == 'google' and a.get('action') == 'block')}/500",
    }

    log.info(
        f"[{client_id}] Fraud Action完了: "
        f"{len(blocked_items)} ブロック, {len(flagged_items)} フラグ, "
        f"{len(unblocked)} アンブロック, 推定節約 ¥{estimated_savings:,.0f}"
    )
    return result


# ============================================================
# 複合判定ロジック（共通）
# ============================================================
def _composite_decision(fraud_score, fraud_rate, monthly_cv, threshold,
                        cv_quality_result=None, client_id="", placement_id="",
                        platform="", cpa_current=0, monthly_spend=0, top_signals=None):
    """TO-07準拠の複合判定。CV Quality Score + 学習DB + Slack判断統合。

    Args:
        cv_quality_result: calculate_true_cv_count()の結果
        client_id/placement_id/platform: Slack判断依頼に必要な情報
    Returns:
        str: "block" / "flag_and_monitor" / "monitor_only" / "pending_human_judgment"
    """
    # CV Quality Scoreがある場合は強化版判定
    if cv_quality_result and cv_quality_result.get("total_cvs", 0) > 0:
        try:
            from analyzers.cv_quality_scorer import enhanced_composite_decision
            result = enhanced_composite_decision(
                fraud_score, fraud_rate, monthly_cv, cv_quality_result, threshold
            )
            # flag_and_monitor かつ Slack判断が必要な条件
            if (result == "flag_and_monitor" and
                    fraud_rate >= FRAUD_RATE_BLOCK_THRESHOLD and
                    0 < cv_quality_result.get("real_cv_count", 0) < CV_SAFE_THRESHOLD and
                    cv_quality_result.get("fake_cv_count", 0) / max(monthly_cv, 1) < 0.80):
                return _try_slack_judgment(
                    client_id, placement_id, platform, fraud_rate, monthly_cv,
                    cv_quality_result, fraud_score, cpa_current, monthly_spend, top_signals
                )
            return result
        except ImportError:
            pass

    # フォールバック: 基本判定
    if fraud_score < threshold:
        return "monitor_only"

    if fraud_rate >= FRAUD_RATE_BLOCK_THRESHOLD and monthly_cv == 0:
        return "block"

    if fraud_rate >= FRAUD_RATE_BLOCK_THRESHOLD:
        return "flag_and_monitor"

    return "flag_and_monitor"


def _try_slack_judgment(client_id, placement_id, platform, fraud_rate, monthly_cv_raw,
                        cv_quality_result, fraud_score, cpa_current, monthly_spend, top_signals):
    """学習DBから自動提案を試み、なければSlack判断依頼"""
    try:
        from analyzers.judgment_db import JudgmentDB
        db = JudgmentDB()
        suggestion = db.get_auto_suggestion(
            category="cv_fraud_judgment",
            metadata={"client_id": client_id, "placement_id": placement_id}
        )
        if suggestion:
            log.info(f"学習DB自動適用: {client_id}/{placement_id} → {suggestion}")
            return suggestion
    except Exception as e:
        log.debug(f"学習DB参照エラー: {e}")

    try:
        from analyzers.slack_judgment import request_cv_fraud_judgment
        request_cv_fraud_judgment(
            client_id=client_id, placement_id=placement_id, platform=platform,
            fraud_rate=fraud_rate, monthly_cv_raw=monthly_cv_raw,
            true_cv_count=cv_quality_result.get("real_cv_count", 0),
            fake_cv_count=cv_quality_result.get("fake_cv_count", 0),
            fake_ratio=cv_quality_result.get("fake_cv_count", 0) / max(monthly_cv_raw, 1),
            cv_quality_avg=cv_quality_result.get("avg_quality_score", 0),
            fraud_score=fraud_score,
            top_fraud_signals=top_signals or [],
            cpa_current=cpa_current, monthly_spend=monthly_spend,
        )
        return "pending_human_judgment"
    except Exception as e:
        log.warning(f"Slack判断依頼エラー: {e}")
        return "flag_and_monitor"


# ============================================================
# プラットフォーム別評価
# ============================================================
def _evaluate_google_ip_block(fraud_items, campaign_metrics, snapshots):
    """Google Ads IPブロック評価"""
    actions, flagged, blocked = [], [], []

    ip_candidates = [
        item for item in fraud_items
        if item.get("platform", "").lower() in ("google", "cross", "") and item.get("ip")
    ]

    for item in ip_candidates:
        ip = item["ip"]
        score = item.get("score", 0)
        campaign = item.get("campaign", "unknown")
        fraud_rate = item.get("fraud_rate", item.get("score", 0))
        monthly_cv = _get_monthly_cv(campaign_metrics, campaign, "google")

        decision = _composite_decision(score, fraud_rate, monthly_cv, IP_BLOCK_THRESHOLD)

        entry = {
            "platform": "google",
            "type": "ip_exclusion",
            "target": ip,
            "campaign": campaign,
            "fraud_score": score,
            "fraud_rate": fraud_rate,
            "monthly_cv": monthly_cv,
            "decision": decision,
            "action": decision,
            "message": f"IP {ip}: 不正スコア{score:.2f}, CV={monthly_cv}",
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

        if decision == "block":
            blocked.append(entry)
            actions.append(entry)
            snapshots[f"google_{campaign}_{ip}"] = _capture_metrics(
                campaign_metrics, campaign, "google"
            )
        elif decision == "flag_and_monitor":
            flagged.append(entry)
            actions.append(entry)

    block_ips = [b["target"] for b in blocked]
    aggregated = _aggregate_ips(block_ips)
    if len(aggregated) > IP_EXCLUSION_LIMIT:
        log.warning(f"IPブロック候補{len(aggregated)}件が上限{IP_EXCLUSION_LIMIT}を超過")

    return {"actions": actions, "flagged": flagged, "blocked": blocked}


def _evaluate_meta_publisher_block(fraud_items, campaign_metrics, snapshots):
    """Meta パブリッシャーブロック評価"""
    actions, flagged, blocked = [], [], []

    pub_candidates = [
        item for item in fraud_items
        if item.get("platform", "").lower() == "meta" and item.get("publisher")
    ]

    for item in pub_candidates:
        publisher = item["publisher"]
        score = item.get("score", 0)
        campaign = item.get("campaign", "unknown")
        fraud_rate = item.get("fraud_rate", item.get("score", 0))
        monthly_cv = _get_monthly_cv(campaign_metrics, campaign, "meta")

        decision = _composite_decision(score, fraud_rate, monthly_cv, PUBLISHER_BLOCK_THRESHOLD_HIGH_CV)

        entry = {
            "platform": "meta",
            "type": "publisher_block",
            "target": publisher,
            "campaign": campaign,
            "fraud_score": score,
            "fraud_rate": fraud_rate,
            "monthly_cv": monthly_cv,
            "decision": decision,
            "action": decision,
            "message": f"Publisher {publisher}: 不正スコア{score:.2f}",
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

        if decision == "block":
            blocked.append(entry)
            actions.append(entry)
            snapshots[f"meta_{campaign}_{publisher}"] = _capture_metrics(
                campaign_metrics, campaign, "meta"
            )
        elif decision == "flag_and_monitor":
            flagged.append(entry)
            actions.append(entry)

    if len(blocked) > PUBLISHER_EXCLUSION_LIMIT:
        blocked = blocked[:PUBLISHER_EXCLUSION_LIMIT]

    return {"actions": actions, "flagged": flagged, "blocked": blocked}


def _evaluate_tiktok_placement_block(fraud_items, campaign_metrics, snapshots):
    """TikTok / Pangle 配信面除外評価"""
    actions, flagged, blocked = [], [], []

    placement_candidates = [
        item for item in fraud_items
        if item.get("platform", "").lower() == "tiktok" and item.get("placement")
    ]

    for item in placement_candidates:
        placement = item["placement"]
        score = item.get("score", 0)
        campaign = item.get("campaign", "unknown")
        fraud_rate = item.get("fraud_rate", item.get("score", 0))
        monthly_cv = _get_monthly_cv(campaign_metrics, campaign, "tiktok")

        decision = _composite_decision(score, fraud_rate, monthly_cv, PLACEMENT_BLOCK_THRESHOLD)

        entry = {
            "platform": "tiktok",
            "type": "placement_exclusion",
            "target": placement,
            "campaign": campaign,
            "fraud_score": score,
            "fraud_rate": fraud_rate,
            "monthly_cv": monthly_cv,
            "decision": decision,
            "action": decision,
            "message": f"Placement {placement}: 不正スコア{score:.2f}",
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

        if decision == "block":
            blocked.append(entry)
            actions.append(entry)
            snapshots[f"tiktok_{campaign}_{placement}"] = _capture_metrics(
                campaign_metrics, campaign, "tiktok"
            )
        elif decision == "flag_and_monitor":
            flagged.append(entry)
            actions.append(entry)

    return {"actions": actions, "flagged": flagged, "blocked": blocked}


# ============================================================
# ブロック後 効果測定 & 自動アンブロック
# ============================================================
def _evaluate_existing_blocks(client_id, campaign_metrics):
    """既存ブロックの効果を測定し、悪化していれば自動アンブロック。"""
    unblocked = []
    snapshot_path = _snapshot_path(client_id)

    if not os.path.exists(snapshot_path):
        return unblocked

    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            snapshots = json.load(f)
    except (json.JSONDecodeError, OSError):
        return unblocked

    for key, snap in list(snapshots.items()):
        try:
            block_date = datetime.datetime.fromisoformat(snap.get("blocked_at", ""))
        except (ValueError, TypeError):
            continue
        days_since = (datetime.datetime.utcnow() - block_date).days

        if days_since < POST_BLOCK_EVAL_DAYS:
            continue

        parts = key.split("_", 2)
        platform = parts[0] if len(parts) > 0 else ""
        campaign = parts[1] if len(parts) > 1 else ""

        current = _capture_metrics(campaign_metrics, campaign, platform)
        pre_cpa = snap.get("cpa", 0)
        pre_cv = snap.get("cv_count", 0)
        pre_reach = snap.get("reach", 0)
        cur_cpa = current.get("cpa", 0)
        cur_cv = current.get("cv_count", 0)
        cur_reach = current.get("reach", 0)

        should_unblock = False
        reasons = []

        if pre_cpa > 0 and cur_cpa > 0 and cur_cpa / pre_cpa >= CPA_DETERIORATION_THRESHOLD:
            should_unblock = True
            reasons.append(f"CPA悪化: ¥{pre_cpa:,.0f} → ¥{cur_cpa:,.0f}")

        if pre_cv > 0 and cur_cv / pre_cv <= CV_DETERIORATION_THRESHOLD:
            should_unblock = True
            reasons.append(f"CV減少: {pre_cv} → {cur_cv}")

        if pre_reach > 0 and cur_reach / pre_reach <= REACH_DETERIORATION_THRESHOLD:
            should_unblock = True
            reasons.append(f"リーチ減少: {pre_reach:,} → {cur_reach:,}")

        if should_unblock:
            unblocked.append({
                "key": key, "platform": platform, "campaign": campaign,
                "reasons": reasons, "days_since_block": days_since,
                "action": "auto_unblock",
            })
            log.warning(f"自動アンブロック: {key} — {'; '.join(reasons)}")

    for entry in unblocked:
        snapshots.pop(entry["key"], None)

    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)

    return unblocked


# ============================================================
# Slack通知
# ============================================================
def _send_fraud_notification(client_id, fraud_rate, actions, flagged, blocked, client_config):
    """Slack通知: フラグ項目は人間判断を求めるメッセージを送信"""
    notif_cfg = client_config.get("notifications", {}).get("slack", {})
    webhook_env = notif_cfg.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "")
    if not webhook_url:
        return

    import urllib.request

    if fraud_rate >= FRAUD_RATE_CRITICAL:
        _send_slack_message(webhook_url, {
            "text": (
                f"🚨 *CRITICAL* | {client_id}\n"
                f"不正率: {fraud_rate*100:.1f}%\n"
                f"ブロック: {len(blocked)}件 | フラグ: {len(flagged)}件"
            )
        })

    for item in flagged[:5]:
        _send_slack_message(webhook_url, {
            "text": (
                f"⚠️ *判断依頼* | {item.get('platform','').upper()} | {item.get('campaign','')}\n"
                f"対象: `{item.get('target','')}`\n"
                f"不正率: {item.get('fraud_rate',0)*100:.1f}% | CV: {item.get('monthly_cv',0)}件/月\n"
                f"→ *ブロックしますか？*"
            )
        })

    if blocked:
        _send_slack_message(webhook_url, {
            "text": f"🛡️ *自動ブロック* | {client_id} | {len(blocked)}件実行"
        })


def _send_slack_message(webhook_url, payload):
    """Slack Webhookへメッセージ送信"""
    import urllib.request
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.error(f"Fraud Slack通知失敗: {e}")


# ============================================================
# ヘルパー関数
# ============================================================
def _get_monthly_cv(metrics, campaign, platform):
    """キャンペーンの月間CV数を取得"""
    if not metrics:
        return 0
    key = f"{platform}_{campaign}"
    camp_data = metrics.get(key, metrics.get(campaign, {}))
    return camp_data.get("monthly_cv", camp_data.get("conversions", 0))


def _capture_metrics(metrics, campaign, platform):
    """現在のキャンペーン指標をスナップショットとして取得"""
    if not metrics:
        return {
            "cpa": 0, "cv_count": 0, "reach": 0,
            "blocked_at": datetime.datetime.utcnow().isoformat()
        }
    key = f"{platform}_{campaign}"
    camp_data = metrics.get(key, metrics.get(campaign, {}))
    return {
        "cpa": camp_data.get("cpa", 0),
        "cv_count": camp_data.get("monthly_cv", camp_data.get("conversions", 0)),
        "reach": camp_data.get("reach", camp_data.get("impressions", 0)),
        "cost": camp_data.get("cost", 0),
        "blocked_at": datetime.datetime.utcnow().isoformat(),
    }


def _aggregate_ips(ip_list):
    """IPアドレスを/24サブネットに集約"""
    subnets = {}
    for ip in ip_list:
        parts = ip.split(".")
        if len(parts) == 4:
            subnet = ".".join(parts[:3])
            subnets.setdefault(subnet, []).append(ip)

    aggregated = []
    for subnet, ips in subnets.items():
        if len(ips) >= 3:
            aggregated.append(f"{subnet}.0/24")
        else:
            aggregated.extend(ips)
    return aggregated


def _save_pre_block_snapshot(client_id, snapshots):
    """ブロック前スナップショットを保存"""
    path = _snapshot_path(client_id)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(snapshots)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _snapshot_path(client_id):
    """スナップショットファイルパス"""
    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "block_snapshots"
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{client_id}_pre_block.json")
