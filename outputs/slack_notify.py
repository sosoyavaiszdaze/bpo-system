"""Slack通知 v2.0 - Block Kit 3媒体対応リッチフォーマット"""
import os
import json
import logging
import urllib.request

log = logging.getLogger("bpo")

EMOJI = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}
PLATFORM_EMOJI = {"google": "🔍", "meta": "📘", "tiktok": "🎵", "cross": "🔄"}
PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads", "cross": "クロス媒体"}
SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "warning": "⚠️"}


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

    # ===== ヘッダー =====
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"📊 {client_name} 日次レポート", "emoji": True}
    })

    # ===== スコア概要 =====
    blocks.append({"type": "divider"})

    score_bar = _score_bar(score if isinstance(score, (int, float)) else 0)
    total_issues = len(audit.get("issues", []))
    critical_issues = len([i for i in audit.get("issues", []) if i.get("severity") == "critical"])
    alert_count = anomalies.get("alert_count", 0)
    waste_savings = waste.get("potential_savings", "¥0")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": (
            f"*{emoji} Health Score: {score} / 100 ({grade})*\n"
            f"{score_bar}\n"
            f"📅 {timestamp}\n\n"
            f"*サマリー*\n"
            f"├ 🚨 重大問題: *{critical_issues}件* / 全{total_issues}件\n"
            f"├ ⚠️ 異常検知: *{alert_count}件*\n"
            f"├ 💸 無駄コスト: *{waste_savings}*\n"
            f"└ ✅ Quick Wins: *{len(audit.get('quick_wins', []))}件*"
        )}
    })

    # ===== 媒体別パフォーマンス =====
    platform_summary = audit.get("platform_summary", {})
    if platform_summary:
        blocks.append({"type": "divider"})
        perf_text = "*📈 媒体別パフォーマンス*\n\n"
        for p, summary in platform_summary.items():
            p_emoji = PLATFORM_EMOJI.get(p, "📊")
            p_label = PLATFORM_LABEL.get(p, p)
            campaigns = summary.get("campaigns", 0)
            p_cost = summary.get("cost", 0)
            p_cv = summary.get("conversions", 0)
            p_roas = summary.get("roas", 0)
            p_issues = summary.get("issues", 0)
            p_critical = summary.get("critical", 0)

            status = "🔴" if p_critical > 0 else "🟡" if p_issues > 0 else "🟢"
            perf_text += (
                f"{p_emoji} *{p_label}* {status}\n"
                f"　CP数: {campaigns} | コスト: ¥{p_cost:,.0f} | CV: {p_cv:.0f} | ROAS: {p_roas:.1f}\n"
                f"　問題: {p_issues}件"
            )
            if p_critical > 0:
                perf_text += f" (うち重大 *{p_critical}件*)"
            perf_text += "\n\n"

        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": perf_text}})

    # ===== 重大問題（媒体別グループ） =====
    issues = audit.get("issues", [])
    critical = [i for i in issues if i.get("severity") in ("critical", "high")]
    if critical:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🚨 要対応の問題 ({len(critical)}件)*"}
        })

        # 媒体別にグループ化
        grouped = _group_issues_by_platform(critical)
        for platform, items in grouped.items():
            p_emoji = PLATFORM_EMOJI.get(platform, "📊")
            p_label = PLATFORM_LABEL.get(platform, platform)
            issue_text = f"\n{p_emoji} *{p_label}*\n"
            for i in items[:4]:
                sev = SEVERITY_EMOJI.get(i.get("severity"), "⚪")
                issue_text += f"　{sev} *{i['campaign']}*: {i['issue']}\n　　→ {i['action']}\n"
            if len(items) > 4:
                issue_text += f"　...他 {len(items) - 4}件\n"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": issue_text}})

    # ===== 異常検知（媒体別） =====
    alerts = anomalies.get("alerts", [])
    if alerts:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚠️ 異常検知 ({len(alerts)}件)*"}
        })

        grouped_alerts = {}
        for a in alerts:
            p = a.get("platform", "cross")
            if p not in grouped_alerts:
                grouped_alerts[p] = []
            grouped_alerts[p].append(a)

        for platform, items in grouped_alerts.items():
            p_emoji = PLATFORM_EMOJI.get(platform, "📊")
            p_label = PLATFORM_LABEL.get(platform, platform)
            alert_text = f"\n{p_emoji} *{p_label}*\n"
            for a in items[:3]:
                sev = SEVERITY_EMOJI.get(a.get("severity"), "⚪")
                alert_text += f"　{sev} {a['message']}\n　　原因: {a['cause']}\n　　→ {a['action']}\n"
            if len(items) > 3:
                alert_text += f"　...他 {len(items) - 3}件\n"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": alert_text}})

    # ===== 無駄コスト =====
    waste_items = waste.get("waste_items", [])
    if waste_items:
        blocks.append({"type": "divider"})
        waste_pct = waste.get("waste_percentage", 0)
        waste_text = f"*💸 無駄コスト検出: {waste.get('potential_savings', '¥0')}*\n"
        waste_text += f"全体の *{waste_pct}%* が非効率に消化されている可能性\n\n"

        # 媒体別サマリー
        waste_platform = waste.get("platform_summary", {})
        if waste_platform:
            for p, ws in waste_platform.items():
                p_emoji = PLATFORM_EMOJI.get(p, "📊")
                p_label = PLATFORM_LABEL.get(p, p)
                waste_text += f"{p_emoji} {p_label}: {ws['count']}件 / ¥{ws['waste_cost']:,.0f}\n"
            waste_text += "\n"

        for w in waste_items[:5]:
            sev = SEVERITY_EMOJI.get(w.get("severity"), "⚪")
            waste_text += f"　{sev} *{w['campaign']}*: {w['message']}\n　　→ {w['action']}\n"
        if len(waste_items) > 5:
            waste_text += f"　...他 {len(waste_items) - 5}件\n"

        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": waste_text}})

    # ===== Quick Wins =====
    quick_wins = audit.get("quick_wins", [])
    if quick_wins:
        blocks.append({"type": "divider"})
        qw_text = f"*✅ Quick Wins — すぐ実行可能な改善 ({len(quick_wins)}件)*\n\n"
        for q in quick_wins[:5]:
            campaign = q.get("campaign", "")
            platform = q.get("platform", "")
            p_emoji = PLATFORM_EMOJI.get(platform, "📊")
            qw_text += f"　{p_emoji} *{campaign}*: {q['action']}\n"
        if len(quick_wins) > 5:
            qw_text += f"　...他 {len(quick_wins) - 5}件\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": qw_text}})

    # ===== SEO =====
    seo = results.get("seo_audit")
    if seo and not seo.get("error"):
        seo_score = seo.get("score", "N/A")
        seo_issues = seo.get("total_issues", 0)
        seo_critical = seo.get("critical_count", 0)
        if seo_issues > 0:
            blocks.append({"type": "divider"})
            seo_text = f"*🔎 SEO監査: Score {seo_score} / 問題 {seo_issues}件*\n"
            if seo_critical > 0:
                seo_text += f"🔴 重大問題 *{seo_critical}件* あり\n"
            seo_items = seo.get("issues", [])
            for s in seo_items[:3]:
                sev = SEVERITY_EMOJI.get(s.get("severity"), "⚪")
                seo_text += f"　{sev} {s.get('message', '')}\n"
            if len(seo_items) > 3:
                seo_text += f"　...他 {len(seo_items) - 3}件\n"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": seo_text}})

    # ===== フッター =====
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": (
            "🤖 *BPO System v2.0* | "
            f"チェック項目: 広告{len(issues)}件 + 異常{len(alerts)}件 + 無駄{len(waste_items)}件 | "
            "詳細はPDFレポートを確認"
        )}]
    })

    return blocks


def _score_bar(score):
    """スコアをビジュアルバーで表現"""
    filled = int(score / 5)
    empty = 20 - filled
    if score >= 90:
        bar_char = "🟩"
    elif score >= 75:
        bar_char = "🟦"
    elif score >= 60:
        bar_char = "🟨"
    elif score >= 40:
        bar_char = "🟧"
    else:
        bar_char = "🟥"
    return bar_char * filled + "⬜" * empty


def _group_issues_by_platform(issues):
    """Issueを媒体別にグループ化"""
    grouped = {}
    for i in issues:
        # issueのcampaign名やplatformフィールドから媒体を推定
        platform = i.get("platform", "")
        if not platform:
            campaign = i.get("campaign", "").lower()
            if any(k in campaign for k in ["meta", "fb", "ig", "facebook", "instagram", "reels", "advantage"]):
                platform = "meta"
            elif any(k in campaign for k in ["tiktok", "spark", "pangle", "smart+"]):
                platform = "tiktok"
            else:
                platform = "google"
        if platform not in grouped:
            grouped[platform] = []
        grouped[platform].append(i)
    return grouped
