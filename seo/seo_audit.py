"""SEO監査 v2.0 - Claude SEO Layer1 対応 22チェック"""
import os
import json
import logging
import urllib.request
import urllib.parse
import re

log = logging.getLogger("bpo")

PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run_seo_audit(client_id, seo_cfg, thresholds=None):
    seo_thresh = (thresholds or {}).get("seo", {})
    site_url = seo_cfg.get("site_url", "")
    landing_pages = seo_cfg.get("landing_pages", [])
    all_urls = [site_url] + landing_pages if site_url else landing_pages

    if not all_urls:
        log.warning(f"[{client_id}] SEO対象URLなし")
        return {"status": "skipped", "reason": "No URLs configured"}

    results = {"status": "completed", "pages": [], "site_checks": [], "summary": {}}

    if site_url:
        results["site_checks"] = _check_site_level(site_url)

    for url in all_urls:
        if not url:
            continue
        log.info(f"[{client_id}] SEO分析中: {url}")
        page_result = _analyze_page(url, seo_thresh)
        results["pages"].append(page_result)

    pages = results["pages"]
    if pages:
        scores = [p.get("performance_score", 0) for p in pages]
        all_issues = []
        for p in pages:
            all_issues.extend(p.get("issues", []))
        all_issues.extend(results.get("site_checks", []))
        avg = round(sum(scores) / len(scores)) if scores else 0
        results["summary"] = {
            "total_pages": len(pages), "avg_performance": avg,
            "issues_count": len(all_issues),
            "critical_count": len([i for i in all_issues if i.get("severity") == "critical"]),
            "high_count": len([i for i in all_issues if i.get("severity") == "high"]),
            "check_count": 22,
        }
        if avg >= 90: results["summary"]["grade"] = "A"
        elif avg >= 75: results["summary"]["grade"] = "B"
        elif avg >= 60: results["summary"]["grade"] = "C"
        elif avg >= 40: results["summary"]["grade"] = "D"
        else: results["summary"]["grade"] = "F"

    log.info(f"[{client_id}] SEO監査完了: {len(pages)}ページ分析")
    return results


def _check_site_level(site_url):
    issues = []
    domain = site_url.rstrip("/")

    try:
        req = urllib.request.Request(f"{domain}/robots.txt", headers={"User-Agent": "BPOBot/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            robots = resp.read().decode("utf-8", errors="ignore")
            if "Disallow: /" in robots and "Allow:" not in robots:
                issues.append({"check_id": "S01", "severity": "critical", "type": "robots.txt",
                    "message": "robots.txt がサイト全体をブロック", "action": "Disallow: / を削除"})
            if "sitemap" not in robots.lower():
                issues.append({"check_id": "S02", "severity": "medium", "type": "robots.txt",
                    "message": "robots.txt に Sitemap 参照なし", "action": "Sitemap: URL を追加"})
    except Exception:
        issues.append({"check_id": "S01", "severity": "high", "type": "robots.txt",
            "message": "robots.txt が存在しないかアクセス不可", "action": "robots.txt を作成"})

    try:
        req = urllib.request.Request(f"{domain}/sitemap.xml", headers={"User-Agent": "BPOBot/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            sitemap = resp.read().decode("utf-8", errors="ignore")
            url_count = sitemap.count("<loc>")
            if url_count == 0:
                issues.append({"check_id": "S03", "severity": "high", "type": "sitemap",
                    "message": "sitemap.xml に URL なし", "action": "有効な URL を追加"})
            elif url_count > 50000:
                issues.append({"check_id": "S03", "severity": "medium", "type": "sitemap",
                    "message": f"sitemap に {url_count:,} URL（上限50,000）", "action": "インデックスに分割"})
    except Exception:
        issues.append({"check_id": "S03", "severity": "high", "type": "sitemap",
            "message": "sitemap.xml が存在しないかアクセス不可", "action": "sitemap.xml を作成"})

    if site_url.startswith("https://"):
        try:
            http_url = site_url.replace("https://", "http://")
            req = urllib.request.Request(http_url, headers={"User-Agent": "BPOBot/2.0"}, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if not resp.geturl().startswith("https://"):
                    issues.append({"check_id": "S04", "severity": "critical", "type": "https_redirect",
                        "message": "HTTP→HTTPS リダイレクト未設定", "action": "301リダイレクトを設定"})
        except Exception:
            pass

    return issues


def _analyze_page(url, seo_thresh):
    result = {"url": url, "performance_score": 0, "metrics": {}, "issues": [], "issues_count": 0, "critical_count": 0}
    psi_cfg = seo_thresh.get("pagespeed", {})
    html_cfg = seo_thresh.get("html", {})

    try:
        psi = _fetch_pagespeed(url)
        if psi:
            result["performance_score"] = psi.get("score", 0)
            result["metrics"] = psi.get("metrics", {})
            m = psi.get("metrics", {})
            lcp = m.get("lcp", 0)
            lcp_max = psi_cfg.get("lcp_max_ms", 2500)
            if lcp > lcp_max * 1.6:
                result["issues"].append({"check_id": "P01", "severity": "critical", "type": "LCP",
                    "message": f"LCP {lcp/1000:.1f}秒（目標 {lcp_max/1000:.1f}秒）", "action": "画像最適化・サーバー応答改善"})
            elif lcp > lcp_max:
                result["issues"].append({"check_id": "P01", "severity": "high", "type": "LCP",
                    "message": f"LCP {lcp/1000:.1f}秒（目標 {lcp_max/1000:.1f}秒）", "action": "画像圧縮・CDN導入"})
            cls = m.get("cls", 0)
            cls_max = psi_cfg.get("cls_max", 0.1)
            if cls > cls_max * 2.5:
                result["issues"].append({"check_id": "P02", "severity": "critical", "type": "CLS",
                    "message": f"CLS {cls:.3f}（目標 {cls_max}）", "action": "画像・広告にサイズ属性指定"})
            elif cls > cls_max:
                result["issues"].append({"check_id": "P02", "severity": "high", "type": "CLS",
                    "message": f"CLS {cls:.3f}（目標 {cls_max}）", "action": "レイアウトシフト原因特定"})
            tbt = m.get("tbt", 0)
            inp_max = psi_cfg.get("inp_max_ms", 200)
            if tbt > inp_max * 3:
                result["issues"].append({"check_id": "P03", "severity": "critical", "type": "TBT",
                    "message": f"TBT {tbt:.0f}ms（目標 {inp_max}ms）", "action": "JS実行時間削減"})
            elif tbt > inp_max:
                result["issues"].append({"check_id": "P03", "severity": "high", "type": "TBT",
                    "message": f"TBT {tbt:.0f}ms（目標 {inp_max}ms）", "action": "重いJSの遅延読込"})
            fcp = m.get("fcp", 0)
            if fcp > 3000:
                result["issues"].append({"check_id": "P04", "severity": "critical", "type": "FCP",
                    "message": f"FCP {fcp/1000:.1f}秒（目標 1.8秒）", "action": "レンダリングブロック削減"})
            elif fcp > 1800:
                result["issues"].append({"check_id": "P04", "severity": "high", "type": "FCP",
                    "message": f"FCP {fcp/1000:.1f}秒（目標 1.8秒）", "action": "CSS/JS最適化"})
            perf_min = psi_cfg.get("performance_min", 50)
            if psi.get("score", 0) < perf_min:
                result["issues"].append({"check_id": "P05", "severity": "high", "type": "performance",
                    "message": f"スコア {psi['score']}（基準 {perf_min}）", "action": "CWV全体改善"})
    except Exception as e:
        log.error(f"PageSpeed取得エラー ({url}): {e}")
        result["issues"].append({"check_id": "P00", "severity": "medium", "type": "PageSpeed",
            "message": f"PageSpeed分析失敗: {str(e)[:100]}", "action": "URL確認"})

    try:
        result["issues"].extend(_check_html(url, html_cfg))
    except Exception as e:
        log.error(f"HTMLチェックエラー ({url}): {e}")

    result["issues_count"] = len(result["issues"])
    result["critical_count"] = len([i for i in result["issues"] if i.get("severity") == "critical"])
    return result


def _fetch_pagespeed(url):
    params = urllib.parse.urlencode({"url": url, "strategy": "mobile", "category": "performance"})
    req = urllib.request.Request(f"{PAGESPEED_API}?{params}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    lh = data.get("lighthouseResult", {})
    score = int(lh.get("categories", {}).get("performance", {}).get("score", 0) * 100)
    a = lh.get("audits", {})
    metrics = {
        "lcp": a.get("largest-contentful-paint", {}).get("numericValue", 0),
        "fcp": a.get("first-contentful-paint", {}).get("numericValue", 0),
        "cls": a.get("cumulative-layout-shift", {}).get("numericValue", 0),
        "tbt": a.get("total-blocking-time", {}).get("numericValue", 0),
        "speed_index": a.get("speed-index", {}).get("numericValue", 0),
        "tti": a.get("interactive", {}).get("numericValue", 0),
    }
    return {"score": score, "metrics": metrics}


def _check_html(url, html_cfg):
    issues = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BPOBot/2.0)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            headers = dict(resp.headers)
            html = resp.read().decode("utf-8", errors="ignore")

            t = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            t_min = html_cfg.get("title_min_len", 30)
            t_max = html_cfg.get("title_max_len", 60)
            if not t or not t.group(1).strip():
                issues.append({"check_id": "H01", "severity": "critical", "type": "title",
                    "message": "titleタグが空/存在しない", "action": "titleを設定"})
            else:
                tl = len(t.group(1).strip())
                if tl < t_min:
                    issues.append({"check_id": "H01", "severity": "medium", "type": "title",
                        "message": f"title {tl}文字（推奨{t_min}以上）", "action": "titleを拡張"})
                elif tl > t_max:
                    issues.append({"check_id": "H01", "severity": "medium", "type": "title",
                        "message": f"title {tl}文字（推奨{t_max}以内）", "action": "titleを短縮"})

            d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)
            d_min = html_cfg.get("meta_desc_min_len", 70)
            d_max = html_cfg.get("meta_desc_max_len", 160)
            if not d or not d.group(1).strip():
                issues.append({"check_id": "H02", "severity": "high", "type": "meta_description",
                    "message": "meta description未設定", "action": f"{d_min}-{d_max}文字で設定"})
            else:
                dl = len(d.group(1).strip())
                if dl < d_min or dl > d_max:
                    issues.append({"check_id": "H02", "severity": "medium", "type": "meta_description",
                        "message": f"description {dl}文字（推奨{d_min}-{d_max}）", "action": "文字数調整"})

            h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
            if not h1s:
                issues.append({"check_id": "H03", "severity": "high", "type": "h1",
                    "message": "h1タグなし", "action": "h1を追加"})
            elif len(h1s) > 1:
                issues.append({"check_id": "H03", "severity": "medium", "type": "h1",
                    "message": f"h1が{len(h1s)}個（推奨1個）", "action": "h1を統一"})

            if "viewport" not in html.lower():
                issues.append({"check_id": "H04", "severity": "critical", "type": "viewport",
                    "message": "viewport未設定", "action": "meta viewportを追加"})

            if url.startswith("http://"):
                issues.append({"check_id": "H05", "severity": "critical", "type": "https",
                    "message": "HTTPS未使用", "action": "SSL導入"})

            if not re.search(r'<link[^>]+rel=["\']canonical["\']', html, re.I):
                issues.append({"check_id": "H06", "severity": "high", "type": "canonical",
                    "message": "canonical未設定", "action": "rel=canonicalを設定"})

            missing_og = []
            if not re.search(r'property=["\']og:title["\']', html, re.I): missing_og.append("og:title")
            if not re.search(r'property=["\']og:description["\']', html, re.I): missing_og.append("og:description")
            if not re.search(r'property=["\']og:image["\']', html, re.I): missing_og.append("og:image")
            if missing_og:
                issues.append({"check_id": "H07", "severity": "medium", "type": "ogp",
                    "message": f"OGP未設定: {', '.join(missing_og)}", "action": "OGPタグ追加"})

            if not re.search(r'type=["\']application/ld\+json["\']', html, re.I):
                issues.append({"check_id": "H08", "severity": "medium", "type": "schema",
                    "message": "構造化データ（JSON-LD）未設定", "action": "schemaを追加"})

            imgs = re.findall(r'<img[^>]*>', html, re.I)
            if imgs:
                no_alt = [i for i in imgs if 'alt=' not in i.lower() or 'alt=""' in i.lower()]
                if no_alt:
                    pct = round(len(no_alt) / len(imgs) * 100)
                    issues.append({"check_id": "H09", "severity": "high" if pct > 50 else "medium",
                        "type": "img_alt", "message": f"画像 {len(no_alt)}/{len(imgs)} 個にalt無し（{pct}%）",
                        "action": "全画像にalt設定"})

            if not re.search(r'<html[^>]+lang=', html, re.I):
                issues.append({"check_id": "H10", "severity": "medium", "type": "lang",
                    "message": "lang属性未設定", "action": 'lang="ja"を追加'})

            if not headers.get("Strict-Transport-Security") and url.startswith("https://"):
                issues.append({"check_id": "H11", "severity": "medium", "type": "hsts",
                    "message": "HSTSヘッダー未設定", "action": "HSTSを追加"})

            if "nosniff" not in headers.get("X-Content-Type-Options", "").lower():
                issues.append({"check_id": "H12", "severity": "low", "type": "security_header",
                    "message": "X-Content-Type-Options未設定", "action": "nosniffヘッダー追加"})

            if final_url != url and final_url.rstrip("/") != url.rstrip("/"):
                issues.append({"check_id": "H13", "severity": "medium", "type": "redirect",
                    "message": f"リダイレクト: {url} → {final_url}", "action": "不要なリダイレクト解消"})

    except Exception as e:
        issues.append({"check_id": "H00", "severity": "high", "type": "access",
            "message": f"アクセス失敗: {str(e)[:100]}", "action": "URL確認"})

    return issues
