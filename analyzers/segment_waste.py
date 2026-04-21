"""低効率セグメント検出 v2.0 - 3媒体対応、CVゼロ・高CPA・媒体別無駄を特定"""
import logging

log = logging.getLogger("bpo")

# 媒体別デフォルト閾値
PLATFORM_DEFAULTS = {
    "google": {
        "min_cost_zero_cv": 5000, "min_cost_high_cpa": 3000, "max_cpa_ratio": 3.0,
        "min_impressions": 100, "frequency_waste_threshold": 5.0,
        "low_ctr_threshold": 0.5, "low_ctr_min_cost": 30000,
    },
    "meta": {
        "min_cost_zero_cv": 4000, "min_cost_high_cpa": 3000, "max_cpa_ratio": 2.5,
        "min_impressions": 500, "frequency_waste_threshold": 4.0,
        "low_ctr_threshold": 0.3, "low_ctr_min_cost": 25000,
    },
    "tiktok": {
        "min_cost_zero_cv": 4000, "min_cost_high_cpa": 3000, "max_cpa_ratio": 2.5,
        "min_impressions": 1000, "frequency_waste_threshold": 4.0,
        "low_ctr_threshold": 0.3, "low_ctr_min_cost": 25000,
    },
}


def _get_platform_config(platform, thresholds):
    """媒体別の閾値を取得"""
    waste_cfg = thresholds.get("segment_waste", {})
    defaults = PLATFORM_DEFAULTS.get(platform, PLATFORM_DEFAULTS["google"])
    return {
        "min_cost_zero_cv": waste_cfg.get("cv_zero_cost_min", defaults["min_cost_zero_cv"]),
        "min_cost_high_cpa": waste_cfg.get("min_cost", defaults["min_cost_high_cpa"]),
        "max_cpa_ratio": waste_cfg.get("max_cpa_ratio", defaults["max_cpa_ratio"]),
        "min_impressions": waste_cfg.get("min_impressions", defaults["min_impressions"]),
        "frequency_waste_threshold": defaults["frequency_waste_threshold"],
        "low_ctr_threshold": defaults["low_ctr_threshold"],
        "low_ctr_min_cost": defaults["low_ctr_min_cost"],
    }


def _group_by_platform(campaigns):
    """キャンペーンを媒体別にグループ化"""
    groups = {}
    for camp in campaigns:
        p = camp.get("platform", "unknown").lower()
        if p not in groups:
            groups[p] = []
        groups[p].append(camp)
    return groups


def detect_waste(client_id, data, thresholds):
    """3媒体対応の低効率セグメント検出"""
    campaigns = data.get("campaigns", [])
    totals = data.get("totals", {})
    total_cost = totals.get("total_cost", 0)
    overall_avg_cpa = totals.get("avg_cpa", 0)

    platform_groups = _group_by_platform(campaigns)
    waste_items = []
    total_waste_cost = 0

    for platform, camps in platform_groups.items():
        cfg = _get_platform_config(platform, thresholds)
        platform_label = {"google": "Google", "meta": "Meta", "tiktok": "TikTok"}.get(platform, platform)

        # 媒体別平均CPA
        p_cost = sum(c.get("cost", 0) for c in camps)
        p_cv = sum(c.get("conversions", 0) for c in camps)
        platform_avg_cpa = p_cost / p_cv if p_cv > 0 else overall_avg_cpa

        for camp in camps:
            name = camp.get("campaign", "unknown")
            cost = camp.get("cost", 0)
            cv = camp.get("conversions", 0)
            imps = camp.get("impressions", 0)
            cpa = camp.get("cpa", 0)
            ctr = camp.get("ctr", 0)
            roas = camp.get("roas", 0)
            freq = camp.get("frequency", 0)
            camp_type = camp.get("campaign_type", "")

            # W1: CV 0 でコスト発生
            if cv == 0 and cost >= cfg["min_cost_zero_cv"] and imps >= cfg["min_impressions"]:
                waste_items.append({
                    "campaign": name,
                    "platform": platform,
                    "campaign_type": camp_type,
                    "type": "zero_cv",
                    "cost": cost,
                    "impressions": imps,
                    "waste_amount": cost,
                    "message": f"[{platform_label}] {name}: CV 0件でコスト ¥{cost:,.0f} が全額無駄",
                    "action": "即停止、またはターゲティング・LP・クリエイティブを全面見直し",
                    "severity": "critical",
                })
                total_waste_cost += cost

            # W2: CPA が媒体平均の N倍以上
            elif platform_avg_cpa > 0 and cpa > platform_avg_cpa * cfg["max_cpa_ratio"] and cost >= cfg["min_cost_high_cpa"]:
                excess = cost - (platform_avg_cpa * cv) if cv > 0 else cost
                excess = max(0, excess)
                waste_items.append({
                    "campaign": name,
                    "platform": platform,
                    "campaign_type": camp_type,
                    "type": "high_cpa",
                    "cost": cost,
                    "cpa": cpa,
                    "platform_avg_cpa": platform_avg_cpa,
                    "ratio": round(cpa / platform_avg_cpa, 1),
                    "waste_amount": excess,
                    "message": f"[{platform_label}] {name}: CPA ¥{cpa:,.0f} が{platform_label}平均の{cpa/platform_avg_cpa:.1f}倍。超過コスト ¥{excess:,.0f}",
                    "action": "ターゲティング縮小、クリエイティブ改善、または入札引き下げ",
                    "severity": "warning",
                })
                total_waste_cost += excess

            # W3: フリークエンシー過多 + 低パフォーマンス
            elif freq >= cfg["frequency_waste_threshold"] and roas < 2.0 and cost >= cfg["min_cost_high_cpa"]:
                fatigue_waste = cost * 0.3  # 推定30%が疲労による無駄
                waste_items.append({
                    "campaign": name,
                    "platform": platform,
                    "campaign_type": camp_type,
                    "type": "frequency_waste",
                    "cost": cost,
                    "frequency": freq,
                    "roas": roas,
                    "waste_amount": fatigue_waste,
                    "message": f"[{platform_label}] {name}: Frequency {freq:.1f} + ROAS {roas:.1f} で疲労浪費 推定 ¥{fatigue_waste:,.0f}",
                    "action": "クリエイティブ差し替え + オーディエンス拡張",
                    "severity": "warning",
                })
                total_waste_cost += fatigue_waste

            # W4: 極端に低いCTR + 高コスト
            elif ctr < cfg["low_ctr_threshold"] and cost >= cfg["low_ctr_min_cost"] and imps >= cfg["min_impressions"]:
                low_ctr_waste = cost * 0.4  # CTR極低 = 40%が無駄と推定
                waste_items.append({
                    "campaign": name,
                    "platform": platform,
                    "campaign_type": camp_type,
                    "type": "low_ctr_waste",
                    "cost": cost,
                    "ctr": ctr,
                    "waste_amount": low_ctr_waste,
                    "message": f"[{platform_label}] {name}: CTR {ctr:.2f}% が極端に低く ¥{cost:,.0f} 消化。推定無駄 ¥{low_ctr_waste:,.0f}",
                    "action": "広告コピー・クリエイティブの全面刷新、またはターゲティング見直し",
                    "severity": "warning",
                })
                total_waste_cost += low_ctr_waste

    waste_pct = round(total_waste_cost / total_cost * 100, 1) if total_cost > 0 else 0

    # 媒体別サマリー
    platform_summary = {}
    for item in waste_items:
        p = item.get("platform", "unknown")
        if p not in platform_summary:
            platform_summary[p] = {"count": 0, "waste_cost": 0, "critical": 0}
        platform_summary[p]["count"] += 1
        platform_summary[p]["waste_cost"] += item.get("waste_amount", 0)
        if item.get("severity") == "critical":
            platform_summary[p]["critical"] += 1

    result = {
        "waste_items": waste_items,
        "waste_count": len(waste_items),
        "total_waste_cost": round(total_waste_cost),
        "total_cost": total_cost,
        "waste_percentage": waste_pct,
        "potential_savings": f"¥{total_waste_cost:,.0f} ({waste_pct}%)",
        "platform_summary": platform_summary,
    }

    log.info(f"[{client_id}] 低効率検出完了: {len(waste_items)}件, 無駄コスト ¥{total_waste_cost:,.0f} ({waste_pct}%)")
    return result
