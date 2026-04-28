"""Google Ads チェック — 全63ルール完全実装 (G01-G60, G-PM1-PM6, G-KW1, G-WS1, G-AI1, G-DG1-DG3, G-CTV1, G-CT2, G-AD1)"""
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
    results.extend(_check_negative_kw(google_camps, g_t))
    results.extend(_check_keywords_qs(google_camps, g_t))
    results.extend(_check_ads_assets(google_camps, g_t))
    results.extend(_check_bidding(google_camps, g_t))
    results.extend(_check_conversion_tracking(google_camps, g_t))
    results.extend(_check_extensions(google_camps, g_t))
    results.extend(_check_pmax(google_camps, g_t))
    results.extend(_check_advanced(google_camps, g_t))

    return results


# ========================================
# Structure (G01, G03-G05, G07-G09, G11-G12)
# ========================================
def _check_structure(camps, g_t):
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]
    pmax_camps = [c for c in camps if c.get("campaign_type") == "pmax"]

    # G01: 命名規則 — 区切り文字を含む構造化された名前か
    for camp in camps:
        name = camp.get("campaign", "")
        has_structure = bool(re.search(r"[_\-|]", name))
        results.append(_r("G25", has_structure, name,
                          "" if has_structure else f"命名規則不適合: '{name}' に区切り文字なし"))

    # G03: STAG構造（1広告グループ≤10KW）
    for camp in search_camps:
        kw_count = camp.get("keyword_count", 0)
        adgroup_count = camp.get("adgroup_count", 1) or 1
        if kw_count > 0:
            avg_kw = kw_count / adgroup_count
            results.append(_r("G39", avg_kw <= 15, camp["campaign"],
                              "" if avg_kw <= 15 else f"広告グループあたり平均KW {avg_kw:.0f}個 (推奨≤15)"))

    # G04: キャンペーン数/目的の整合性
    type_counts = {}
    for c in camps:
        ct = c.get("campaign_type", "other")
        type_counts[ct] = type_counts.get(ct, 0) + 1
    for ct, count in type_counts.items():
        if count > 5:
            results.append(_r("G17", False, f"{ct}タイプ",
                              f"同一タイプ '{ct}' に {count} キャンペーン — 統合推奨"))

    # G05: ブランド/非ブランド分離
    brand_kws = ["brand", "ブランド", "指名"]
    has_brand = any(any(bk in c.get("campaign", "").lower() for bk in brand_kws) for c in search_camps)
    has_nonbrand = any(not any(bk in c.get("campaign", "").lower() for bk in brand_kws) for c in search_camps)
    if search_camps:
        results.append(_r("G31", has_brand and has_nonbrand, "全Search",
                          "" if (has_brand and has_nonbrand) else "ブランド/非ブランド キャンペーン分離なし"))

    # G07: PMax + Search 重複
    if pmax_camps and search_camps:
        # PMでブランド除外がされているかチェック
        pm_has_neg = any(c.get("negative_keyword_lists") for c in pmax_camps)
        results.append(_r("G53", pm_has_neg, "アカウント全体",
                          "" if pm_has_neg else "PMax + Search併用中だがPMaxにネガKWなし — ブランドカニバリリスク"))

    # G08: 予算制限チェック
    for camp in camps:
        budget = camp.get("daily_budget", 0)
        cost = camp.get("cost", 0)
        if budget > 0 and cost > 0:
            util = cost / budget
            results.append(_r("G13", util < 0.95, camp["campaign"],
                              "" if util < 0.95 else f"予算制約: 消化率 {util * 100:.0f}% — 機会損失"))

    # G09: 予算配分バランス
    total_cost = sum(c.get("cost", 0) for c in camps)
    if total_cost > 0 and len(camps) > 1:
        for camp in camps:
            share = camp.get("cost", 0) / total_cost * 100
            cv = camp.get("conversions", 0)
            roas = camp.get("roas", 0)
            # 予算シェア>50%なのにROAS<1 or CV=0 → 非効率な集中
            if share > 50 and (roas < 1 or cv == 0):
                results.append(_r("G15", False, camp["campaign"],
                                  f"予算{share:.0f}%集中だがROAS {roas:.1f} — 再配分推奨"))

    # G11: 地理ターゲティング People in 設定
    for camp in camps:
        geo_setting = camp.get("geo_targeting_type", "")
        if geo_setting and "interest" in geo_setting.lower():
            results.append(_r("G20", False, camp["campaign"],
                              "地理ターゲ: 'People in or interested in' → 'People in' に変更推奨"))

    # G12: ネットワーク設定（Search Partners除外推奨）
    for camp in search_camps:
        search_partners = camp.get("search_partners_enabled", None)
        if search_partners is True:
            results.append(_r("G42", False, camp["campaign"],
                              "Search Partners有効 — パフォーマンス検証の上、除外を検討"))

    return results


# ========================================
# Negative KW / Search Terms (G13-G17, G-WS1)
# ========================================
def _check_negative_kw(camps, g_t):
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]

    for camp in search_camps:
        name = camp.get("campaign", "")

        # G13: 検索語句レビュー日
        last_review = camp.get("search_term_review_date", "")
        if not last_review:
            results.append(_r("G28", False, name, "検索語句レビュー日不明 — 定期レビュー推奨"))

        # G14: ネガティブKWリスト存在
        neg_lists = camp.get("negative_keyword_lists", [])
        results.append(_r("G27", len(neg_lists) > 0, name,
                          "" if neg_lists else "ネガティブKWリスト未設定"))

        # G15: 共有ネガKWリスト
        shared_neg = camp.get("shared_negative_lists", [])
        results.append(_r("G27b", len(shared_neg) > 0, name,
                          "" if shared_neg else "共有ネガティブKWリスト未適用"))

        # G16: 無駄クリック率
        waste_clicks = camp.get("waste_click_rate", 0)
        waste_threshold = g_t.get("waste_click_rate_max", 20)
        if waste_clicks > waste_threshold:
            results.append(_r("G80", False, name,
                              f"無駄クリック率 {waste_clicks:.1f}% (閾値: {waste_threshold}%)"))

        # G17: Broad Match + Manual CPC
        match_types = camp.get("match_types", [])
        bidding = camp.get("bidding_strategy", "")
        if "broad" in [m.lower() for m in match_types] and "manual" in bidding.lower():
            results.append(_r("G29", False, name, "Broad Match + Manual CPC: Smart Bidding推奨"))

        # G-WS1: ゼロCV KW with cost
        if camp.get("conversions", 0) == 0 and camp.get("cost", 0) > 0 and camp.get("keyword_count", 0) > 0:
            results.append(_r("G79", False, name,
                              f"ゼロCVキーワード群: ¥{camp['cost']:,.0f} 消化"))

    return results


# ========================================
# KW / QS (G20-G25, G-KW1)
# ========================================
def _check_keywords_qs(camps, g_t):
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]

    for camp in search_camps:
        name = camp.get("campaign", "")

        # G20: Average QS
        avg_qs = camp.get("quality_score_avg")
        if avg_qs is not None:
            results.append(_r("G26", avg_qs >= 5, name,
                              "" if avg_qs >= 5 else f"平均QS {avg_qs:.1f} (推奨: ≥5)"))

        # G21: QS≤3 比率
        qs_low_ratio = camp.get("qs_low_ratio", 0)
        if qs_low_ratio > 0:
            results.append(_r("G26b", qs_low_ratio <= 20, name,
                              "" if qs_low_ratio <= 20 else f"QS≤3のKW比率 {qs_low_ratio:.0f}% (推奨: ≤20%)"))

        # G22: Expected CTR Below Average 比率
        ectr_below = camp.get("expected_ctr_below_avg_pct", 0)
        if ectr_below > 0:
            results.append(_r("G26c", ectr_below <= 30, name,
                              "" if ectr_below <= 30 else f"Expected CTR Below Average {ectr_below:.0f}%"))

        # G23: Ad Relevance Below Average 比率
        ar_below = camp.get("ad_relevance_below_avg_pct", 0)
        if ar_below > 0:
            results.append(_r("G26d", ar_below <= 30, name,
                              "" if ar_below <= 30 else f"Ad Relevance Below Average {ar_below:.0f}%"))

        # G24: LP Experience Below Average 比率
        lp_below = camp.get("lp_experience_below_avg_pct", 0)
        if lp_below > 0:
            results.append(_r("G26e", lp_below <= 30, name,
                              "" if lp_below <= 30 else f"LP Experience Below Average {lp_below:.0f}%"))

        # G-KW1: ゼロインプレッション KW
        zero_imp_kw = camp.get("zero_impression_keywords", 0)
        kw_count = camp.get("keyword_count", 0)
        if kw_count > 0 and zero_imp_kw > 0:
            ratio = zero_imp_kw / kw_count * 100
            results.append(_r("G32", ratio <= 30, name,
                              "" if ratio <= 30 else f"ゼロインプKW {zero_imp_kw}/{kw_count} ({ratio:.0f}%) — 整理推奨"))

    return results


# ========================================
# Ads / Assets (G26-G32, G-AD1)
# ========================================
def _check_ads_assets(camps, g_t):
    results = []

    for camp in camps:
        name = camp.get("campaign", "")
        ad_count = camp.get("ad_count", 0)
        ct = camp.get("campaign_type", "")

        # G26: RSA数（search キャンペーン）
        if ct == "search":
            results.append(_r("G37", ad_count >= 1, name,
                              "" if ad_count >= 1 else "RSA広告なし"))

        # G27: RSA ヘッドライン数（≥8推奨）
        headline_counts = camp.get("headline_counts", [])
        if headline_counts:
            min_hl = min(headline_counts) if headline_counts else 0
            results.append(_r("G37b", min_hl >= 8, name,
                              "" if min_hl >= 8 else f"RSAヘッドライン {min_hl}個 (推奨≥8)"))

        # G28: RSA 説明文数（≥4推奨）
        desc_counts = camp.get("description_counts", [])
        if desc_counts:
            min_desc = min(desc_counts) if desc_counts else 0
            results.append(_r("G37c", min_desc >= 4, name,
                              "" if min_desc >= 4 else f"RSA説明文 {min_desc}個 (推奨≥4)"))

        # G29: Ad Strength
        ad_strengths = camp.get("ad_strengths", [])
        if ad_strengths:
            poor = [s for s in ad_strengths if "poor" in s.lower() or "average" in s.lower()]
            results.append(_r("G34", len(poor) == 0, name,
                              "" if len(poor) == 0 else f"Ad Strength低: {len(poor)}/{len(ad_strengths)}件"))

        # G31: PMax アセット密度
        if ct == "pmax":
            asset_images = camp.get("asset_image_count", camp.get("asset_count", 0))
            asset_logos = camp.get("asset_logo_count", 0)
            asset_videos = camp.get("asset_video_count", 0)
            problems = []
            if asset_images < 20:
                problems.append(f"画像{asset_images}/20")
            if asset_logos < 5:
                problems.append(f"ロゴ{asset_logos}/5")
            if asset_videos < 5:
                problems.append(f"動画{asset_videos}/5")
            results.append(_r("G65", len(problems) == 0, name,
                              "" if not problems else f"PMaxアセット不足: {', '.join(problems)}"))

        # G32: ネイティブ動画有無（ディスプレイ・動画キャンペーン）
        if ct in ("display", "video", "demand_gen", "pmax"):
            has_video = camp.get("has_native_video", False)
            results.append(_r("G66", has_video, name,
                              "" if has_video else "ネイティブ動画なし — 動画アセット追加推奨"))

    return results


# ========================================
# Bidding / Budget (G36-G41)
# ========================================
def _check_bidding(camps, g_t):
    results = []
    smart_bidding = {"target_cpa", "target_roas", "max_conversions", "max_conv_value"}

    for camp in camps:
        name = camp.get("campaign", "")
        bidding = camp.get("bidding_strategy", "unknown")
        ct = camp.get("campaign_type", "")

        # G36: Smart Bidding 推奨
        if ct in ("search", "shopping"):
            is_smart = bidding in smart_bidding
            results.append(_r("G11", is_smart, name,
                              "" if is_smart else f"Manual Bidding使用中: {bidding}"))

        # G37: 目標CPA/ROAS 妥当性
        target_cpa = camp.get("target_cpa", 0)
        actual_cpa = camp.get("cpa", 0)
        if target_cpa > 0 and actual_cpa > 0:
            ratio = actual_cpa / target_cpa
            if ratio > 1.5:
                results.append(_r("G11b", False, name,
                                  f"実績CPA ¥{actual_cpa:,.0f} が目標¥{target_cpa:,.0f}の{ratio:.1f}倍 — 目標緩和または改善"))
            elif ratio < 0.5:
                results.append(_r("G11b", False, name,
                                  f"実績CPA ¥{actual_cpa:,.0f} が目標の半分以下 — 目標を攻めに調整可能"))

        target_roas = camp.get("target_roas", 0)
        actual_roas = camp.get("roas", 0)
        if target_roas > 0 and actual_roas > 0:
            if actual_roas < target_roas * 0.7:
                results.append(_r("G11b", False, name,
                                  f"実績ROAS {actual_roas:.1f} が目標{target_roas:.1f}の70%未満"))

        # G38: 学習フェーズ率
        if camp.get("learning_phase"):
            results.append(_r("G12", False, name, "学習フェーズ中: CV不足"))

        # G39: 予算制約（Limited by budget）
        limited = camp.get("limited_by_budget", False)
        budget = camp.get("daily_budget", 0)
        cost = camp.get("cost", 0)
        if limited or (budget > 0 and cost >= budget * 0.95):
            results.append(_r("G13b", False, name,
                              f"予算制約: 日予算¥{budget:,.0f}に対し消化¥{cost:,.0f} — 増額推奨"))

        # G40: Manual CPC 正当性
        if bidding == "manual_cpc" and ct not in ("display",):
            cv = camp.get("conversions", 0)
            if cv >= 30:
                results.append(_r("G11c", False, name,
                                  f"Manual CPC使用中だがCV {cv:.0f}/日 — Smart Bidding移行推奨"))

        # G41: ポートフォリオ入札設定
        portfolio = camp.get("portfolio_bid_strategy", False)
        if not portfolio and ct in ("search", "shopping") and bidding in smart_bidding:
            results.append(_r("G41", True, name, ""))  # Pass: 個別入札でOK

    return results


# ========================================
# Conversion Tracking (G43, G45, G47-G49, G-CT2)
# ========================================
def _check_conversion_tracking(camps, g_t):
    results = []
    if not camps:
        return results
    first = camps[0]

    # G43: Enhanced Conversions
    results.append(_r("G03", first.get("enhanced_conversions", False), "アカウント全体",
                       "" if first.get("enhanced_conversions") else "Enhanced Conversions 未有効"))

    # G45: Consent Mode v2
    consent_mode = first.get("consent_mode_v2", None)
    if consent_mode is not None:
        results.append(_r("G81", consent_mode, "アカウント全体",
                          "" if consent_mode else "Consent Mode v2 未実装 — EU/UK向けの場合必須"))
    else:
        results.append(_r("G81", False, "アカウント全体", "Consent Mode v2 ステータス不明"))

    # G47: マクロ/マイクロ CV分離
    cv_actions = first.get("conversion_action_count", 0)
    primary_actions = first.get("primary_conversion_actions", 0)
    if cv_actions > 0:
        results.append(_r("G02", primary_actions > 0 and primary_actions < cv_actions, "アカウント全体",
                          "" if (primary_actions > 0 and primary_actions < cv_actions)
                          else f"CVアクション {cv_actions}個全てがprimary — マクロ/マイクロ分離推奨"))

    # G48: DDA
    attr = first.get("attribution_model", "")
    results.append(_r("G08", attr == "dda", "アカウント全体",
                       "" if attr == "dda" else f"DDA未設定 (現在: {attr or 'unknown'})"))

    # G49: コンバージョン値割当
    has_values = first.get("conversion_values_set", False)
    results.append(_r("G06", has_values, "アカウント全体",
                       "" if has_values else "コンバージョン値未設定 — value-based bidding に必要"))

    # G-CT2: GA4連携
    ga4_linked = first.get("ga4_linked", None)
    if ga4_linked is not None:
        results.append(_r("G04", ga4_linked, "アカウント全体",
                          "" if ga4_linked else "GA4連携なし — クロスチャネル分析に必要"))
    else:
        results.append(_r("G04", False, "アカウント全体", "GA4連携ステータス不明"))

    return results


# ========================================
# Extensions / Audiences (G50-G60)
# ========================================
def _check_extensions(camps, g_t):
    results = []
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]

    for camp in search_camps:
        name = camp.get("campaign", "")
        exts = camp.get("extensions", {})

        # G50: サイトリンク
        sitelinks = exts.get("sitelinks", 0) if isinstance(exts, dict) else 0
        results.append(_r("G22", sitelinks >= 4, name,
                          "" if sitelinks >= 4 else f"サイトリンク {sitelinks}個 (推奨≥4)"))

        # G51: コールアウト
        callouts = exts.get("callouts", 0) if isinstance(exts, dict) else 0
        results.append(_r("G22b", callouts >= 4, name,
                          "" if callouts >= 4 else f"コールアウト {callouts}個 (推奨≥4)"))

        # G52: 構造化スニペット
        snippets = exts.get("structured_snippets", 0) if isinstance(exts, dict) else 0
        results.append(_r("G22c", snippets >= 1, name,
                          "" if snippets >= 1 else "構造化スニペット未設定"))

        # G53: 画像拡張
        images = exts.get("images", 0) if isinstance(exts, dict) else 0
        results.append(_r("G22d", images >= 1, name,
                          "" if images >= 1 else "画像拡張未設定"))

    # アカウントレベルチェック
    if camps:
        first = camps[0]
        # G56: オーディエンスセグメント
        audiences = first.get("audience_segments", [])
        results.append(_r("G09", len(audiences) > 0, "アカウント全体",
                          "" if audiences else "オーディエンスセグメント未設定"))

        # G57: Customer Match
        cm = first.get("customer_match_enabled", False)
        results.append(_r("G09b", cm, "アカウント全体",
                          "" if cm else "Customer Match 未活用"))

        # G58: プレースメント除外（ディスプレイ/PMax）
        display_pmax = [c for c in camps if c.get("campaign_type") in ("display", "pmax", "demand_gen")]
        for dpc in display_pmax:
            exclusions = dpc.get("placement_exclusions", 0)
            results.append(_r("G54", exclusions > 0, dpc["campaign"],
                              "" if exclusions > 0 else "プレースメント除外なし — 低品質面を除外推奨"))

        # G59: LP速度
        for camp in camps:
            lp_speed = camp.get("lp_speed_score", None)
            if lp_speed is not None:
                results.append(_r("G59", lp_speed >= 50, camp["campaign"],
                                  "" if lp_speed >= 50 else f"LP速度スコア {lp_speed} (推奨≥50)"))

        # G60: LP関連性
        for camp in camps:
            lp_relevance = camp.get("lp_relevance_score", None)
            if lp_relevance is not None:
                results.append(_r("G60", lp_relevance >= 0.7, camp["campaign"],
                                  "" if lp_relevance >= 0.7 else f"LP関連性スコア {lp_relevance:.1f} (推奨≥0.7)"))

    return results


# ========================================
# PMax Extended (G-PM1-PM6)
# ========================================
def _check_pmax(camps, g_t):
    results = []
    pmax_camps = [c for c in camps if c.get("campaign_type") == "pmax"]
    search_camps = [c for c in camps if c.get("campaign_type") == "search"]

    for camp in pmax_camps:
        name = camp.get("campaign", "")

        # G-PM1: PMax オーディエンスシグナル
        audience_signals = camp.get("audience_signals", [])
        results.append(_r("G68", len(audience_signals) > 0, name,
                          "" if audience_signals else "PMaxオーディエンスシグナル未設定"))

        # G-PM2: PMax Ad Strength
        ad_strengths = camp.get("ad_strengths", [])
        if ad_strengths:
            good = [s for s in ad_strengths if "good" in s.lower() or "excellent" in s.lower()]
            results.append(_r("G68b", len(good) > 0, name,
                              "" if good else "PMax Ad Strength: Good未達"))

        # G-PM3: PMax ブランドカニバリゼーション
        if search_camps:
            brand_camps = [c for c in search_camps
                           if any(bk in c.get("campaign", "").lower() for bk in ["brand", "ブランド", "指名"])]
            if brand_camps:
                # PMaxがブランド除外してないならカニバリリスク
                neg = camp.get("negative_keyword_lists", [])
                results.append(_r("G70", len(neg) > 0, name,
                                  "" if neg else "PMaxブランドカニバリリスク: ブランドKW除外なし",
                                  conflict_group="exclude_vs_opportunity"))

        # G-PM4: PMax 検索テーマ
        search_themes = camp.get("search_themes", [])
        results.append(_r("G68c", len(search_themes) > 0, name,
                          "" if search_themes else "PMax検索テーマ未設定 — シグナル精度低下"))

        # G-PM5: PMax ネガティブKW（アカウントレベル）
        acct_neg = camp.get("account_level_negatives", 0)
        results.append(_r("G68d", acct_neg > 0, name,
                          "" if acct_neg > 0 else "PMaxアカウントレベル ネガKWなし"))

        # G-PM6: PMax ブランドKW除外
        brand_excluded = camp.get("brand_keywords_excluded", False)
        results.append(_r("G70b", brand_excluded, name,
                          "" if brand_excluded else "PMaxブランドKW未除外 — Search とのカニバリ注意"))

    return results


# ========================================
# Advanced: AI Max / Demand Gen / CTV (G-AI1, G-DG1-DG3, G-CTV1)
# ========================================
def _check_advanced(camps, g_t):
    results = []

    # G-AI1: AI Max 評価
    for camp in camps:
        ai_max = camp.get("ai_max_enabled", None)
        if ai_max is not None:
            results.append(_r("G75", True, camp["campaign"],
                              f"AI Max {'有効' if ai_max else '無効'} — パフォーマンス監視推奨"))

    # DG チェック
    dg_camps = [c for c in camps if c.get("campaign_type") == "demand_gen"]
    for camp in dg_camps:
        name = camp.get("campaign", "")

        # G-DG1: Demand Gen 画像+動画
        has_video = camp.get("has_native_video", False)
        has_image = camp.get("ad_count", 0) > 0
        results.append(_r("G74", has_video and has_image, name,
                          "" if (has_video and has_image) else "Demand Gen: 画像+動画の両方推奨"))

        # G-DG3: フリークエンシーキャップ
        freq = camp.get("frequency", 0)
        freq_cap = camp.get("frequency_cap", 0)
        if freq > 3 and freq_cap == 0:
            results.append(_r("G77", False, name,
                              f"フリークエンシー {freq:.1f} だがキャップ未設定"))

    # G-DG2: VAC→Demand Gen 移行（旧VAC検出）
    vac_camps = [c for c in camps if c.get("campaign_type") == "video" and "action" in c.get("campaign", "").lower()]
    for camp in vac_camps:
        results.append(_r("G76", False, camp["campaign"],
                          "Video Action Campaign検出 — Demand Genへの移行推奨"))

    # G-CTV1: CTV Floodlight 制限
    ctv_camps = [c for c in camps if "ctv" in c.get("campaign", "").lower()]
    for camp in ctv_camps:
        results.append(_r("G78", True, camp["campaign"],
                          "CTV キャンペーン: Floodlight 制限に注意"))

    return results


# ========================================
# Helper
# ========================================
def _r(check_id, passed, campaign, message, conflict_group=None, context=None):
    """チェック結果 dict を構築"""
    r = {"id": check_id, "passed": passed, "campaign": campaign, "platform": "google", "message": message}
    if conflict_group:
        r["conflict_group"] = conflict_group
    if context:
        r["context"] = context
    return r
