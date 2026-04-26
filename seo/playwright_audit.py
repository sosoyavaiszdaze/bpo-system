"""Playwright LP 実測 — Core Web Vitals + LP品質チェック (Layer 3)"""
import os
import json
import logging

log = logging.getLogger("bpo")


def run_playwright_audit(urls, timeout_ms=30000):
    """Playwright を使って LP の Core Web Vitals を実測

    Args:
        urls: 検査対象URLリスト
        timeout_ms: ページロードタイムアウト(ms)
    Returns:
        list[dict]: [{url, lcp, cls, inp, ttfb, dom_count, page_size, ...}, ...]
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright 未インストール: pip install playwright && playwright install chromium")
        return [{"url": u, "error": "playwright not installed"} for u in urls]

    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 375, "height": 812},  # iPhone 13
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            )

            for url in urls:
                result = _measure_page(context, url, timeout_ms)
                results.append(result)
                log.info(f"LP実測完了: {url} — LCP {result.get('lcp', 'N/A')}ms, CLS {result.get('cls', 'N/A')}")

            browser.close()

    except Exception as e:
        log.error(f"Playwright 起動エラー: {e}")
        for url in urls:
            if not any(r.get("url") == url for r in results):
                results.append({"url": url, "error": str(e)})

    return results


def _measure_page(context, url, timeout_ms):
    """1ページの CWV を計測"""
    result = {"url": url}

    try:
        page = context.new_page()

        # Performance Observer を注入
        page.add_init_script("""
            window.__cwv = {lcp: 0, cls: 0, inp: Infinity};
            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    window.__cwv.lcp = Math.max(window.__cwv.lcp, entry.startTime);
                }
            }).observe({type: 'largest-contentful-paint', buffered: true});

            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    if (!entry.hadRecentInput) window.__cwv.cls += entry.value;
                }
            }).observe({type: 'layout-shift', buffered: true});

            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    window.__cwv.inp = Math.min(window.__cwv.inp, entry.duration);
                }
            }).observe({type: 'event', buffered: true});
        """)

        # ページロード
        response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        if response:
            result["status_code"] = response.status
            result["is_https"] = url.startswith("https://")

        # 少し待ってCWV収集
        page.wait_for_timeout(3000)

        # CWV 取得
        cwv = page.evaluate("() => window.__cwv")
        result["lcp"] = round(cwv.get("lcp", 0), 1)
        result["cls"] = round(cwv.get("cls", 0), 3)
        inp = cwv.get("inp", 0)
        result["inp"] = round(inp, 1) if inp != float('inf') else None

        # Navigation Timing
        timing = page.evaluate("""() => {
            const t = performance.getEntriesByType('navigation')[0];
            return t ? {
                ttfb: t.responseStart - t.requestStart,
                dom_interactive: t.domInteractive,
                dom_complete: t.domComplete,
                load_time: t.loadEventEnd - t.startTime,
            } : null;
        }""")
        if timing:
            result["ttfb"] = round(timing.get("ttfb", 0), 1)
            result["load_time"] = round(timing.get("load_time", 0), 1)

        # DOM要素数
        result["dom_count"] = page.evaluate("() => document.querySelectorAll('*').length")

        # ページサイズ（転送量）
        result["page_size"] = page.evaluate("""() => {
            return performance.getEntriesByType('resource')
                .reduce((sum, r) => sum + (r.transferSize || 0), 0);
        }""")

        # SEO基本要素
        result["has_title"] = page.evaluate("() => !!document.querySelector('title')?.textContent")
        result["title_length"] = page.evaluate("() => (document.querySelector('title')?.textContent || '').length")
        result["has_h1"] = page.evaluate("() => !!document.querySelector('h1')")
        result["h1_count"] = page.evaluate("() => document.querySelectorAll('h1').length")
        result["has_meta_desc"] = page.evaluate("() => !!document.querySelector('meta[name=\"description\"]')")
        result["has_viewport"] = page.evaluate("() => !!document.querySelector('meta[name=\"viewport\"]')")
        result["has_canonical"] = page.evaluate("() => !!document.querySelector('link[rel=\"canonical\"]')")
        result["has_form"] = page.evaluate("() => !!document.querySelector('form')")
        result["has_cta"] = page.evaluate("""() => {
            return !!document.querySelector('button, a.cta, [class*="cta"], [class*="btn"], input[type="submit"]');
        }""")

        # 画像 alt カバー率
        alt_stats = page.evaluate("""() => {
            const imgs = document.querySelectorAll('img');
            const total = imgs.length;
            const with_alt = [...imgs].filter(i => i.alt && i.alt.trim()).length;
            return {total, with_alt};
        }""")
        result["image_total"] = alt_stats.get("total", 0)
        result["image_with_alt"] = alt_stats.get("with_alt", 0)
        result["alt_coverage"] = round(alt_stats["with_alt"] / alt_stats["total"] * 100, 1) if alt_stats["total"] > 0 else 100

        # 構造化データ
        result["has_json_ld"] = page.evaluate("() => !!document.querySelector('script[type=\"application/ld+json\"]')")

        page.close()

    except Exception as e:
        result["error"] = str(e)
        log.warning(f"LP実測エラー ({url}): {e}")

    return result


def evaluate_results(results, thresholds=None):
    """実測結果を評価してチェックリストに変換"""
    checks = []
    for r in results:
        url = r.get("url", "")
        if r.get("error"):
            continue

        # CWV
        lcp = r.get("lcp", 0)
        if lcp > 0:
            checks.append({"id": "SEO-CWV1", "passed": lcp <= 2500, "campaign": url,
                            "platform": "seo", "message": f"LCP {lcp:.0f}ms" + ("" if lcp <= 2500 else " (≤2500ms推奨)")})
        cls_val = r.get("cls", 0)
        checks.append({"id": "SEO-CWV3", "passed": cls_val <= 0.1, "campaign": url,
                        "platform": "seo", "message": f"CLS {cls_val:.3f}" + ("" if cls_val <= 0.1 else " (≤0.1推奨)")})

        ttfb = r.get("ttfb", 0)
        if ttfb > 0:
            checks.append({"id": "SEO-CWV5", "passed": ttfb <= 800, "campaign": url,
                            "platform": "seo", "message": f"TTFB {ttfb:.0f}ms" + ("" if ttfb <= 800 else " (≤800ms推奨)")})

        # Performance
        dom = r.get("dom_count", 0)
        if dom > 0:
            checks.append({"id": "SEO-PF4", "passed": dom <= 1500, "campaign": url,
                            "platform": "seo", "message": f"DOM要素 {dom}" + ("" if dom <= 1500 else " (≤1500推奨)")})

        # SEO Basics
        checks.append({"id": "SEO-BS1", "passed": r.get("has_title", False) and r.get("title_length", 0) <= 60,
                        "campaign": url, "platform": "seo", "message": "Title" + ("✓" if r.get("has_title") else " なし")})
        checks.append({"id": "SEO-BS3", "passed": r.get("h1_count", 0) == 1,
                        "campaign": url, "platform": "seo", "message": f"H1タグ {r.get('h1_count', 0)}個"})
        checks.append({"id": "SEO-MB1", "passed": r.get("has_viewport", False),
                        "campaign": url, "platform": "seo", "message": "viewport" + ("✓" if r.get("has_viewport") else " なし")})
        checks.append({"id": "SEO-SC1", "passed": r.get("is_https", False),
                        "campaign": url, "platform": "seo", "message": "HTTPS" + ("✓" if r.get("is_https") else " なし")})

        # LP Quality
        has_action = r.get("has_form", False) or r.get("has_cta", False)
        checks.append({"id": "SEO-LP1", "passed": has_action,
                        "campaign": url, "platform": "seo", "message": "フォーム/CTA" + ("✓" if has_action else " なし")})

        load = r.get("load_time", 0)
        if load > 0:
            checks.append({"id": "SEO-LP2", "passed": load <= 3000,
                            "campaign": url, "platform": "seo", "message": f"LP読込 {load:.0f}ms" + ("" if load <= 3000 else " (≤3000ms推奨)")})

    return checks
