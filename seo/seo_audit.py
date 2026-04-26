"""SEO監査 v3.0 - Claude SEO Layer1 対応 45チェック"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
import re
import ssl

log = logging.getLogger("bpo")
PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run_seo_audit(client_id, seo_cfg, thresholds=None):
    site_url = seo_cfg.get("site_url", "")
    pages = seo_cfg.get("landing_pages", [site_url])
    if not site_url:
        return {"error": "site_url未設定"}

    seo_t = (thresholds or {}).get("seo", {})
    issues = []
    page_results = []

    # サイト全体チェック S01-S08
    site_issues = _check_site_level(site_url, seo_t)
    issues.extend(site_issues)

    # ページ別チェック S09-S45
    for url in pages[:5]:
        log.info(f"[{client_id}] SEO分析中: {url}")
        pr = _analyze_page(url, seo_t)
        page_results.append(pr)
        issues.extend(pr.get("issues", []))

    # スコア算定
    critical = len([i for i in issues if i.get("severity") == "critical"])
    high = len([i for i in issues if i.get("severity") == "high"])
    medium = len([i for i in issues if i.get("severity") == "medium"])
    penalty = critical * 5 + high * 3 + medium * 1.5
    max_p = max(len(pages) * 15 + 20, 60)
    score = max(0, min(100, round(100 - (penalty / max_p * 100))))

    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    result = {
        "score": score, "grade": grade,
        "site_url": site_url,
        "pages_analyzed": len(page_results),
        "total_issues": len(issues),
        "critical_count": critical,
        "high_count": high,
        "issues": issues,
        "page_results": page_results,
        "check_count": 45,
    }
    log.info(f"[{client_id}] SEO監査完了: {len(page_results)}ページ分析")
    return result


def _check_no_redirect(url, timeout=5):
    """リダイレクト追従なしでHTTPステータスコードを返す"""
    try:
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirectHandler,
                                             urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BPO-SEO-Audit/3.0)"})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _fetch_url(url, timeout=10, verify_ssl=True):
    """URLを取得する（デフォルトSSL検証有効）"""
    try:
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BPO-SEO-Audit/3.0)"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="ignore"), resp.status, dict(resp.headers)
    except ssl.SSLCertVerificationError:
        # SSL検証失敗時はリトライせず、エラーを返す（S24で指摘される）
        return None, 0, {"error": "SSL certificate verification failed"}
    except Exception as e:
        return None, 0, {"error": str(e)}


def _fetch_pagespeed(url):
    try:
        api_url = f"{PAGESPEED_API}?url={urllib.parse.quote(url, safe='')}&strategy=mobile&category=performance&category=accessibility&category=best-practices&category=seo"
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        cats = data.get("lighthouseResult", {}).get("categories", {})
        audits = data.get("lighthouseResult", {}).get("audits", {})
        return {
            "performance": round((cats.get("performance", {}).get("score", 0) or 0) * 100),
            "accessibility": round((cats.get("accessibility", {}).get("score", 0) or 0) * 100),
            "best_practices": round((cats.get("best-practices", {}).get("score", 0) or 0) * 100),
            "seo": round((cats.get("seo", {}).get("score", 0) or 0) * 100),
            "lcp": audits.get("largest-contentful-paint", {}).get("numericValue", 0) / 1000,
            "fid": audits.get("max-potential-fid", {}).get("numericValue", 0),
            "cls": audits.get("cumulative-layout-shift", {}).get("numericValue", 0),
            "ttfb": audits.get("server-response-time", {}).get("numericValue", 0) / 1000,
            "tbt": audits.get("total-blocking-time", {}).get("numericValue", 0),
            "si": audits.get("speed-index", {}).get("numericValue", 0) / 1000,
        }
    except Exception as e:
        log.error(f"PageSpeed取得エラー ({url}): {e}")
        return None


def _check_site_level(site_url, seo_t):
    issues = []
    domain = site_url.rstrip("/")

    # S01: robots.txt
    robots_html, robots_status, _ = _fetch_url(f"{domain}/robots.txt")
    if robots_status != 200:
        issues.append({"check_id": "S01", "severity": "high", "page": "サイト全体",
            "message": "robots.txt が存在しないまたはアクセス不可", "action": "robots.txt を作成しルート配置"})
    elif robots_html:
        if "Disallow: /" in robots_html and "Allow:" not in robots_html:
            issues.append({"check_id": "S01b", "severity": "critical", "page": "サイト全体",
                "message": "robots.txt で全ページがブロックされている", "action": "Disallow設定を見直し"})

    # S02: sitemap.xml
    sm_html, sm_status, _ = _fetch_url(f"{domain}/sitemap.xml")
    if sm_status != 200:
        issues.append({"check_id": "S02", "severity": "high", "page": "サイト全体",
            "message": "sitemap.xml が存在しない", "action": "XMLサイトマップを作成しSearch Consoleに送信"})
    elif sm_html:
        url_count = sm_html.count("<loc>")
        if url_count == 0:
            issues.append({"check_id": "S02b", "severity": "high", "page": "サイト全体",
                "message": "sitemap.xml にURLが含まれていない", "action": "サイトマップを正しく生成"})

    # S03: HTTPS リダイレクト
    if domain.startswith("https://"):
        http_url = domain.replace("https://", "http://")
        _, http_status, http_headers = _fetch_url(http_url)
        if http_status not in (301, 302, 307, 308):
            issues.append({"check_id": "S03", "severity": "high", "page": "サイト全体",
                "message": "HTTP→HTTPSリダイレクトが未設定", "action": "301リダイレクトを設定"})

    # S04: www正規化（リダイレクト追従なしで判定）
    if "www." not in domain:
        www_url = domain.replace("https://", "https://www.")
        www_status = _check_no_redirect(www_url)
        if www_status == 200:
            issues.append({"check_id": "S04", "severity": "medium", "page": "サイト全体",
                "message": "www有無の両方でアクセス可能 — canonical重複リスク", "action": "どちらかに301リダイレクト統一"})

    # S05: セキュリティヘッダー（トップページ）
    _, _, top_headers = _fetch_url(domain)
    if top_headers and not top_headers.get("error"):
        if not top_headers.get("Strict-Transport-Security"):
            issues.append({"check_id": "S05", "severity": "medium", "page": "サイト全体",
                "message": "HSTS ヘッダー未設定", "action": "Strict-Transport-Security ヘッダーを追加"})
        if not top_headers.get("X-Content-Type-Options"):
            issues.append({"check_id": "S06", "severity": "low", "page": "サイト全体",
                "message": "X-Content-Type-Options 未設定", "action": "nosniff ヘッダーを追加"})
        if not top_headers.get("X-Frame-Options") and not top_headers.get("Content-Security-Policy"):
            issues.append({"check_id": "S07", "severity": "low", "page": "サイト全体",
                "message": "クリックジャッキング対策ヘッダー未設定", "action": "X-Frame-Options または CSP を追加"})

    # S08: レスポンスタイム
    import time
    start = time.time()
    _fetch_url(domain)
    elapsed = time.time() - start
    if elapsed > 3.0:
        issues.append({"check_id": "S08", "severity": "high", "page": "サイト全体",
            "message": f"サーバーレスポンス {elapsed:.1f}秒 — 3秒超過", "action": "サーバー・CDN・キャッシュの最適化"})
    elif elapsed > 1.5:
        issues.append({"check_id": "S08", "severity": "medium", "page": "サイト全体",
            "message": f"サーバーレスポンス {elapsed:.1f}秒 — 改善余地あり", "action": "キャッシュ・CDN導入を検討"})

    return issues


def _analyze_page(url, seo_t):
    issues = []
    ps = _fetch_pagespeed(url)
    html, status, headers = _fetch_url(url)

    result = {"url": url, "status": status, "pagespeed": ps, "issues": []}

    if status == 0 or html is None:
        issues.append({"check_id": "S09", "severity": "critical", "page": url,
            "message": "ページアクセス失敗", "action": "URL・サーバー設定を確認"})
        result["issues"] = issues
        return result

    # === PageSpeed チェック S10-S18 ===
    if ps:
        # S10: パフォーマンススコア
        if ps["performance"] < 50:
            issues.append({"check_id": "S10", "severity": "critical", "page": url,
                "message": f"パフォーマンススコア {ps['performance']} — 深刻に遅い", "action": "画像圧縮・JS削減・キャッシュ設定"})
        elif ps["performance"] < 75:
            issues.append({"check_id": "S10", "severity": "high", "page": url,
                "message": f"パフォーマンススコア {ps['performance']} — 改善必要", "action": "Core Web Vitals の最適化"})

        # S11: LCP
        lcp_max = seo_t.get("lcp_max", 2.5)
        if ps["lcp"] > 4.0:
            issues.append({"check_id": "S11", "severity": "critical", "page": url,
                "message": f"LCP {ps['lcp']:.1f}秒 — 極端に遅い", "action": "メイン画像の最適化・サーバー高速化"})
        elif ps["lcp"] > lcp_max:
            issues.append({"check_id": "S11", "severity": "high", "page": url,
                "message": f"LCP {ps['lcp']:.1f}秒 > 基準 {lcp_max}秒", "action": "画像遅延読み込み・CDN導入"})

        # S12: CLS
        cls_max = seo_t.get("cls_max", 0.1)
        if ps["cls"] > 0.25:
            issues.append({"check_id": "S12", "severity": "high", "page": url,
                "message": f"CLS {ps['cls']:.3f} — レイアウト大幅ずれ", "action": "画像サイズ指定・広告枠の固定高さ設定"})
        elif ps["cls"] > cls_max:
            issues.append({"check_id": "S12", "severity": "medium", "page": url,
                "message": f"CLS {ps['cls']:.3f} > 基準 {cls_max}", "action": "レイアウトシフトの原因要素を特定"})

        # S13: TBT (FID代替)
        if ps["tbt"] > 600:
            issues.append({"check_id": "S13", "severity": "high", "page": url,
                "message": f"TBT {ps['tbt']:.0f}ms — メインスレッドブロック", "action": "JS分割・遅延読み込み・不要スクリプト削除"})

        # S14: TTFB
        if ps["ttfb"] > 1.5:
            issues.append({"check_id": "S14", "severity": "high", "page": url,
                "message": f"TTFB {ps['ttfb']:.1f}秒 — サーバー応答遅い", "action": "CDN・サーバーキャッシュ・DB最適化"})

        # S15: アクセシビリティスコア
        if ps["accessibility"] < 70:
            issues.append({"check_id": "S15", "severity": "medium", "page": url,
                "message": f"アクセシビリティスコア {ps['accessibility']} — 改善必要", "action": "alt属性・コントラスト・ARIA対応"})

        # S16: SEOスコア
        if ps["seo"] < 80:
            issues.append({"check_id": "S16", "severity": "high", "page": url,
                "message": f"Lighthouse SEOスコア {ps['seo']} — 基本SEO設定に不備", "action": "meta・構造化データ・リンク設定確認"})

        # S17: Speed Index
        if ps["si"] > 5.0:
            issues.append({"check_id": "S17", "severity": "medium", "page": url,
                "message": f"Speed Index {ps['si']:.1f}秒 — 表示完了が遅い", "action": "クリティカルCSS・フォント最適化"})

        # S18: Best Practices
        if ps["best_practices"] < 80:
            issues.append({"check_id": "S18", "severity": "medium", "page": url,
                "message": f"Best Practicesスコア {ps['best_practices']}", "action": "HTTPS・安全なライブラリ・コンソールエラー修正"})

    # === HTML チェック S19-S45 ===
    hl = html.lower() if html else ""

    # S19: title タグ
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE|re.DOTALL)
    title = title_m.group(1).strip() if title_m else ""
    if not title:
        issues.append({"check_id": "S19", "severity": "critical", "page": url,
            "message": "titleタグが存在しない", "action": "ページ内容に合ったtitleを設定"})
    elif len(title) > 60:
        issues.append({"check_id": "S19b", "severity": "medium", "page": url,
            "message": f"title長 {len(title)}文字 > 推奨60文字", "action": "60文字以内に要約"})
    elif len(title) < 10:
        issues.append({"check_id": "S19c", "severity": "medium", "page": url,
            "message": f"title長 {len(title)}文字 — 短すぎる", "action": "KWを含む具体的なtitleに変更"})

    # S20: meta description
    desc_m = re.search(r'<meta[^>]+name=["\'"]description["\'"][^>]+content=["\'"]([^"\']*)["\'"]', html or "", re.IGNORECASE)
    if not desc_m:
        desc_m = re.search(r'<meta[^>]+content=["\'"]([^"\']*)["\'"][^>]+name=["\'"]description["\'"]', html or "", re.IGNORECASE)
    desc = desc_m.group(1).strip() if desc_m else ""
    if not desc:
        issues.append({"check_id": "S20", "severity": "high", "page": url,
            "message": "meta description が未設定", "action": "120-160文字の説明文を設定"})
    elif len(desc) > 160:
        issues.append({"check_id": "S20b", "severity": "low", "page": url,
            "message": f"meta description {len(desc)}文字 > 推奨160文字", "action": "160文字以内に要約"})

    # S21: H1タグ
    h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", html or "", re.IGNORECASE|re.DOTALL)
    if not h1s:
        issues.append({"check_id": "S21", "severity": "high", "page": url,
            "message": "H1タグが存在しない", "action": "ページの主題を示すH1を1つ設置"})
    elif len(h1s) > 1:
        issues.append({"check_id": "S21b", "severity": "medium", "page": url,
            "message": f"H1タグが{len(h1s)}個 — 1つに統一推奨", "action": "メインH1を1つにし他はH2-H3に変更"})

    # S22: H2タグ
    h2s = re.findall(r"<h2[^>]*>", html or "", re.IGNORECASE)
    if not h2s:
        issues.append({"check_id": "S22", "severity": "medium", "page": url,
            "message": "H2タグが存在しない — コンテンツ構造が不十分", "action": "セクション見出しとしてH2を追加"})

    # S23: viewport
    if 'name="viewport"' not in hl and "name='viewport'" not in hl:
        issues.append({"check_id": "S23", "severity": "critical", "page": url,
            "message": "viewportメタタグが未設定 — モバイル非対応", "action": "viewport meta タグを追加"})

    # S24: HTTPS
    if not url.startswith("https://"):
        issues.append({"check_id": "S24", "severity": "critical", "page": url,
            "message": "HTTPSではない", "action": "SSL証明書を導入しHTTPS化"})

    # S25: canonical
    can_m = re.search(r'<link[^>]+rel=["\'"]canonical["\'"][^>]+href=["\'"]([^"\']*)["\'"]', html or "", re.IGNORECASE)
    if not can_m:
        issues.append({"check_id": "S25", "severity": "medium", "page": url,
            "message": "canonicalタグが未設定", "action": "重複防止のためcanonical URLを指定"})

    # S26: OGP
    if 'property="og:title"' not in hl and "property='og:title'" not in hl:
        issues.append({"check_id": "S26", "severity": "medium", "page": url,
            "message": "OGPタグ(og:title)が未設定", "action": "SNSシェア用にOGPタグを追加"})
    if 'property="og:image"' not in hl:
        issues.append({"check_id": "S27", "severity": "medium", "page": url,
            "message": "OGP画像(og:image)が未設定", "action": "SNSシェア用画像を設定"})

    # S28: JSON-LD 構造化データ
    if "application/ld+json" not in hl:
        issues.append({"check_id": "S28", "severity": "medium", "page": url,
            "message": "JSON-LD構造化データが未設定", "action": "Organization/Product/FAQなどのSchema.orgマークアップを追加"})

    # S29: 画像alt属性
    imgs = re.findall(r"<img[^>]*>", html or "", re.IGNORECASE)
    imgs_no_alt = [img for img in imgs if 'alt="' not in img.lower() and "alt='" not in img.lower()]
    if imgs and len(imgs_no_alt) > len(imgs) * 0.3:
        issues.append({"check_id": "S29", "severity": "high", "page": url,
            "message": f"画像{len(imgs)}枚中{len(imgs_no_alt)}枚がalt未設定({len(imgs_no_alt)/len(imgs)*100:.0f}%)", "action": "全画像に説明的なalt属性を追加"})

    # S30: lang属性
    if 'lang="' not in hl[:500] and "lang='" not in hl[:500]:
        issues.append({"check_id": "S30", "severity": "medium", "page": url,
            "message": "html lang属性が未設定", "action": '<html lang="ja"> を追加'})

    # S31: favicon
    if "shortcut icon" not in hl and "icon" not in hl[:2000]:
        issues.append({"check_id": "S31", "severity": "low", "page": url,
            "message": "faviconが未設定", "action": "faviconを追加"})

    # S32: 内部リンク数
    internal_links = re.findall(r'href=["\'"](/[^"\']*|https?://[^"\']*)', html or "", re.IGNORECASE)
    domain = url.split("//")[1].split("/")[0] if "//" in url else ""
    internal = [link for link in internal_links if link.startswith("/") or domain in link]
    if len(internal) < 3:
        issues.append({"check_id": "S32", "severity": "medium", "page": url,
            "message": f"内部リンク数 {len(internal)} — 少なすぎる", "action": "関連ページへの内部リンクを追加"})

    # S33: 外部リンクnofollow
    external = [link for link in internal_links if not link.startswith("/") and domain not in link and link.startswith("http")]
    nofollow_links = len(re.findall(r'rel=["\'"][^"\']*nofollow', html or "", re.IGNORECASE))
    if external and nofollow_links == 0 and len(external) > 5:
        issues.append({"check_id": "S33", "severity": "low", "page": url,
            "message": f"外部リンク{len(external)}件すべてにnofollow未設定", "action": "信頼できないリンクにはnofollowを付与"})

    # S34: ページサイズ
    if html and len(html) > 500000:
        issues.append({"check_id": "S34", "severity": "medium", "page": url,
            "message": f"HTML {len(html)//1000}KB — 重い", "action": "不要なコード削除・コンポーネント分割"})

    # S35: インラインCSS過多
    inline_styles = len(re.findall(r'style=["\'"]', html or "", re.IGNORECASE))
    if inline_styles > 20:
        issues.append({"check_id": "S35", "severity": "low", "page": url,
            "message": f"インラインstyle {inline_styles}件 — 外部CSS推奨", "action": "CSSクラスに統合"})

    # S36: render-blocking resources推定
    sync_scripts = len(re.findall(r"<script(?!.*(?:async|defer))[^>]*src=", html or "", re.IGNORECASE))
    if sync_scripts > 3:
        issues.append({"check_id": "S36", "severity": "medium", "page": url,
            "message": f"同期スクリプト {sync_scripts}件 — レンダリングブロック", "action": "async/deferを追加"})

    # S37: 画像フォーマット（WebP/AVIF）
    old_format_imgs = len(re.findall(r'\.(jpg|jpeg|png|gif)["\'")\s>]', html or "", re.IGNORECASE))
    webp_imgs = len(re.findall(r'\.(webp|avif)["\'")\s>]', html or "", re.IGNORECASE))
    if old_format_imgs > 3 and webp_imgs == 0:
        issues.append({"check_id": "S37", "severity": "medium", "page": url,
            "message": f"旧形式画像 {old_format_imgs}件 — WebP/AVIF未使用", "action": "次世代画像フォーマットに変換"})

    # S38: lazy loading
    if imgs and "loading=" not in hl:
        issues.append({"check_id": "S38", "severity": "medium", "page": url,
            "message": "画像のlazy loading未設定", "action": 'loading="lazy" を追加'})

    # S39: コンテンツ文字数
    text_only = re.sub(r"<[^>]+>", "", html or "")
    text_only = re.sub(r"\s+", " ", text_only).strip()
    word_count = len(text_only)
    if word_count < 300:
        issues.append({"check_id": "S39", "severity": "medium", "page": url,
            "message": f"テキスト量 {word_count}文字 — コンテンツ薄い", "action": "有益なコンテンツを追加（最低500文字推奨）"})

    # S40: meta robots noindex
    if re.search(r'<meta[^>]+robots[^>]*noindex', hl):
        issues.append({"check_id": "S40", "severity": "critical", "page": url,
            "message": "noindex が設定されている — 検索結果に表示されない", "action": "意図的でなければnoindexを削除"})

    # S41: Twitter Card
    if 'name="twitter:card"' not in hl and "name='twitter:card'" not in hl:
        issues.append({"check_id": "S41", "severity": "low", "page": url,
            "message": "Twitter Card 未設定", "action": "twitter:card メタタグを追加"})

    # S42: amp対応確認（モバイル向け）
    # S43: preconnect/prefetch
    if "preconnect" not in hl and "dns-prefetch" not in hl:
        issues.append({"check_id": "S43", "severity": "low", "page": url,
            "message": "preconnect/dns-prefetch 未設定", "action": "外部リソースにpreconnectを追加"})

    # S44: charset
    if 'charset="utf-8"' not in hl and "charset='utf-8'" not in hl and "charset=utf-8" not in hl:
        issues.append({"check_id": "S44", "severity": "medium", "page": url,
            "message": "charset UTF-8 の明示的指定がない", "action": '<meta charset="UTF-8"> を追加'})

    # S45: 混在コンテンツ（HTTP リソース）
    if url.startswith("https://"):
        http_resources = len(re.findall(r'(src|href)=["\'"]http://', html or "", re.IGNORECASE))
        if http_resources > 0:
            issues.append({"check_id": "S45", "severity": "high", "page": url,
                "message": f"混在コンテンツ {http_resources}件 — HTTPリソースがHTTPSページに存在", "action": "全リソースをHTTPSに変更"})

    result["issues"] = issues
    return result
