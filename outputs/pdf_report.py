"""PDFレポート生成 - ReportLabで日次レポートを作成"""
import os
import logging
from datetime import datetime

log = logging.getLogger("bpo")

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False
    log.warning("reportlab未インストール: pip3 install reportlab")


# 色定義
COLOR_BG = HexColor("#1a1a2e") if HAS_REPORTLAB else None
COLOR_PRIMARY = HexColor("#16213e") if HAS_REPORTLAB else None
COLOR_ACCENT = HexColor("#0f3460") if HAS_REPORTLAB else None
COLOR_GREEN = HexColor("#2ecc71") if HAS_REPORTLAB else None
COLOR_YELLOW = HexColor("#f39c12") if HAS_REPORTLAB else None
COLOR_RED = HexColor("#e74c3c") if HAS_REPORTLAB else None
COLOR_WHITE = HexColor("#ffffff") if HAS_REPORTLAB else None
COLOR_GRAY = HexColor("#95a5a6") if HAS_REPORTLAB else None

GRADE_COLORS = {
    "A": "#2ecc71", "B": "#3498db", "C": "#f39c12", "D": "#e67e22", "F": "#e74c3c"
}


def generate_pdf(client_id, results, pdf_path):
    if not HAS_REPORTLAB:
        log.error(f"[{client_id}] reportlab未インストール、PDF生成スキップ")
        return

    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    c = canvas.Canvas(pdf_path, pagesize=A4)
    w, h = A4
    y = h - 20*mm

    # ヘッダー背景
    c.setFillColor(COLOR_PRIMARY)
    c.rect(0, h - 50*mm, w, 50*mm, fill=1, stroke=0)

    # タイトル
    c.setFillColor(COLOR_WHITE)
    c.setFont("Helvetica-Bold", 24)
    client_name = results.get("client_name", client_id)
    c.drawString(20*mm, h - 25*mm, f"BPO System - Daily Report")
    c.setFont("Helvetica", 14)
    c.drawString(20*mm, h - 35*mm, f"Client: {client_name}")
    timestamp = results.get("timestamp", "")[:10]
    c.drawString(20*mm, h - 43*mm, f"Date: {timestamp}")

    y = h - 65*mm

    # スコアセクション
    audit = results.get("ads_audit") or {}
    score = audit.get("score", "N/A")
    grade = audit.get("grade", "?")
    grade_color = HexColor(GRADE_COLORS.get(grade, "#95a5a6"))

    c.setFillColor(grade_color)
    c.circle(45*mm, y - 5*mm, 15*mm, fill=1, stroke=0)
    c.setFillColor(COLOR_WHITE)
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(45*mm, y - 10*mm, str(score))
    c.setFont("Helvetica", 10)
    c.drawCentredString(45*mm, y - 16*mm, f"Grade {grade}")

    # サマリー数値
    c.setFillColor(HexColor("#333333"))
    c.setFont("Helvetica-Bold", 12)
    x_start = 80*mm
    c.drawString(x_start, y, "Summary")
    c.setFont("Helvetica", 10)
    metrics = [
        f"Campaigns: {audit.get('total_campaigns', 0)}",
        f"Total Cost: Y{audit.get('total_cost', 0):,.0f}",
        f"Total CV: {audit.get('total_conversions', 0):.0f}",
        f"Avg CPA: Y{audit.get('avg_cpa', 0):,.0f}",
        f"Avg CTR: {audit.get('avg_ctr', 0):.2f}%",
    ]
    for i, m in enumerate(metrics):
        c.drawString(x_start, y - (i + 1) * 14, m)

    y -= 90

    # Critical Issues
    issues = audit.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]
    if critical:
        c.setFillColor(COLOR_RED)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(20*mm, y, "Critical Issues")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor("#333333"))
        for issue in critical[:5]:
            c.drawString(25*mm, y, f"- [{issue['campaign']}] {issue['issue']}")
            y -= 14
            c.setFillColor(COLOR_ACCENT)
            c.drawString(30*mm, y, f"Action: {issue['action']}")
            c.setFillColor(HexColor("#333333"))
            y -= 18
        y -= 10

    # Anomalies
    anomalies = results.get("anomalies") or {}
    alerts = anomalies.get("alerts", [])
    if alerts:
        c.setFillColor(COLOR_YELLOW)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(20*mm, y, "Anomaly Alerts")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor("#333333"))
        for a in alerts[:5]:
            camp = a.get("campaign", "Overall")
            c.drawString(25*mm, y, f"- [{camp}] {a['message']}")
            y -= 14
            c.drawString(30*mm, y, f"Cause: {a['cause']}")
            y -= 14
            c.setFillColor(COLOR_ACCENT)
            c.drawString(30*mm, y, f"Action: {a['action']}")
            c.setFillColor(HexColor("#333333"))
            y -= 18
        y -= 10

    # Waste
    waste = results.get("waste") or {}
    waste_items = waste.get("waste_items", [])
    if waste_items:
        c.setFillColor(COLOR_RED)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(20*mm, y, f"Wasted Budget: {waste.get('potential_savings', 'Y0')}")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor("#333333"))
        for w in waste_items[:5]:
            c.drawString(25*mm, y, f"- [{w['campaign']}] {w['message']}")
            y -= 14
            c.setFillColor(COLOR_ACCENT)
            c.drawString(30*mm, y, f"Action: {w['action']}")
            c.setFillColor(HexColor("#333333"))
            y -= 18
        y -= 10

    # Quick Wins
    quick_wins = audit.get("quick_wins", [])
    if quick_wins:
        c.setFillColor(COLOR_GREEN)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(20*mm, y, "Quick Wins")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor("#333333"))
        for q in quick_wins[:5]:
            c.drawString(25*mm, y, f"- [{q['campaign']}] {q['action']}")
            y -= 16
        y -= 10

    # SEO
    seo = results.get("seo_audit") or {}
    if seo and seo.get("status") != "stub":
        if y < 100:
            c.showPage()
            y = h - 30*mm
        c.setFillColor(HexColor("#8e44ad"))
        c.setFont("Helvetica-Bold", 13)
        c.drawString(20*mm, y, "SEO Audit")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor("#333333"))
        c.drawString(25*mm, y, f"Site: {seo.get('site_url', 'N/A')}")
        y -= 16

    # フッター
    c.setFillColor(COLOR_GRAY)
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 15*mm, f"BPO System v1.0 | Generated: {datetime.now():%Y-%m-%d %H:%M}")
    c.drawRightString(w - 20*mm, 15*mm, "Confidential")

    c.save()
    log.info(f"[{client_id}] PDF生成完了: {pdf_path}")
