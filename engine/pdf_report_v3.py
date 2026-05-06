"""v3 PDF レポート生成 — Playwright + Jinja2。

設計: docs/report_design/v3_structure.md
- v2 (templates/report.html) は触らず、v3 は templates/v3/ 以下を読み込む
- engine/report_generator_v3.build_v3_context が用意したコンテキストを渡してレンダリング
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("bpo")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
V3_TEMPLATE = "v3/report_v3.html"


def generate_pdf_v3(client_id: str, client_cfg: dict, results: dict, pdf_path: str) -> bool:
    """v3 レポートを HTML/PDF として生成する。

    Returns:
        True: 成功（PDF 生成に到達）
        False: 失敗（HTML だけ生成 or 例外）
    """
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError:
        log.error(f"[{client_id}] jinja2 未インストール: pip install jinja2")
        return False

    try:
        from engine.report_generator_v3 import build_v3_context
    except ImportError as e:
        log.error(f"[{client_id}] v3 generator import 失敗: {e}")
        return False

    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # コンテキスト構築
    context = build_v3_context(client_id, client_cfg, results)

    # Jinja2 レンダリング
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["abs"] = abs
    template = env.get_template(V3_TEMPLATE)
    html_content = template.render(**context)

    # HTML を必ず保存（PDF 失敗時のフォールバック閲覧用）
    html_path = pdf_path.replace(".pdf", ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    log.info(f"[{client_id}] v3 HTML 保存: {html_path}")

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
                margin={"top": "12mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template='<div style="font-size:9px;font-family:sans-serif;color:#aaa;width:100%;text-align:center;padding:0 20px;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
            )
            browser.close()
        log.info(f"[{client_id}] v3 PDF 生成完了: {pdf_path}")
        return True
    except Exception as e:
        log.error(f"[{client_id}] v3 PDF 生成失敗（HTML 利用可能）: {e}")
        return False
