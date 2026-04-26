"""レポート生成統合 — PDF用テンプレートデータを構築"""
import logging
from datetime import datetime

log = logging.getLogger("bpo")

PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads"}
PLATFORM_EMOJI = {"google": "🟢", "meta": "🔵", "tiktok": "⬛"}
GRADE_DESC = {
    "A": "Excellent — 最適化が行き届いている",
    "B": "Good — 改善余地あり",
    "C": "Fair — 要改善",
    "D": "Poor — 構造見直し必要",
    "F": "Critical — 早急な対応が必要",
}


def build_template_data(client_id, results):
    """PDF テンプレートに渡すデータを構築

    Args:
        client_id: クライアントID
        results: run_client() の結果
    Returns:
        dict: Jinja2 テンプレートに渡す全テンプレートデータ
    """
    audit = results.get("ads_audit") or {}
    anomalies = results.get("anomalies") or {}
    waste = results.get("waste") or {}
    fraud = results.get("fraud_audit") or {}
    fraud_action = results.get("fraud_action") or {}
    conflicts = results.get("conflicts") or []
    claude = results.get("claude_analysis") or {}
    score = audit.get("score", 0)
    grade = audit.get("grade", "F")
    issues = audit.get("issues", [])

    # プラットフォーム別データ
    platform_summary = audit.get("platform_summary", {})
    platforms = []
    for p, s in platform_summary.items():
        p_issues = [i for i in issues if i.get("platform") == p]
        platforms.append({
            "key": p,
            "label": PLATFORM_LABEL.get(p, p),
            "emoji": PLATFORM_EMOJI.get(p, "⚪"),
            "score": s.get("score", 0),
            "campaigns": s.get("campaigns", 0),
            "cost_display": f"{s.get('cost', 0):,.0f}",
            "cv": s.get("conversions", 0),
            "roas": s.get("roas", 0),
            "issues": p_issues,
        })

    # KPIカード
    total_cost = audit.get("total_cost", 0)
    total_cv = audit.get("total_conversions", 0)
    critical_count = len([i for i in issues if i.get("severity") == "critical"])

    # 異常検知
    alerts = anomalies.get("alerts", [])

    # 無駄コスト
    waste_items = waste.get("items", [])
    waste_savings = f"¥{waste.get('total_waste', 0):,.0f}"
    for w in waste_items:
        w["platform_label"] = PLATFORM_LABEL.get(w.get("platform", ""), "")
        w["waste_display"] = f"{w.get('waste_amount', 0):,.0f}"

    # Quick wins
    quick_wins = audit.get("quick_wins", [])
    for qw in quick_wins:
        qw["platform_label"] = PLATFORM_LABEL.get(qw.get("platform", ""), "")
        qw.setdefault("severity", "medium")

    # Executive summary items
    summary_items = _build_summary_items(audit, anomalies, waste, fraud)

    # Fraud action display
    if fraud_action:
        fraud_action["estimated_savings_display"] = f"{fraud_action.get('estimated_savings', 0):,.0f}"

    data = {
        "client_id": client_id,
        "client_name": results.get("client_name", client_id),
        "timestamp": results.get("timestamp", ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "score": score,
        "grade": grade,
        "grade_class": grade.lower(),
        "grade_description": GRADE_DESC.get(grade, ""),
        "executive_summary": _build_executive_summary(audit),
        "summary_items": summary_items,
        "platform_count": len(platforms),
        "total_checks": audit.get("total_checks", 0),
        "alert_count": len(alerts),
        "savings_display": f"{waste.get('total_waste', 0):,.0f}",
        "total_cost_display": f"{total_cost:,.0f}",
        "avg_cpa_display": f"{audit.get('avg_cpa', 0):,.0f}",
        "campaign_count": audit.get("total_campaigns", 0),
        "total_cv": total_cv,
        "issue_count": len(issues),
        "critical_count": critical_count,
        "platforms": platforms,
        "alerts": alerts,
        "quick_wins": quick_wins[:10],
        "waste_items": waste_items,
        "waste_savings": waste_savings,
        # 新規: Phase 3-4
        "fraud_audit": fraud if fraud else None,
        "fraud_action": fraud_action if fraud_action else None,
        "conflicts": conflicts if conflicts else None,
        "claude_analysis": claude if claude and not claude.get("skipped") else None,
    }

    return data


def _build_executive_summary(audit):
    """エグゼクティブサマリーテキスト生成"""
    score = audit.get("score", 0)
    grade = audit.get("grade", "F")
    failed = audit.get("failed_checks", 0)

    if score >= 90:
        return f"総合スコア {score}/100 ({grade}) — アカウントは良好な状態です。微細な最適化余地があります。"
    elif score >= 75:
        return f"総合スコア {score}/100 ({grade}) — 基本的な運用は適切ですが、{failed}件の改善ポイントが検出されました。"
    elif score >= 60:
        return f"総合スコア {score}/100 ({grade}) — 改善が推奨される領域が多く見られます。{failed}件の問題を優先度順に対応してください。"
    elif score >= 40:
        return f"総合スコア {score}/100 ({grade}) — 構造的な見直しが必要です。{failed}件の問題のうち、重大な問題から対応を開始してください。"
    else:
        return f"総合スコア {score}/100 ({grade}) — 早急な対応が必要です。{failed}件の問題が検出され、パフォーマンスへの影響が懸念されます。"


def _build_summary_items(audit, anomalies, waste, fraud):
    """サマリーアイテムリスト生成"""
    items = []
    critical = len([i for i in audit.get("issues", []) if i.get("severity") == "critical"])
    if critical > 0:
        items.append({
            "icon": "!", "bg": "#FCEBEB", "color": "#A32D2D",
            "text": f"重大問題 {critical}件 — 早急な対応が必要",
        })

    alerts = anomalies.get("alerts", [])
    critical_alerts = [a for a in alerts if a.get("severity") == "critical"]
    if critical_alerts:
        items.append({
            "icon": "⚠", "bg": "#FAEEDA", "color": "#854F0B",
            "text": f"異常検知: {len(critical_alerts)}件の重大アラート",
        })

    total_waste = waste.get("total_waste", 0)
    if total_waste > 0:
        items.append({
            "icon": "¥", "bg": "#FAEEDA", "color": "#854F0B",
            "text": f"推定無駄コスト: ¥{total_waste:,.0f} の削減余地",
        })

    fraud_rate = fraud.get("fraud_rate", 0) if fraud else 0
    if fraud_rate > 10:
        items.append({
            "icon": "🛡", "bg": "#FCEBEB", "color": "#A32D2D",
            "text": f"不正率 {fraud_rate:.1f}% — 対策が必要",
        })

    if not items:
        items.append({
            "icon": "✓", "bg": "#EAF3DE", "color": "#27500A",
            "text": "重大な問題は検出されませんでした",
        })

    return items
