"""TikTok Ads チェック — テクニカル/クリエイティブ/入札/構造 (T01-T35)"""
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

    return results


def _check_technical(camps, pixel_status):
    """テクニカルチェック T-TC1-TC2"""
    results = []
    ps = pixel_status or {}

    # T-TC1: Pixel + Events API
    results.append({
        "id": "T-TC1", "passed": ps.get("pixel_installed", False),
        "campaign": "アカウント全体", "platform": "tiktok",
        "message": "" if ps.get("pixel_installed") else "TikTok Pixel 未設置",
    })

    # T-TC2: ttclid パスバック
    results.append({
        "id": "T-TC2", "passed": ps.get("ttclid_passback", False),
        "campaign": "アカウント全体", "platform": "tiktok",
        "message": "" if ps.get("ttclid_passback") else "ttclid パスバック未設定",
    })

    return results


def _check_creative(camps, t_t):
    """クリエイティブチェック T-CR1-CR10"""
    results = []
    creative_t = t_t.get("creative", {})
    vcr_min = creative_t.get("video_completion_rate_min", 15.0)

    for camp in camps:
        name = camp.get("campaign", "")

        # T-CR3: 動画完視聴率 ≥15%
        vcr = camp.get("video_completion_rate", 0)
        if vcr > 0:
            results.append({
                "id": "T-CR3", "passed": vcr >= vcr_min, "campaign": name, "platform": "tiktok",
                "message": "" if vcr >= vcr_min else f"動画完視聴率 {vcr:.1f}% (推奨: ≥{vcr_min}%)",
            })

        # T-CR4: Spark Ads
        is_spark = "spark" in camp.get("campaign_type", "").lower() or "spark" in name.lower()
        if is_spark:
            results.append({
                "id": "T-CR4", "passed": True, "campaign": name, "platform": "tiktok",
                "message": "Spark Ads 使用中 ✓",
            })

    # T-CR7: 全体のクリエイティブ本数
    total_ads = sum(c.get("ad_count", 0) for c in camps)
    if total_ads > 0 and len(camps) > 0:
        avg_ads = total_ads / len(camps)
        results.append({
            "id": "T-CR7", "passed": avg_ads >= 5, "campaign": "全TikTok", "platform": "tiktok",
            "message": "" if avg_ads >= 5 else f"平均クリエイティブ数 {avg_ads:.1f} (推奨: ≥5)",
        })

    return results


def _check_bidding_learning(camps, t_t):
    """入札・学習チェック T-BL1-BL3"""
    results = []
    learning_t = t_t.get("learning_phase", {})
    min_weekly_cv = learning_t.get("min_weekly_conversions", 50)
    daily_min = min_weekly_cv / 7

    for camp in camps:
        name = camp.get("campaign", "")

        # T-BL1: 学習フェーズ 50CV/7日
        if camp.get("conversions", 0) < daily_min and camp.get("cost", 0) > 0:
            results.append({
                "id": "T-BL1", "passed": False, "campaign": name, "platform": "tiktok",
                "message": f"学習フェーズ未達: 日次CV {camp['conversions']:.1f} (週{min_weekly_cv}必要)",
            })

        # T-BL2: 予算充足率
        budget = camp.get("daily_budget", 0)
        cost = camp.get("cost", 0)
        if budget > 0 and cost > 0:
            utilization = cost / budget
            if utilization > 0.95:
                results.append({
                    "id": "T-BL2", "passed": False, "campaign": name, "platform": "tiktok",
                    "message": f"予算制約: 消化率 {utilization * 100:.0f}%",
                })

    return results


def _check_structure(camps, t_t):
    """構造チェック T-ST1-ST6"""
    results = []
    struct_t = t_t.get("structure", {})
    max_adgroups = struct_t.get("max_adgroups_per_campaign", 5)

    for camp in camps:
        name = camp.get("campaign", "")
        adgroup_count = camp.get("adgroup_count", 0)

        # T-ST5: 広告グループ数バランス
        if adgroup_count > max_adgroups:
            results.append({
                "id": "T-ST5", "passed": False, "campaign": name, "platform": "tiktok",
                "message": f"広告グループ数 {adgroup_count} (上限: {max_adgroups})",
            })

    return results
