"""PDFレポート生成 v2.1 - Playwright + Jinja2 HTML→PDF"""
import os
import logging
from datetime import datetime

log = logging.getLogger("bpo")

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads"}
PLATFORM_EMOJI = {"google": "🔍", "meta": "📘", "tiktok": "🎵"}

GRADE_CLASS = {"A": "a", "B": "b", "C": "c", "D": "d", "F": "f"}
GRADE_DESC = {
    "A": "優秀 — 最適化済み",
    "B": "良好 — 軽微な改善余地",
    "C": "要改善 — 構造的問題あり",
    "D": "要注意 — 複数の重大問題",
    "F": "危険 — 即時対応が必要",
}


def generate_pdf(client_id, results, pdf_path):
    """HTML テンプレートから PDF を生成"""
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError:
        log.error(f"[{client_id}] jinja2未インストール: pip3 install jinja2")
        return

    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # データ準備
    context = _build_context(client_id, results)

    # Jinja2 レンダリング
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("report.html")
    html_content = template.render(**context)

    # HTML を一時ファイルに書き出し
    html_path = pdf_path.replace(".pdf", ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Playwright で PDF 生成
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"file://{os.path.abspath(html_path)}", wait_until="networkidle")
            page.pdf(
                path=pdf_path,
                format="A4",
                margin={"top": "15mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                print_background=True,
                display_header_footer=True,
                header_template='<span></span>',
                footer_template='<div style="font-size:9px;font-family:sans-serif;color:#aaa;width:100%;text-align:center;padding:0 20px;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
            )
            browser.close()
        log.info(f"[{client_id}] PDF生成完了: {pdf_path}")
    except Exception as e:
        log.error(f"[{client_id}] Playwright PDF生成失敗: {e}")
        log.info(f"[{client_id}] HTMLレポート保存: {html_path}")


def _build_context(client_id, results):
    """テンプレートに渡すコンテキストデータを構築"""
    audit = results.get("ads_audit") or {}
    anomalies = results.get("anomalies") or {}
    waste = results.get("waste") or {}

    score = audit.get("score", 0)
    grade = audit.get("grade", "F")
    issues = audit.get("issues", [])
    quick_wins = audit.get("quick_wins", [])
    alerts = anomalies.get("alerts", [])
    waste_items = waste.get("waste_items", [])
    platform_summary = audit.get("platform_summary", {})

    platform_count = len(platform_summary)
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    high_issues = [i for i in issues if i.get("severity") == "high"]
    total_waste = waste.get("total_waste_cost", 0)

    summary_items = _build_summary_items(critical_issues, high_issues, alerts, waste_items, score)
    executive_summary = _build_executive_text(score, grade, len(issues), len(alerts), total_waste, platform_summary)

    # 媒体別データ
    platforms = []
    for p, summary in platform_summary.items():
        p_issues = [i for i in issues if _match_platform(i, p)]
        p_score = _calc_platform_score(summary, p_issues)
        platforms.append({
            "key": p,
            "label": PLATFORM_LABEL.get(p, p),
            "emoji": PLATFORM_EMOJI.get(p, "📊"),
            "score": p_score,
            "campaigns": summary.get("campaigns", 0),
            "cost_display": f"{summary.get('cost', 0):,.0f}",
            "cv": f"{summary.get('conversions', 0):.0f}",
            "roas": f"{summary.get('roas', 0):.1f}",
            "issues": p_issues,
        })

    # Quick Wins に媒体ラベルと severity 追加
    for qw in quick_wins:
        p = qw.get("platform", "")
        if not p:
            campaign = qw.get("campaign", "").lower()
            if any(k in campaign for k in ["meta", "fb", "ig", "reels"]):
                p = "meta"
            elif any(k in campaign for k in ["tiktok", "spark"]):
                p = "tiktok"
            else:
                p = "google"
            qw["platform"] = p
        qw["platform_label"] = PLATFORM_LABEL.get(p, p)
        if "severity" not in qw:
            qw["severity"] = "medium"

    # Waste items に表示用データ追加
    for w in waste_items:
        p = w.get("platform", "unknown")
        w["platform_label"] = PLATFORM_LABEL.get(p, p)
        w["waste_display"] = f"{w.get('waste_amount', w.get('cost', 0)):,.0f}"

    # Fraud action display
    fraud_audit_data = results.get("fraud_audit") or {}
    fraud_action_data = results.get("fraud_action") or {}
    if fraud_action_data:
        fraud_action_data["estimated_savings_display"] = f"{fraud_action_data.get('estimated_savings', 0):,.0f}"

    return {
        "client_name": results.get("client_name", client_id),
        "timestamp": results.get("timestamp", "")[:10],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M JST"),
        "platform_count": platform_count,
        "score": score,
        "grade": grade,
        "grade_class": GRADE_CLASS.get(grade, "f"),
        "grade_description": GRADE_DESC.get(grade, ""),
        "executive_summary": executive_summary,
        "summary_items": summary_items,
        "total_checks": len(issues) + len(alerts) + len(waste_items),
        "alert_count": len(alerts),
        "savings_display": f"{total_waste:,.0f}",
        "total_cost_display": f"{audit.get('total_cost', 0):,.0f}",
        "avg_cpa_display": f"{audit.get('avg_cpa', 0):,.0f}",
        "total_cv": f"{audit.get('total_conversions', 0):.0f}",
        "campaign_count": audit.get("total_campaigns", 0),
        "issue_count": len(issues),
        "critical_count": len(critical_issues),
        "alerts": alerts,
        "platforms": platforms,
        "quick_wins": quick_wins,
        "waste_items": waste_items,
        "waste_savings": waste.get("potential_savings", "¥0"),
        # Phase 3-4: 新セクション
        "fraud_audit": fraud_audit_data if fraud_audit_data else None,
        "fraud_action": fraud_action_data if fraud_action_data else None,
        "conflicts": results.get("conflicts"),
        "claude_analysis": results.get("claude_analysis") if results.get("claude_analysis") and not results.get("claude_analysis", {}).get("skipped") else None,
    }


def _build_executive_text(score, grade, issue_count, alert_count, total_waste, platform_summary):
    parts = []
    if score >= 80:
        parts.append(f"全体スコア{score}点（{grade}）。アカウントは良好な状態を維持している。")
    elif score >= 60:
        parts.append(f"全体スコア{score}点（{grade}）。基本的な運用は機能しているが、改善余地がある。")
    elif score >= 40:
        parts.append(f"全体スコア{score}点（{grade}）。複数の構造的問題が検出された。早期対応を推奨。")
    else:
        parts.append(f"全体スコア{score}点（{grade}）。重大な問題が複数検出されており、即時対応が必要。")

    if issue_count > 0:
        parts.append(f"今回の監査で{issue_count}件の問題を検出。")
    if alert_count > 0:
        parts.append(f"異常検知で{alert_count}件のアラートが発生。")
    if total_waste > 0:
        parts.append(f"推定¥{total_waste:,.0f}の非効率コストが検出された。")

    for p, summary in platform_summary.items():
        label = PLATFORM_LABEL.get(p, p)
        critical = summary.get("critical", 0)
        if critical > 0:
            parts.append(f"{label}で重大問題{critical}件 — 優先対応が必要。")

    return "".join(parts)


def _build_summary_items(critical_issues, high_issues, alerts, waste_items, score):
    items = []
    for issue in critical_issues[:2]:
        items.append({
            "bg": "#FCEBEB", "color": "#A32D2D", "icon": "✗",
            "text": f"{issue['campaign']}: {issue['issue']}",
        })
    for issue in high_issues[:2]:
        items.append({
            "bg": "#FAEEDA", "color": "#854F0B", "icon": "!",
            "text": f"{issue['campaign']}: {issue['issue']}",
        })
    for alert in alerts[:1]:
        items.append({
            "bg": "#FAEEDA", "color": "#854F0B", "icon": "!",
            "text": alert["message"],
        })
    if score >= 60:
        items.append({
            "bg": "#EAF3DE", "color": "#27500A", "icon": "✓",
            "text": "基本的な広告設定は正常に機能中",
        })
    if not items:
        items.append({
            "bg": "#EAF3DE", "color": "#27500A", "icon": "✓",
            "text": "重大な問題は検出されませんでした",
        })
    return items


def _match_platform(issue, platform):
    p = issue.get("platform", "")
    if p == platform:
        return True
    if not p:
        campaign = issue.get("campaign", "").lower()
        if platform == "meta":
            return any(k in campaign for k in ["meta", "fb", "ig", "facebook", "instagram", "reels"])
        elif platform == "tiktok":
            return any(k in campaign for k in ["tiktok", "spark", "pangle"])
        elif platform == "google":
            return not any(k in campaign for k in ["meta", "fb", "ig", "tiktok", "spark", "pangle"])
    return False


def _calc_platform_score(summary, issues):
    base = 80
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev == "critical":
            base -= 15
        elif sev == "high":
            base -= 8
        elif sev == "medium":
            base -= 3
        else:
            base -= 1
    return max(0, min(100, base))
