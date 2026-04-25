"""Google Ads チェック — 構造/KW/QS/アセット/コンバージョン/入札/PMax (G01-G84)"""
import logging
import re

log = logging.getLogger("bpo")


def run_google_checks(campaigns, thresholds):
    """Google Ads 固有チェック実行"""
    google_camps = [c for c in campaigns if c.get("platform", "").lower() == "google"]
    if not google_camps:
        return []

    g_t = thresholds.get("google", {})
    results = []

    results.extend(_check_structure(google_camps, g_t))
    results.extend(_check_keywords(google_camps, g_t))
    results.extend(_check_ads_assets(google_camps, g_t))
    results.extend(_check_bidding(google_camps, g_t))
    results.extend(_check_conversion_tracking(google_camps, g_t))
    results.extend(_check_pmax(google_camps, g_t))

    return results


def _check_structure(camps, g_t):
    """構造チェック G01-G12"""
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]
    pmax_camps = [c for c in camps if c.get("campaign_type") == "pmax"]

    # G01: 命名規則 — キャンペーン名にプラットフォーム/タイプ/ターゲットが含まれるか
    for camp in camps:
        name = camp.get("campaign", "")
        has_structure = bool(re.search(r"[_\-|]", name))
        results.append({
            "id": "G01", "passed": has_structure, "campaign": name, "platform": "google",
            "message": "" if has_structure else f"命名規則不適合: '{name}' に区切り文字なし",
        })

    # G05: ブランド/非ブランド分離
    brand_kws = ["brand", "ブランド", "指名"]
    has_brand = any(any(bk in c.get("campaign", "").lower() for bk in brand_kws) for c in search_camps)
    has_nonbrand = any(not any(bk in c.get("campaign", "").lower() for bk in brand_kws) for c in search_camps)
    if search_camps:
        results.append({
            "id": "G05", "passed": has_brand and has_nonbrand, "campaign": "全Search",
            "platform": "google",
            "message": "" if (has_brand and has_nonbrand) else "ブランド/非ブランド キャンペーン分離なし",
        })

    # G07: PMax + Search 重複
    if pmax_camps and search_camps:
        results.append({
            "id": "G07", "passed": True, "campaign": "アカウント全体", "platform": "google",
            "message": "PMax + Search 併用中（ブランド除外要確認）",
        })

    # G08-G09: 予算制限
    for camp in camps:
        budget = camp.get("daily_budget", 0)
        cost = camp.get("cost", 0)
        if budget > 0 and cost > 0:
            utilization = cost / budget
            results.append({
                "id": "G08", "passed": utilization < 0.95, "campaign": camp["campaign"],
                "platform": "google",
                "message": "" if utilization < 0.95 else f"予算制約あり: 消化率 {utilization * 100:.0f}%",
            })

    return results


def _check_keywords(camps, g_t):
    """キーワード/QS チェック G13-G25"""
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]

    for camp in search_camps:
        name = camp.get("campaign", "")

        # G14: ネガティブKWリスト
        neg_lists = camp.get("negative_keyword_lists", [])
        results.append({
            "id": "G14", "passed": len(neg_lists) > 0, "campaign": name, "platform": "google",
            "message": "" if neg_lists else "ネガティブKWリスト未設定",
        })

        # G17: Broad Match + Manual CPC
        match_types = camp.get("match_types", [])
        bidding = camp.get("bidding_strategy", "")
        if "broad" in [m.lower() for m in match_types] and "manual" in bidding.lower():
            results.append({
                "id": "G17", "passed": False, "campaign": name, "platform": "google",
                "message": "Broad Match + Manual CPC: Smart Bidding推奨",
            })

        # G20: Average QS
        avg_qs = camp.get("quality_score_avg")
        if avg_qs is not None:
            results.append({
                "id": "G20", "passed": avg_qs >= 5, "campaign": name, "platform": "google",
                "message": "" if avg_qs >= 5 else f"平均QS {avg_qs:.1f} (推奨: ≥5)",
            })

        # G-WS1: ゼロCV KW with cost
        if camp.get("conversions", 0) == 0 and camp.get("cost", 0) > 0 and camp.get("keyword_count", 0) > 0:
            results.append({
                "id": "G-WS1", "passed": False, "campaign": name, "platform": "google",
                "message": f"ゼロCVキーワード群: ¥{camp['cost']:,.0f} 消化",
            })

    return results


def _check_ads_assets(camps, g_t):
    """広告・アセットチェック G26-G35"""
    results = []

    for camp in camps:
        name = camp.get("campaign", "")
        ad_count = camp.get("ad_count", 0)

        # G26: RSA数（search キャンペーン）
        if camp.get("campaign_type") == "search" and ad_count > 0:
            results.append({
                "id": "G26", "passed": ad_count >= 1, "campaign": name, "platform": "google",
                "message": "" if ad_count >= 1 else "RSAなし",
            })

        # G29: Ad Strength
        ad_strengths = camp.get("ad_strengths", [])
        if ad_strengths:
            poor = [s for s in ad_strengths if "poor" in s.lower() or "average" in s.lower()]
            results.append({
                "id": "G29", "passed": len(poor) == 0, "campaign": name, "platform": "google",
                "message": "" if len(poor) == 0 else f"Ad Strength低: {len(poor)}/{len(ad_strengths)}件",
            })

    return results


def _check_bidding(camps, g_t):
    """入札・予算チェック G36-G41"""
    results = []

    for camp in camps:
        name = camp.get("campaign", "")
        bidding = camp.get("bidding_strategy", "unknown")

        # G36: Smart Bidding 推奨
        smart_bidding = ["target_cpa", "target_roas", "max_conversions", "max_conv_value"]
        if camp.get("campaign_type") in ["search", "shopping"]:
            is_smart = bidding in smart_bidding
            results.append({
                "id": "G36", "passed": is_smart, "campaign": name, "platform": "google",
                "message": "" if is_smart else f"Manual Bidding使用中: {bidding}",
            })

        # G38: 学習フェーズ率
        if camp.get("learning_phase"):
            results.append({
                "id": "G38", "passed": False, "campaign": name, "platform": "google",
                "message": "学習フェーズ中: CV不足",
            })

    return results


def _check_conversion_tracking(camps, g_t):
    """コンバージョントラッキングチェック G42-G49"""
    results = []

    # アカウント全体のチェック（1回だけ）
    if camps:
        first = camps[0]
        # G43: Enhanced Conversions
        results.append({
            "id": "G43", "passed": first.get("enhanced_conversions", False),
            "campaign": "アカウント全体", "platform": "google",
            "message": "" if first.get("enhanced_conversions") else "Enhanced Conversions 未有効",
        })

        # G48: DDA
        attr = first.get("attribution_model", "")
        results.append({
            "id": "G48", "passed": attr == "dda",
            "campaign": "アカウント全体", "platform": "google",
            "message": "" if attr == "dda" else f"DDA未設定 (現在: {attr})",
        })

    return results


def _check_pmax(camps, g_t):
    """PMax チェック G-PM1-PM6"""
    results = []
    pmax_camps = [c for c in camps if c.get("campaign_type") == "pmax"]

    for camp in pmax_camps:
        name = camp.get("campaign", "")
        asset_count = camp.get("asset_count", 0)

        # G-PM2: PMax Ad Strength
        ad_strengths = camp.get("ad_strengths", [])
        if ad_strengths:
            good = [s for s in ad_strengths if "good" in s.lower() or "excellent" in s.lower()]
            results.append({
                "id": "G-PM2", "passed": len(good) > 0, "campaign": name, "platform": "google",
                "message": "" if good else "PMax Ad Strength: Good未達",
            })

        # G31: アセット密度
        results.append({
            "id": "G31", "passed": asset_count >= 20, "campaign": name, "platform": "google",
            "message": "" if asset_count >= 20 else f"PMaxアセット不足: {asset_count}個 (推奨≥20)",
        })

    return results
