"""SEO監査 - サイトの技術SEO・パフォーマンスをチェック"""
import os
import json
import logging
import urllib.request
import urllib.parse
import re

log = logging.getLogger("bpo")

PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run_seo_audit(client_id, seo_cfg):
    """SEO監査を実行"""
    site_url = seo_cfg.get("site_url", "")
    landing_pages = seo_cfg.get("landing_pages", [])
    all_urls = [site_url] + landing_pages if site_url else landing_pages

    if not all_urls:
        log.warning(f"[{client_id}] SEO対象URLなし")
        return {"status": "skipped", "reason": "No URLs configured"}

    results = {
        "status": "completed",
        "pages": [],
        "summary": {},
    }

    for url in all_urls:
        if not url:
            continue
        log.info(f"[{client_id}] SEO分析中: {url}")
        page_result = _analyze_page(url)
        results["pages"].append(page_result)

    # サマリー計算
    pages = results["pages"]
    if pages:
        scores = [p.get("performance_score", 0) for p in pages]
        results["summary"] = {
            "total_pages": len(pages),
            "avg_performance": round(sum(scores) / len(scores)),
            "issues_count": sum(p.get("issues_count", 0) for p in pages),
            "critical_count": sum(p.get("critical_count", 0) for p in pages),
        }

        # SEOグレード
        avg = results["summary"]["avg_performance"]
        if avg >= 90:
            results["summary"]["grade"] = "A"
        elif avg >= 75:
            results["summary"]["grade"] = "B"
        elif avg >= 60:
            results["summary"]["grade"] = "C"
        elif avg >= 40:
            results["summary"]["grade"] = "D"
        else:
            results["summary"]["grade"] = "F"

    log.info(f"[{client_id}] SEO監査完了: {len(pages)}ページ分析")
    return results


def _analyze_page(url):
    """1ページのSEO分析"""
    result = {
        "url": url,
        "performance_score": 0,
        "metrics": {},
        "issues": [],
        "issues_count": 0,
        "critical_count": 0,
    }

    # 1. PageSpeed Insights
    try:
        psi = _fetch_pagespeed(url)
        if psi:
            result["performance_score"] = psi.get("score", 0)
            result["metrics"] = psi.get("metrics", {})

            # パフォーマンス問題チェック
            metrics = psi.get("metrics", {})

            lcp = metrics.get("lcp", 0)
            if lcp > 4000:
                result["issues"].append({
                    "severity": "critical",
                    "type": "LCP",
                    "message": f"LCP {lcp/1000:.1f}秒 (目標: 2.5秒以下)",
                    "action": "画像最適化、サーバー応答時間改善",
                })
            elif lcp > 2500:
                result["issues"].append({
                    "severity": "warning",
                    "type": "LCP",
                    "message": f"LCP {lcp/1000:.1f}秒 (目標: 2.5秒以下)",
                    "action": "画像圧縮、CDN導入を検討",
                })

            fcp = metrics.get("fcp", 0)
            if fcp > 3000:
                result["issues"].append({
                    "severity": "critical",
                    "type": "FCP",
                    "message": f"FCP {fcp/1000:.1f}秒 (目標: 1.8秒以下)",
                    "action": "レンダリングブロックリソースの削減",
                })

            cls = metrics.get("cls", 0)
            if cls > 0.25:
                result["issues"].append({
                    "severity": "critical",
                    "type": "CLS",
                    "message": f"CLS {cls:.3f} (目標: 0.1以下)",
                    "action": "画像・広告にサイズ属性を指定",
                })
            elif cls > 0.1:
                result["issues"].append({
                    "severity": "warning",
                    "type": "CLS",
                    "message": f"CLS {cls:.3f} (目標: 0.1以下)",
                    "action": "レイアウトシフトの原因を特定",
                })

            tbt = metrics.get("tbt", 0)
            if tbt > 600:
                result["issues"].append({
                    "severity": "critical",
                    "type": "TBT",
                    "message": f"TBT {tbt:.0f}ms (目標: 200ms以下)",
                    "action": "JavaScript実行時間の削減",
                })

            speed_index = metrics.get("speed_index", 0)
            if speed_index > 5800:
                result["issues"].append({
                    "severity": "warning",
                    "type": "SpeedIndex",
                    "message": f"Speed Index {speed_index/1000:.1f}秒 (目標: 3.4秒以下)",
                    "action": "表示コンテンツの優先読み込み",
                })

    except Exception as e:
        log.error(f"PageSpeed取得エラー ({url}): {e}")
        result["issues"].append({
            "severity": "warning",
            "type": "PageSpeed",
            "message": f"PageSpeed分析失敗: {str(e)[:100]}",
            "action": "URL確認またはサイトの稼働状況確認",
        })

    # 2. 基本HTMLチェック
    try:
        html_issues = _check_html(url)
        result["issues"].extend(html_issues)
    except Exception as e:
        log.error(f"HTMLチェックエラー ({url}): {e}")

    result["issues_count"] = len(result["issues"])
    result["critical_count"] = len([i for i in result["issues"] if i["severity"] == "critical"])

    return result


def _fetch_pagespeed(url):
    """PageSpeed Insights APIからデータ取得"""
    params = urllib.parse.urlencode({
        "url": url,
        "strategy": "mobile",
        "category": "performance",
    })
    api_url = f"{PAGESPEED_API}?{params}"

    req = urllib.request.Request(api_url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    perf = categories.get("performance", {})
    score = int(perf.get("score", 0) * 100)

    audits = lighthouse.get("audits", {})

    metrics = {
        "lcp": audits.get("largest-contentful-paint", {}).get("numericValue", 0),
        "fcp": audits.get("first-contentful-paint", {}).get("numericValue", 0),
        "cls": audits.get("cumulative-layout-shift", {}).get("numericValue", 0),
        "tbt": audits.get("total-blocking-time", {}).get("numericValue", 0),
        "speed_index": audits.get("speed-index", {}).get("numericValue", 0),
        "tti": audits.get("interactive", {}).get("numericValue", 0),
    }

    return {"score": score, "metrics": metrics}


def _check_html(url):
    """基本的なHTMLチェック"""
    issues = []
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; BPOBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

            # titleタグ
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if not title_match or not title_match.group(1).strip():
                issues.append({
                    "severity": "critical",
                    "type": "title",
                    "message": "titleタグが空または存在しない",
                    "action": "ページのtitleタグを適切に設定",
                })
            elif len(title_match.group(1).strip()) > 60:
                issues.append({
                    "severity": "warning",
                    "type": "title",
                    "message": f"titleタグが長すぎる ({len(title_match.group(1).strip())}文字、推奨60文字以内)",
                    "action": "titleを60文字以内に短縮",
                })

            # meta description
            desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.IGNORECASE)
            if not desc_match or not desc_match.group(1).strip():
                issues.append({
                    "severity": "warning",
                    "type": "meta_description",
                    "message": "meta descriptionが未設定",
                    "action": "120-160文字のdescriptionを設定",
                })

            # h1タグ
            h1_matches = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            if not h1_matches:
                issues.append({
                    "severity": "warning",
                    "type": "h1",
                    "message": "h1タグが存在しない",
                    "action": "ページに1つのh1タグを追加",
                })
            elif len(h1_matches) > 1:
                issues.append({
                    "severity": "warning",
                    "type": "h1",
                    "message": f"h1タグが{len(h1_matches)}個（推奨1個）",
                    "action": "h1を1つに統一し、他はh2-h6に変更",
                })

            # viewport
            if "viewport" not in html.lower():
                issues.append({
                    "severity": "critical",
                    "type": "viewport",
                    "message": "viewportメタタグ未設定（モバイル非対応）",
                    "action": "meta viewportタグを追加",
                })

            # HTTPS チェック
            if url.startswith("http://"):
                issues.append({
                    "severity": "critical",
                    "type": "https",
                    "message": "HTTPSが未使用",
                    "action": "SSL証明書を導入しHTTPSに移行",
                })

            # canonical
            if "canonical" not in html.lower():
                issues.append({
                    "severity": "warning",
                    "type": "canonical",
                    "message": "canonicalタグ未設定",
                    "action": "rel=canonicalを設定して重複コンテンツを防止",
                })

    except Exception as e:
        issues.append({
            "severity": "warning",
            "type": "access",
            "message": f"ページアクセス失敗: {str(e)[:100]}",
            "action": "URLまたはサーバー設定を確認",
        })

    return issues
