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

        # platform 推定（未設定 or "unknown" の場合）
        if not camp.get("platform") or camp["platform"] == "unknown":
            source = data.get("source", "")
            inferred = _infer_platform(camp, source)
            if inferred != "unknown":
                camp["platform"] = inferred
            else:
                camp["platform"] = "unknown"
                log.warning(f"Campaign[{i}] '{camp.get('campaign', '?')}': platform推定不可、unknown設定")

        # 自動計算: CPA（未設定 or 0 の場合のみ）
        if not camp.get("cpa") and camp.get("conversions", 0) > 0 and camp.get("cost", 0) > 0:
            camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)

        # 自動計算: CTR（未設定 or 0 の場合のみ）
        if not camp.get("ctr") and camp.get("impressions", 0) > 0 and camp.get("clicks", 0) > 0:
            camp["ctr"] = round(camp["clicks"] / camp["impressions"] * 100, 2)

        # conversion_value と revenue の同期
        cv_val = camp.get("conversion_value", 0)
        rev_val = camp.get("revenue", 0)
        if cv_val > 0 and rev_val == 0:
            camp["revenue"] = cv_val
        elif rev_val > 0 and cv_val == 0:
            camp["conversion_value"] = rev_val

        # 自動計算: ROAS（未設定 or 0 の場合のみ）
        effective_value = camp.get("conversion_value", 0) or camp.get("revenue", 0)
        if not camp.get("roas") and effective_value > 0 and camp.get("cost", 0) > 0:
            camp["roas"] = round(effective_value / camp["cost"], 2)

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


def _infer_platform(camp, source):
    """platform推定: アダプタ名 → キャンペーン名ヒューリスティック → unknown"""
    # 1. アダプタ名（source）から確定
    if "google" in source.lower():
        return "google"
    if "meta" in source.lower():
        return "meta"
    if "tiktok" in source.lower():
        return "tiktok"

    # 2. キャンペーン名ヒューリスティック（フォールバック、warning付き）
    name = camp.get("campaign", "").lower()
    inferred = None
    if any(k in name for k in ["google", "search", "pmax", "gdn", "youtube"]):
        inferred = "google"
    elif any(k in name for k in ["meta", "fb", "ig", "facebook", "instagram", "reels"]):
        inferred = "meta"
    elif any(k in name for k in ["tiktok", "spark", "pangle"]):
        inferred = "tiktok"

    if inferred:
        log.warning(f"platform をキャンペーン名から推定: '{camp.get('campaign')}' → {inferred}")
        return inferred

    return "unknown"


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


def validate_check_results(checks):
    """チェック結果のplatformフィールド検証（警告のみ、落とさない）

    Args:
        checks: チェック結果リスト
    Returns:
        list: 入力をそのまま返す
    """
    missing_platform = []
    for check in checks:
        if not check.get("platform"):
            missing_platform.append(check.get("id", "unknown"))
    if missing_platform:
        log.warning(f"platformフィールド未設定のチェック: {missing_platform[:10]}")
    return checks
