"""低効率セグメント検出 - CVゼロや高CPAのセグメントを特定"""
import logging

log = logging.getLogger("bpo")


def detect_waste(client_id, data, thresholds):
    """低効率キャンペーン・セグメントを検出"""
    waste_cfg = thresholds.get("segment_waste", {})
    min_imps = waste_cfg.get("min_impressions", 100)
    min_cost = waste_cfg.get("min_cost", 3000)
    max_cpa_ratio = waste_cfg.get("max_cpa_ratio", 3.0)

    campaigns = data.get("campaigns", [])
    totals = data.get("totals", {})
    avg_cpa = totals.get("avg_cpa", 0)

    waste_items = []
    total_waste_cost = 0

    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        cost = camp.get("cost", 0)
        cv = camp.get("conversions", 0)
        imps = camp.get("impressions", 0)
        cpa = camp.get("cpa", 0)

        # CV 0 でコスト発生
        if cv == 0 and cost >= min_cost and imps >= min_imps:
            waste_items.append({
                "campaign": name,
                "type": "zero_cv",
                "cost": cost,
                "impressions": imps,
                "message": f"CV 0件でコスト ¥{cost:,.0f} が無駄",
                "action": "停止または大幅改善",
                "severity": "critical",
            })
            total_waste_cost += cost

        # CPA が平均の N倍以上
        elif avg_cpa > 0 and cpa > avg_cpa * max_cpa_ratio and cost >= min_cost:
            excess = cost - (avg_cpa * cv) if cv > 0 else cost
            waste_items.append({
                "campaign": name,
                "type": "high_cpa",
                "cost": cost,
                "cpa": cpa,
                "avg_cpa": avg_cpa,
                "ratio": round(cpa / avg_cpa, 1),
                "message": f"CPA ¥{cpa:,.0f} が平均の{cpa/avg_cpa:.1f}倍。超過コスト推定 ¥{excess:,.0f}",
                "action": "ターゲティング縮小またはクリエイティブ改善",
                "severity": "warning",
            })
            total_waste_cost += max(0, excess)

    total_cost = totals.get("total_cost", 0)
    waste_pct = round(total_waste_cost / total_cost * 100, 1) if total_cost > 0 else 0

    result = {
        "waste_items": waste_items,
        "waste_count": len(waste_items),
        "total_waste_cost": total_waste_cost,
        "total_cost": total_cost,
        "waste_percentage": waste_pct,
        "potential_savings": f"¥{total_waste_cost:,.0f} ({waste_pct}%)",
    }

    log.info(f"[{client_id}] 低効率検出完了: {len(waste_items)}件, 無駄コスト ¥{total_waste_cost:,.0f} ({waste_pct}%)")
    return result
