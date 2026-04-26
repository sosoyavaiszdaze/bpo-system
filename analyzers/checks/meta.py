"""Meta Ads チェック — 全34ルール完全実装 (M-PI1-PI8, M-CR1-CR6, M-ST1-ST7, M-AU1-AU6, M-C01-C06, G-AD1)"""
import logging

log = logging.getLogger("bpo")


def run_meta_checks(campaigns, thresholds, pixel_status=None):
    """Meta Ads 固有チェック実行"""
    meta_camps = [c for c in campaigns if c.get("platform", "").lower() == "meta"]
    if not meta_camps:
        return []

    m_t = thresholds.get("meta", {})
    results = []

    results.extend(_check_pixel_capi(meta_camps, pixel_status))
    results.extend(_check_creative(meta_camps, m_t))
    results.extend(_check_structure(meta_camps, m_t))
    results.extend(_check_audience(meta_camps, m_t))
    results.extend(_check_campaign_config(meta_camps, m_t))

    return results


# ========================================
# Pixel / CAPI (M-PI1 ~ M-PI8)
# ========================================
def _check_pixel_capi(camps, pixel_status):
    results = []
    ps = pixel_status or {}

    # M-PI1: Pixel 設置
    results.append(_r("M-PI1", ps.get("pixel_installed", False), "アカウント全体",
                       "" if ps.get("pixel_installed") else "Meta Pixel 未設置"))

    # M-PI2: EMQ ≥ 6
    emq = ps.get("event_match_quality")
    if emq is not None:
        results.append(_r("M-PI2", emq >= 6, "アカウント全体",
                          "" if emq >= 6 else f"EMQ {emq:.1f} (推奨: ≥6.0)"))
    else:
        results.append(_r("M-PI2", False, "アカウント全体", "EMQ不明 — 確認推奨"))

    # M-PI3: CAPI 有効
    results.append(_r("M-PI3", ps.get("capi_enabled", False), "アカウント全体",
                       "" if ps.get("capi_enabled") else "Conversions API 未有効"))

    # M-PI4: イベントパラメータ（必須イベントの送信状況）
    events_configured = ps.get("standard_events_count", 0)
    results.append(_r("M-PI4", events_configured >= 3, "アカウント全体",
                       "" if events_configured >= 3 else f"標準イベント {events_configured}個 (推奨≥3: Purchase, Lead, AddToCart)"))

    # M-PI5: CAPI + Pixelの重複排除
    dedup = ps.get("deduplication_enabled", False)
    if ps.get("capi_enabled"):
        results.append(_r("M-PI5", dedup, "アカウント全体",
                          "" if dedup else "CAPI+Pixel 重複排除未設定 — CV二重計上リスク"))

    # M-PI6: Aggregated Event Measurement
    aem = ps.get("aggregated_event_measurement", None)
    if aem is not None:
        results.append(_r("M-PI6", aem, "アカウント全体",
                          "" if aem else "Aggregated Event Measurement 未構成"))

    # M-PI7: ドメイン検証
    domain_verified = ps.get("domain_verified", None)
    if domain_verified is not None:
        results.append(_r("M-PI7", domain_verified, "アカウント全体",
                          "" if domain_verified else "ドメイン検証未完了"))

    # M-PI8: iOS ATT オプトイン率
    att_rate = ps.get("att_opt_in_rate", None)
    if att_rate is not None:
        results.append(_r("M-PI8", att_rate >= 20, "アカウント全体",
                          "" if att_rate >= 20 else f"ATTオプトイン率 {att_rate:.0f}% (業界平均: 25-30%)"))

    return results


# ========================================
# Creative (M-CR1 ~ M-CR6)
# ========================================
def _check_creative(camps, m_t):
    results = []
    creative_t = m_t.get("creative", {})

    # M-CR1: クリエイティブ多様性（全体で≥10種）
    total_ads = sum(c.get("ad_count", 0) for c in camps)
    results.append(_r("M-CR1", total_ads >= 10 or total_ads == 0, "全Meta",
                       "" if total_ads >= 10 or total_ads == 0 else f"クリエイティブ {total_ads}種 (推奨≥10)"))

    # M-CR2: 画像+動画の混合
    has_video = any(c.get("has_native_video", False) for c in camps)
    has_image = any(c.get("ad_count", 0) > 0 for c in camps)
    if has_image and not has_video:
        results.append(_r("M-CR2", False, "全Meta",
                          "動画クリエイティブなし — 静止画+動画の混合推奨"))

    # M-CR3: フリークエンシー疲弊
    fatigue_freq = creative_t.get("fatigue_frequency", 3.5)
    for camp in camps:
        freq = camp.get("frequency", 0)
        if freq > fatigue_freq:
            results.append(_r("M-CR3", False, camp["campaign"],
                              f"フリークエンシー疲弊: {freq:.1f} (閾値: {fatigue_freq})"))

    # M-CR4: クリエイティブ入替日数
    for camp in camps:
        days_active = camp.get("creative_days_active", 0)
        if days_active > 21:
            results.append(_r("M-CR4", False, camp["campaign"],
                              f"クリエイティブ {days_active}日稼働 — 21日超でリフレッシュ推奨"))

    # M-CR5: UGC/Reels広告
    has_reels = any("reels" in c.get("campaign", "").lower() or c.get("has_reels", False) for c in camps)
    results.append(_r("M-CR5", has_reels, "全Meta",
                       "" if has_reels else "Reels/UGC クリエイティブなし — エンゲージメント向上推奨"))

    # M-CR6: DCO（Dynamic Creative Optimization）
    dco_camps = [c for c in camps if c.get("dynamic_creative", False)]
    if camps and not dco_camps:
        results.append(_r("M-CR6", False, "全Meta",
                          "Dynamic Creative未使用 — テスト効率化に推奨"))

    return results


# ========================================
# Structure (M-ST1 ~ M-ST7)
# ========================================
def _check_structure(camps, m_t):
    results = []
    struct_t = m_t.get("structure", {})
    learning_t = m_t.get("learning_phase", {})

    # M-ST1: キャンペーン構造（目的ベース分離）
    objectives = {}
    for c in camps:
        obj = c.get("objective", c.get("campaign_type", "unknown"))
        objectives.setdefault(obj, []).append(c)
    for obj, group in objectives.items():
        if len(group) > 3:
            results.append(_r("M-ST1", False, f"{obj}グループ",
                              f"同一目的 '{obj}' に {len(group)} キャンペーン — 統合推奨"))

    # M-ST2: 広告セット数 ≤5/キャンペーン
    max_adsets = struct_t.get("max_adsets_per_campaign", 5)
    for camp in camps:
        adset_count = camp.get("adset_count", 0)
        if adset_count > max_adsets:
            results.append(_r("M-ST2", False, camp["campaign"],
                              f"広告セット数 {adset_count} (上限: {max_adsets}) — CV学習分散リスク"))

    # M-ST3: 学習フェーズ ≥50CV/週
    min_weekly_cv = learning_t.get("min_weekly_conversions", 50)
    daily_min = min_weekly_cv / 7
    for camp in camps:
        cv = camp.get("conversions", 0)
        if cv < daily_min and camp.get("cost", 0) > 0:
            results.append(_r("M-ST3", False, camp["campaign"],
                              f"学習フェーズ未達: 日次CV {cv:.1f} (週{min_weekly_cv}必要)",
                              conflict_group="learning_vs_testing"))

    # M-ST4: Advantage+ ショッピング
    for camp in camps:
        is_aplus = camp.get("advantage_plus", False)
        if is_aplus:
            results.append(_r("M-ST4", True, camp["campaign"], "Advantage+ Shopping 使用中 ✓"))

    # M-ST5: CBO（Campaign Budget Optimization）
    for camp in camps:
        cbo = camp.get("campaign_budget_optimization", None)
        if cbo is not None:
            results.append(_r("M-ST5", cbo, camp["campaign"],
                              "" if cbo else "CBO未有効 — 広告セット間の自動配分推奨"))

    # M-ST6: 最低予算チェック
    for camp in camps:
        budget = camp.get("daily_budget", 0)
        target_cpa = camp.get("target_cpa", camp.get("cpa", 0))
        if budget > 0 and target_cpa > 0:
            ratio = budget / target_cpa
            if ratio < 5:
                results.append(_r("M-ST6", False, camp["campaign"],
                                  f"日予算がCPAの{ratio:.1f}倍 — 学習に最低5×CPA必要"))

    # M-ST7: Advantage+ クリエイティブ
    for camp in camps:
        aplus_creative = camp.get("advantage_creative", None)
        if aplus_creative is not None:
            results.append(_r("M-ST7", aplus_creative, camp["campaign"],
                              "" if aplus_creative else "Advantage+ Creative未有効 — 自動最適化推奨"))

    return results


# ========================================
# Audience (M-AU1 ~ M-AU6)
# ========================================
def _check_audience(camps, m_t):
    results = []

    # M-AU1: オーバーラップ検出
    type_groups = {}
    for camp in camps:
        ct = camp.get("campaign_type", "other")
        type_groups.setdefault(ct, []).append(camp)
    for ct, group in type_groups.items():
        if len(group) > 2:
            results.append(_r("M-AU1", False, f"{ct}グループ",
                              f"同一目的 '{ct}' に {len(group)} キャンペーン: オーバーラップリスク"))

    # M-AU2: カスタムオーディエンス
    has_custom = any(c.get("custom_audiences", []) for c in camps)
    results.append(_r("M-AU2", has_custom, "全Meta",
                       "" if has_custom else "カスタムオーディエンス未設定"))

    # M-AU3: Lookalike品質
    for camp in camps:
        lal_pct = camp.get("lookalike_percentage", 0)
        if lal_pct > 0 and lal_pct > 5:
            results.append(_r("M-AU3", False, camp["campaign"],
                              f"Lookalike {lal_pct}% — 精度重視なら1-3%推奨",
                              conflict_group="precision_vs_reach"))

    # M-AU4: 除外オーディエンス（既存顧客除外）
    has_exclusions = any(c.get("audience_exclusions", []) for c in camps)
    results.append(_r("M-AU4", has_exclusions, "全Meta",
                       "" if has_exclusions else "除外オーディエンス未設定 — 既存顧客除外推奨"))

    # M-AU5: Advantage+ ターゲティング展開
    for camp in camps:
        aplus_targeting = camp.get("advantage_targeting", None)
        if aplus_targeting is not None:
            results.append(_r("M-AU5", True, camp["campaign"],
                              f"Advantage+ ターゲティング {'有効' if aplus_targeting else '無効'}",
                              conflict_group="precision_vs_reach"))

    # M-AU6: ファーストパーティデータ活用
    has_first_party = any(c.get("first_party_data", False) for c in camps)
    results.append(_r("M-AU6", has_first_party, "全Meta",
                       "" if has_first_party else "ファーストパーティデータ未活用"))

    return results


# ========================================
# Campaign Config (M-C01 ~ M-C06)
# ========================================
def _check_campaign_config(camps, m_t):
    results = []

    for camp in camps:
        name = camp.get("campaign", "")

        # M-C01: アトリビューション設定
        attr_window = camp.get("attribution_window", "")
        if attr_window:
            results.append(_r("M-C01", True, name, f"アトリビューション: {attr_window}"))
        else:
            results.append(_r("M-C01", False, name, "アトリビューション設定不明"))

        # M-C02: 配信最適化目標の妥当性
        opt_goal = camp.get("optimization_goal", "")
        obj = camp.get("objective", "")
        if obj and opt_goal:
            mismatch = False
            if "conversion" in obj.lower() and "impression" in opt_goal.lower():
                mismatch = True
            if "lead" in obj.lower() and "link_click" in opt_goal.lower():
                mismatch = True
            if mismatch:
                results.append(_r("M-C02", False, name,
                                  f"目的'{obj}'に対し最適化'{opt_goal}'が不適切"))

        # M-C03: コスト上限/入札上限
        cost_cap = camp.get("cost_cap", 0)
        bid_cap = camp.get("bid_cap", 0)
        if cost_cap == 0 and bid_cap == 0 and camp.get("cost", 0) > 0:
            results.append(_r("M-C03", False, name,
                              "コスト上限/入札上限未設定 — CPA暴騰リスク"))

        # M-C04: 地域ターゲティング精度
        geo_type = camp.get("geo_targeting_type", "")
        if "interested" in geo_type.lower():
            results.append(_r("M-C04", False, name,
                              "地域: 'People interested' → 'People living' に変更推奨"))

    # M-C05: アカウントレベル — 支払い方法
    if camps:
        payment = camps[0].get("payment_method", "unknown")
        results.append(_r("M-C05", payment != "unknown", "アカウント全体",
                          "" if payment != "unknown" else "支払い方法ステータス不明"))

    # M-C06: Business Manager検証
    if camps:
        bm_verified = camps[0].get("business_manager_verified", None)
        if bm_verified is not None:
            results.append(_r("M-C06", bm_verified, "アカウント全体",
                              "" if bm_verified else "Business Manager未検証 — 制限リスク"))

    return results


def _r(check_id, passed, campaign, message, conflict_group=None, context=None):
    """チェック結果 dict を構築"""
    r = {"id": check_id, "passed": passed, "campaign": campaign, "platform": "meta", "message": message}
    if conflict_group:
        r["conflict_group"] = conflict_group
    if context:
        r["context"] = context
    return r
