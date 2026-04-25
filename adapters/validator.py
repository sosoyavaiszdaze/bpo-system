"""データバリデーション — 全アダプタ出力が unified format に準拠しているか検証"""
import logging

log = logging.getLogger("bpo")

# unified format の必須フィールドとデフォルト値
CAMPAIGN_REQUIRED_FIELDS = {
    "campaign": "",
    "platform": "unknown",
    "campaign_type": "unknown",
    "status": "ENABLED",
    "bidding_strategy": "unknown",
    "daily_budget": 0.0,
    "impressions": 0.0,
    "clicks": 0.0,
    "cost": 0.0,
    "conversions": 0.0,
    "cpa": 0.0,
    "ctr": 0.0,
    "cpm": 0.0,
    "frequency": 0.0,
    "roas": 0.0,
    "revenue": 0.0,
    "conversion_value": 0.0,
    # 拡張フィールド (Phase 1)
    "ad_count": 0,
    "keyword_count": 0,
    "match_types": [],
    "negative_keyword_lists": [],
    "quality_score_avg": None,
    "enhanced_conversions": False,
    "asset_count": 0,
    "learning_phase": False,
}

TOTALS_REQUIRED_FIELDS = {
    "campaign_count": 0,
    "total_cost": 0.0,
    "total_conversions": 0.0,
    "total_clicks": 0.0,
    "total_impressions": 0.0,
    "avg_cpa": 0.0,
    "avg_ctr": 0.0,
}


def validate_data(data):
    """アダプタ出力を検証し、不足フィールドにデフォルト値を設定

    Args:
        data: アダプタから返されたデータ dict
    Returns:
        dict: 検証済みデータ（不足フィールド補完済み）
    """
    if not data or not isinstance(data, dict):
        log.warning("Validator: データが空または不正な形式")
        return data

    campaigns = data.get("campaigns", [])
    issues = []

    for i, camp in enumerate(campaigns):
        for field, default in CAMPAIGN_REQUIRED_FIELDS.items():
            if field not in camp:
                camp[field] = default if not isinstance(default, list) else list(default)
                issues.append(f"Campaign[{i}] '{camp.get('campaign', '?')}': 不足フィールド '{field}' にデフォルト値設定")

        # 自動計算: CPA
        if camp.get("cpa", 0) == 0 and camp.get("conversions", 0) > 0 and camp.get("cost", 0) > 0:
            camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)

        # 自動計算: CTR
        if camp.get("ctr", 0) == 0 and camp.get("impressions", 0) > 0 and camp.get("clicks", 0) > 0:
            camp["ctr"] = round(camp["clicks"] / camp["impressions"] * 100, 2)

        # 自動計算: ROAS
        cv_val = camp.get("conversion_value", 0) or camp.get("revenue", 0)
        if camp.get("roas", 0) == 0 and cv_val > 0 and camp.get("cost", 0) > 0:
            camp["roas"] = round(cv_val / camp["cost"], 2)

    # totals 検証
    totals = data.get("totals", {})
    if not totals:
        totals = _recalc_totals(campaigns)
        data["totals"] = totals
    else:
        for field, default in TOTALS_REQUIRED_FIELDS.items():
            if field not in totals:
                totals[field] = default

    if issues:
        log.info(f"Validator: {len(issues)}件のフィールド補完 (最初: {issues[0]})")

    return data


def _recalc_totals(campaigns):
    """キャンペーンリストから totals を再計算"""
    total_cost = sum(c.get("cost", 0) for c in campaigns)
    total_cv = sum(c.get("conversions", 0) for c in campaigns)
    total_clicks = sum(c.get("clicks", 0) for c in campaigns)
    total_imps = sum(c.get("impressions", 0) for c in campaigns)

    return {
        "campaign_count": len(campaigns),
        "total_cost": total_cost,
        "total_conversions": total_cv,
        "total_clicks": total_clicks,
        "total_impressions": total_imps,
        "avg_cpa": round(total_cost / total_cv, 2) if total_cv > 0 else 0.0,
        "avg_ctr": round(total_clicks / total_imps * 100, 2) if total_imps > 0 else 0.0,
    }
