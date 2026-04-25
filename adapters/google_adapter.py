"""Google Ads API Adapter - google-ads Python ライブラリ v30.1.0 (API v24)"""
import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger("bpo")


def fetch_google_ads(config):
    """Google Ads データを取得して unified format に変換

    Args:
        config: Google Ads 設定 dict (customer_id, developer_token_env, credentials_path)
    Returns:
        dict: unified format {"source": "google_api", "campaigns": [...], "totals": {...}}
    """
    customer_id = config.get("customer_id", "").replace("-", "")
    developer_token_env = config.get("developer_token_env", "")
    developer_token = os.environ.get(developer_token_env, "")
    credentials_path = config.get("credentials_path", "")

    if not customer_id or not developer_token:
        log.warning("Google Ads: customer_id or developer_token missing")
        return None

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        log.error("Google Ads: google-ads パッケージ未インストール (pip install google-ads)")
        return None

    try:
        # 認証情報ファイルから GoogleAdsClient を初期化
        if credentials_path and os.path.exists(credentials_path):
            client = GoogleAdsClient.load_from_storage(credentials_path)
        else:
            # 環境変数からクライアントを構成
            client = GoogleAdsClient.load_from_dict({
                "developer_token": developer_token,
                "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
                "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
                "refresh_token": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
                "use_proto_plus": True,
            })

        log.info(f"Google Ads API: アカウント {customer_id} からデータ取得中")

        campaigns = _fetch_campaigns(client, customer_id)
        keywords = _fetch_keywords(client, customer_id)
        conversions = _fetch_conversion_actions(client, customer_id)
        assets = _fetch_asset_info(client, customer_id)
        change_history = _fetch_change_history(client, customer_id)

        # キーワード情報をキャンペーンに統合
        _merge_keyword_data(campaigns, keywords)
        # コンバージョン設定をキャンペーンに統合
        _merge_conversion_data(campaigns, conversions)
        # アセット情報を統合
        _merge_asset_data(campaigns, assets)

        if not campaigns:
            log.warning("Google Ads: データが0件")
            return None

        totals = _calc_totals(campaigns)

        result = {
            "source": "google_api",
            "customer_id": customer_id,
            "date_range": {
                "since": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                "until": datetime.now().strftime("%Y-%m-%d"),
            },
            "campaigns": campaigns,
            "totals": totals,
            "change_history": change_history,
        }

        log.info(f"Google Ads API: {len(campaigns)}キャンペーン取得完了")
        return result

    except Exception as e:
        log.error(f"Google Ads API Error: {e}")
        return None


def _fetch_campaigns(client, customer_id):
    """キャンペーンレベルのデータ取得"""
    ga_service = client.get_service("GoogleAdsService")

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            campaign.bidding_strategy_type,
            campaign_budget.amount_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.conversions_value,
            metrics.ctr,
            metrics.average_cpm,
            campaign.optimization_score
        FROM campaign
        WHERE segments.date BETWEEN '{yesterday}' AND '{today}'
            AND campaign.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
        LIMIT 200
    """

    campaigns = []
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            camp = _parse_campaign_row(row)
            campaigns.append(camp)
    except Exception as e:
        log.error(f"Google Ads Campaign fetch error: {e}")

    return campaigns


def _parse_campaign_row(row):
    """GAQL レスポンス行を unified format キャンペーン dict に変換"""
    cost = row.metrics.cost_micros / 1_000_000 if row.metrics.cost_micros else 0
    conversions = float(row.metrics.conversions) if row.metrics.conversions else 0
    conv_value = float(row.metrics.conversions_value) if row.metrics.conversions_value else 0
    impressions = float(row.metrics.impressions) if row.metrics.impressions else 0
    clicks = float(row.metrics.clicks) if row.metrics.clicks else 0

    # キャンペーンタイプのマッピング
    channel_type = str(row.campaign.advertising_channel_type).lower()
    type_map = {
        "search": "search",
        "display": "display",
        "shopping": "shopping",
        "video": "video",
        "performance_max": "pmax",
        "demand_gen": "demand_gen",
        "local": "local",
        "smart": "smart",
        "app": "app",
    }
    campaign_type = "unknown"
    for key, val in type_map.items():
        if key in channel_type:
            campaign_type = val
            break

    # ステータスのマッピング
    status_str = str(row.campaign.status).upper()
    status = "ENABLED" if "ENABLED" in status_str else "PAUSED"

    # 入札戦略
    bidding_str = str(row.campaign.bidding_strategy_type).lower()
    bidding_map = {
        "target_cpa": "target_cpa",
        "target_roas": "target_roas",
        "maximize_conversions": "max_conversions",
        "maximize_conversion_value": "max_conv_value",
        "manual_cpc": "manual_cpc",
        "manual_cpm": "manual_cpm",
        "target_impression_share": "target_impression_share",
    }
    bidding_strategy = "unknown"
    for key, val in bidding_map.items():
        if key in bidding_str:
            bidding_strategy = val
            break

    # 予算 (micros → 通常単位)
    daily_budget = row.campaign_budget.amount_micros / 1_000_000 if row.campaign_budget.amount_micros else 0

    camp = {
        "campaign": row.campaign.name,
        "campaign_id": str(row.campaign.id),
        "platform": "google",
        "campaign_type": campaign_type,
        "status": status,
        "bidding_strategy": bidding_strategy,
        "daily_budget": round(daily_budget, 2),
        "impressions": impressions,
        "clicks": clicks,
        "cost": round(cost, 2),
        "conversions": conversions,
        "conversion_value": conv_value,
        "cpa": round(cost / conversions, 2) if conversions > 0 else 0,
        "ctr": float(row.metrics.ctr) * 100 if row.metrics.ctr else 0,
        "cpm": float(row.metrics.average_cpm) / 1_000_000 if row.metrics.average_cpm else 0,
        "roas": round(conv_value / cost, 2) if cost > 0 and conv_value > 0 else 0,
        "frequency": 0,  # Google Ads にはキャンペーンレベルのフリークエンシーなし
        "optimization_score": float(row.campaign.optimization_score) if row.campaign.optimization_score else None,
        # Phase 2 で追加されるフィールド
        "keyword_count": 0,
        "match_types": [],
        "negative_keyword_lists": [],
        "quality_score_avg": None,
        "ad_count": 0,
        "asset_count": 0,
        "enhanced_conversions": False,
        "learning_phase": False,
    }

    return camp


def _fetch_keywords(client, customer_id):
    """キーワードレベルデータ取得（QS、マッチタイプなど）"""
    ga_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
            ad_group.campaign.id,
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            ad_group_criterion.quality_info.quality_score,
            ad_group_criterion.status,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
            AND metrics.impressions > 0
        LIMIT 1000
    """

    keywords = {}
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            camp_id = str(row.ad_group.campaign.id)
            if camp_id not in keywords:
                keywords[camp_id] = {
                    "count": 0,
                    "match_types": set(),
                    "quality_scores": [],
                    "zero_impression_count": 0,
                }
            kw_data = keywords[camp_id]
            kw_data["count"] += 1
            match_type = str(row.ad_group_criterion.keyword.match_type).lower()
            kw_data["match_types"].add(match_type)
            qs = row.ad_group_criterion.quality_info.quality_score
            if qs and qs > 0:
                kw_data["quality_scores"].append(qs)
    except Exception as e:
        log.warning(f"Google Ads Keyword fetch error: {e}")

    return keywords


def _fetch_conversion_actions(client, customer_id):
    """コンバージョンアクション設定取得"""
    ga_service = client.get_service("GoogleAdsService")

    query = """
        SELECT
            conversion_action.name,
            conversion_action.type,
            conversion_action.status,
            conversion_action.attribution_model_settings.attribution_model,
            conversion_action.value_settings.default_value
        FROM conversion_action
        WHERE conversion_action.status = 'ENABLED'
    """

    conversions = []
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            conv = {
                "name": row.conversion_action.name,
                "type": str(row.conversion_action.type),
                "attribution_model": str(
                    row.conversion_action.attribution_model_settings.attribution_model
                ) if row.conversion_action.attribution_model_settings else "unknown",
                "default_value": float(
                    row.conversion_action.value_settings.default_value
                ) if row.conversion_action.value_settings else 0,
            }
            conversions.append(conv)
    except Exception as e:
        log.warning(f"Google Ads Conversion action fetch error: {e}")

    return conversions


def _fetch_asset_info(client, customer_id):
    """RSA / PMax アセット情報取得"""
    ga_service = client.get_service("GoogleAdsService")

    # RSA ヘッドライン・説明文数をキャンペーン別に集計
    query = """
        SELECT
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group.campaign.id,
            ad_group_ad.ad_strength
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
            AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
        LIMIT 500
    """

    assets = {}
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            camp_id = str(row.ad_group.campaign.id)
            if camp_id not in assets:
                assets[camp_id] = {
                    "ad_count": 0,
                    "headline_counts": [],
                    "description_counts": [],
                    "ad_strengths": [],
                }
            asset_data = assets[camp_id]
            asset_data["ad_count"] += 1
            headlines = row.ad_group_ad.ad.responsive_search_ad.headlines
            descriptions = row.ad_group_ad.ad.responsive_search_ad.descriptions
            if headlines:
                asset_data["headline_counts"].append(len(headlines))
            if descriptions:
                asset_data["description_counts"].append(len(descriptions))
            ad_strength = str(row.ad_group_ad.ad_strength)
            asset_data["ad_strengths"].append(ad_strength)
    except Exception as e:
        log.warning(f"Google Ads Asset fetch error: {e}")

    return assets


def _fetch_change_history(client, customer_id):
    """直近14日の変更履歴取得"""
    ga_service = client.get_service("GoogleAdsService")

    since = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    query = f"""
        SELECT
            change_event.change_date_time,
            change_event.change_resource_type,
            change_event.resource_change_operation,
            change_event.user_email
        FROM change_event
        WHERE change_event.change_date_time >= '{since}'
            AND change_event.change_date_time <= '{today}'
        ORDER BY change_event.change_date_time DESC
        LIMIT 100
    """

    changes = []
    try:
        response = ga_service.search(customer_id=customer_id, query=query)
        for row in response:
            change = {
                "date": str(row.change_event.change_date_time),
                "resource_type": str(row.change_event.change_resource_type),
                "operation": str(row.change_event.resource_change_operation),
                "user": str(row.change_event.user_email),
            }
            changes.append(change)
    except Exception as e:
        log.warning(f"Google Ads Change history fetch error: {e}")

    return changes


def _merge_keyword_data(campaigns, keywords):
    """キーワードデータをキャンペーンに統合"""
    for camp in campaigns:
        camp_id = camp.get("campaign_id", "")
        kw_data = keywords.get(camp_id)
        if kw_data:
            camp["keyword_count"] = kw_data["count"]
            camp["match_types"] = list(kw_data["match_types"])
            if kw_data["quality_scores"]:
                camp["quality_score_avg"] = round(
                    sum(kw_data["quality_scores"]) / len(kw_data["quality_scores"]), 1
                )


def _merge_conversion_data(campaigns, conversions):
    """コンバージョン設定データをキャンペーンに統合"""
    has_enhanced = any("enhanced" in c.get("type", "").lower() for c in conversions)
    has_dda = any("data_driven" in c.get("attribution_model", "").lower() for c in conversions)

    for camp in campaigns:
        camp["enhanced_conversions"] = has_enhanced
        camp["attribution_model"] = "dda" if has_dda else "other"
        camp["conversion_action_count"] = len(conversions)


def _merge_asset_data(campaigns, assets):
    """アセット情報をキャンペーンに統合"""
    for camp in campaigns:
        camp_id = camp.get("campaign_id", "")
        asset_data = assets.get(camp_id)
        if asset_data:
            camp["ad_count"] = asset_data["ad_count"]
            camp["asset_count"] = sum(asset_data.get("headline_counts", []))
            camp["ad_strengths"] = asset_data.get("ad_strengths", [])


def _calc_totals(campaigns):
    """集計値を計算"""
    total_cost = sum(c["cost"] for c in campaigns)
    total_cv = sum(c["conversions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_imps = sum(c["impressions"] for c in campaigns)
    total_conv_value = sum(c.get("conversion_value", 0) for c in campaigns)
    avg_cpa = round(total_cost / total_cv, 2) if total_cv > 0 else 0.0
    avg_ctr = round(total_clicks / total_imps * 100, 2) if total_imps > 0 else 0.0
    total_roas = round(total_conv_value / total_cost, 2) if total_cost > 0 else 0.0

    return {
        "campaign_count": len(campaigns),
        "total_cost": total_cost,
        "total_conversions": total_cv,
        "total_clicks": total_clicks,
        "total_impressions": total_imps,
        "total_conversion_value": total_conv_value,
        "total_roas": total_roas,
        "avg_cpa": avg_cpa,
        "avg_ctr": avg_ctr,
    }
