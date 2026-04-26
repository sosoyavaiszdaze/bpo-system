"""TikTok Ads チェック — 全23ルール完全実装 (T-TC1-TC2, T-CR1-CR8, T-BL1-BL3, T-ST1-ST6, T-C01-C04)"""
import logging

log = logging.getLogger("bpo")


def run_tiktok_checks(campaigns, thresholds, pixel_status=None):
    """TikTok Ads 固有チェック実行"""
    tt_camps = [c for c in campaigns if c.get("platform", "").lower() == "tiktok"]
    if not tt_camps:
        return []

    t_t = thresholds.get("tiktok", {})
    results = []

    results.extend(_check_technical(tt_camps, pixel_status))
    results.extend(_check_creative(tt_camps, t_t))
    results.extend(_check_bidding_learning(tt_camps, t_t))
    results.extend(_check_structure(tt_camps, t_t))
    results.extend(_check_campaign_config(tt_camps, t_t))

    return results


# ========================================
# Technical (T-TC1, T-TC2)
# ========================================
def _check_technical(camps, pixel_status):
    results = []
    ps = pixel_status or {}

    # T-TC1: Pixel + Events API
    results.append(_r("T-TC1", ps.get("pixel_installed", False), "アカウント全体",
                       "" if ps.get("pixel_installed") else "TikTok Pixel 未設置"))

    # T-TC2: ttclid パスバック
    results.append(_r("T-TC2", ps.get("ttclid_passback", False), "アカウント全体",
                       "" if ps.get("ttclid_passback") else "ttclid パスバック未設定 — CV計測精度低下"))

    return results


# ========================================
# Creative (T-CR1 ~ T-CR8)
# ========================================
def _check_creative(camps, t_t):
    results = []
    creative_t = t_t.get("creative", {})
    vcr_min = creative_t.get("video_completion_rate_min", 15.0)

    for camp in camps:
        name = camp.get("campaign", "")

        # T-CR1: 動画あり（TikTokは動画必須）
        has_video = camp.get("has_native_video", True)
        ad_count = camp.get("ad_count", 0)
        if ad_count > 0 and not has_video:
            results.append(_r("T-CR1", False, name, "TikTokに非動画クリエイティブ — 動画必須"))

        # T-CR2: 動画尺（9-15秒推奨）
        avg_duration = camp.get("avg_video_duration", 0)
        if avg_duration > 0:
            results.append(_r("T-CR2", 9 <= avg_duration <= 30, name,
                              "" if 9 <= avg_duration <= 30 else f"動画尺 {avg_duration:.0f}秒 (推奨: 9-30秒)"))

        # T-CR3: 動画完視聴率 ≥15%
        vcr = camp.get("video_completion_rate", 0)
        if vcr > 0:
            results.append(_r("T-CR3", vcr >= vcr_min, name,
                              "" if vcr >= vcr_min else f"動画完視聴率 {vcr:.1f}% (推奨: ≥{vcr_min}%)"))

        # T-CR4: Spark Ads
        is_spark = "spark" in camp.get("campaign_type", "").lower() or "spark" in name.lower() or camp.get("is_spark", False)
        if is_spark:
            results.append(_r("T-CR4", True, name, "Spark Ads 使用中 ✓"))

        # T-CR5: Hook率（最初3秒の維持率）
        hook_rate = camp.get("hook_rate", 0)
        if hook_rate > 0:
            results.append(_r("T-CR5", hook_rate >= 30, name,
                              "" if hook_rate >= 30 else f"Hook率 {hook_rate:.0f}% (推奨≥30%) — 冒頭3秒改善"))

        # T-CR6: テキストオーバーレイ有無
        has_overlay = camp.get("has_text_overlay", None)
        if has_overlay is not None:
            results.append(_r("T-CR6", has_overlay, name,
                              "" if has_overlay else "テキストオーバーレイなし — 訴求力UPに推奨"))

    # T-CR7: 全体のクリエイティブ本数
    total_ads = sum(c.get("ad_count", 0) for c in camps)
    if total_ads > 0 and len(camps) > 0:
        avg_ads = total_ads / len(camps)
        results.append(_r("T-CR7", avg_ads >= 5, "全TikTok",
                          "" if avg_ads >= 5 else f"平均クリエイティブ数 {avg_ads:.1f} (推奨: ≥5)"))

    # T-CR8: SmartVideo / TikTok Creative Center
    has_smart = any(c.get("smart_video", False) for c in camps)
    results.append(_r("T-CR8", has_smart, "全TikTok",
                       "" if has_smart else "SmartVideo/Creative Center テンプレート未使用"))

    return results


# ========================================
# Bidding / Learning (T-BL1 ~ T-BL3)
# ========================================
def _check_bidding_learning(camps, t_t):
    results = []
    learning_t = t_t.get("learning_phase", {})
    min_weekly_cv = learning_t.get("min_weekly_conversions", 50)
    daily_min = min_weekly_cv / 7

    for camp in camps:
        name = camp.get("campaign", "")
        cv = camp.get("conversions", 0)
        cost = camp.get("cost", 0)
        budget = camp.get("daily_budget", 0)

        # T-BL1: 学習フェーズ 50CV/7日
        if cv < daily_min and cost > 0:
            results.append(_r("T-BL1", False, name,
                              f"学習フェーズ未達: 日次CV {cv:.1f} (週{min_weekly_cv}必要)",
                              conflict_group="learning_vs_testing"))

        # T-BL2: 予算充足率
        if budget > 0 and cost > 0:
            utilization = cost / budget
            if utilization > 0.95:
                results.append(_r("T-BL2", False, name,
                                  f"予算制約: 消化率 {utilization * 100:.0f}% — 増額または入札調整"))

        # T-BL3: 入札戦略妥当性
        bidding = camp.get("bidding_strategy", "unknown")
        if bidding == "lowest_cost" and cv >= 30:
            results.append(_r("T-BL3", False, name,
                              "Lowest Cost使用中だがCV十分 → Cost Cap/Target CPA推奨"))
        elif bidding == "cost_cap":
            target = camp.get("target_cpa", 0)
            actual = camp.get("cpa", 0)
            if target > 0 and actual > target * 1.5:
                results.append(_r("T-BL3", False, name,
                                  f"Cost Cap ¥{target:,.0f} に対し実績CPA ¥{actual:,.0f} — 目標緩和検討"))

    return results


# ========================================
# Structure (T-ST1 ~ T-ST6)
# ========================================
def _check_structure(camps, t_t):
    results = []
    struct_t = t_t.get("structure", {})
    max_adgroups = struct_t.get("max_adgroups_per_campaign", 5)

    # T-ST1: 命名規則
    for camp in camps:
        name = camp.get("campaign", "")
        has_structure = bool(name) and any(c in name for c in ["_", "-", "|"])
        results.append(_r("T-ST1", has_structure, name,
                          "" if has_structure else f"命名規則不適合: '{name}'"))

    # T-ST2: 目的別キャンペーン分離
    obj_groups = {}
    for c in camps:
        obj = c.get("campaign_type", c.get("objective", "unknown"))
        obj_groups.setdefault(obj, []).append(c)
    for obj, group in obj_groups.items():
        if len(group) > 3:
            results.append(_r("T-ST2", False, f"{obj}グループ",
                              f"同一目的 '{obj}' に {len(group)} キャンペーン"))

    # T-ST3: リターゲティング分離
    has_retarget = any("retarget" in c.get("campaign", "").lower() or "rt" in c.get("campaign", "").lower() for c in camps)
    has_prospecting = any("prospect" in c.get("campaign", "").lower() or not any(k in c.get("campaign", "").lower() for k in ["retarget", "rt"]) for c in camps)
    if len(camps) > 1:
        results.append(_r("T-ST3", has_retarget and has_prospecting, "全TikTok",
                          "" if (has_retarget and has_prospecting) else "リターゲティング/プロスペクティング分離推奨"))

    # T-ST4: iOS vs Android 分離
    has_ios = any("ios" in c.get("campaign", "").lower() for c in camps)
    has_android = any("android" in c.get("campaign", "").lower() for c in camps)
    app_camps = [c for c in camps if c.get("campaign_type") == "app" or "install" in c.get("campaign", "").lower()]
    if app_camps:
        results.append(_r("T-ST4", has_ios and has_android, "全TikTok",
                          "" if (has_ios and has_android) else "アプリキャンペーン: iOS/Android 分離推奨"))

    # T-ST5: 広告グループ数バランス
    for camp in camps:
        adgroup_count = camp.get("adgroup_count", 0)
        if adgroup_count > max_adgroups:
            results.append(_r("T-ST5", False, camp["campaign"],
                              f"広告グループ数 {adgroup_count} (上限: {max_adgroups})"))

    # T-ST6: 重複ターゲティング検出
    for i, c1 in enumerate(camps):
        for c2 in camps[i + 1:]:
            # 簡易チェック: 同じ demographic + 同じ objective
            if (c1.get("campaign_type") == c2.get("campaign_type") and
                    c1.get("age_range") == c2.get("age_range") and
                    c1.get("gender") == c2.get("gender") and
                    c1.get("age_range") is not None):
                results.append(_r("T-ST6", False, f"{c1['campaign']} / {c2['campaign']}",
                                  "同一ターゲティング重複 — オーディエンス分離推奨"))
                break

    return results


# ========================================
# Campaign Config (T-C01 ~ T-C04)
# ========================================
def _check_campaign_config(camps, t_t):
    results = []

    for camp in camps:
        name = camp.get("campaign", "")

        # T-C01: 配信最適化イベント
        opt_event = camp.get("optimization_event", "")
        if opt_event and opt_event.lower() in ("impression", "click"):
            results.append(_r("T-C01", False, name,
                              f"最適化イベント '{opt_event}' — CV系イベントに変更推奨"))

        # T-C02: 配信タイプ（All Day vs Dayparting）
        delivery_type = camp.get("delivery_type", "")
        if delivery_type == "dayparting" and camp.get("conversions", 0) < 20:
            results.append(_r("T-C02", False, name,
                              "Dayparting中だがCV少 — All Day に変更して学習優先"))

    # T-C03: 自動入札 + 手動ターゲティング（推奨組合せ）
    for camp in camps:
        bidding = camp.get("bidding_strategy", "")
        targeting = camp.get("targeting_type", "")
        if "auto" in bidding.lower() and "auto" in targeting.lower():
            results.append(_r("T-C03", True, camp["campaign"],
                              "自動入札+自動ターゲティング ✓"))
        elif "manual" in bidding.lower() and "manual" in targeting.lower():
            results.append(_r("T-C03", False, camp["campaign"],
                              "手動入札+手動ターゲティング — 自動化推奨"))

    # T-C04: Pangle 配信面
    for camp in camps:
        pangle = camp.get("pangle_enabled", None)
        if pangle is True:
            placement_performance = camp.get("pangle_roas", 0)
            if placement_performance < 0.5:
                results.append(_r("T-C04", False, camp["campaign"],
                                  f"Pangle ROAS {placement_performance:.1f} — 除外検討"))

    return results


def _r(check_id, passed, campaign, message, conflict_group=None):
    """チェック結果 dict を構築"""
    r = {"id": check_id, "passed": passed, "campaign": campaign, "platform": "tiktok", "message": message}
    if conflict_group:
        r["conflict_group"] = conflict_group
    return r
