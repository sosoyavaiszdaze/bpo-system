"""TikTok Ads API Adapter v2.0 - AdGroup + Events API/Pixel ステータス対応"""
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"


def fetch_tiktok_ads(config):
    """TikTok広告データを取得してunified formatに変換"""
    advertiser_id = config.get("advertiser_id", "")
    token_env = config.get("access_token_env", "")
    access_token = os.environ.get(token_env, "")

    if not access_token:
        access_token = config.get("access_token", "")
    if not advertiser_id or not access_token:
        log.warning("TikTok: advertiser_id or access_token missing")
        return None

    log.info(f"TikTok API: 広告主 {advertiser_id} からデータ取得中")

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. キャンペーンレベル
    campaigns = _fetch_campaign_report(advertiser_id, access_token, yesterday, today)

    # 2. 広告グループレベル
    adgroup_data = _fetch_adgroup_report(advertiser_id, access_token, yesterday, today)

    # 3. Pixel / Events API ステータス
    pixel_data = _fetch_pixel_status(advertiser_id, access_token)

    if not campaigns:
        log.warning("TikTok: データが0件")
        return None

    # 広告グループデータを統合
    _merge_adgroup_data(campaigns, adgroup_data)

    totals = _calc_totals(campaigns)

    result = {
        "source": "tiktok_api",
        "advertiser_id": advertiser_id,
        "date_range": {"since": yesterday, "until": today},
        "campaigns": campaigns,
        "totals": totals,
        "pixel_status": pixel_data,
    }

    log.info(f"TikTok API: {len(campaigns)}キャンペーン取得完了")
    return result


def _fetch_campaign_report(advertiser_id, access_token, start_date, end_date):
    """キャンペーンレベルレポート"""
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "data_level": "AUCTION_CAMPAIGN",
        "dimensions": json.dumps(["campaign_id"]),
        "metrics": json.dumps([
            "campaign_name",
            "impressions",
            "clicks",
            "spend",
            "conversion",
            "cost_per_conversion",
            "conversion_rate",
            "ctr",
            "cpm",
            "frequency",
            "total_purchase_value",
        ]),
        "start_date": start_date,
        "end_date": end_date,
        "page": 1,
        "page_size": 100,
    }

    query_string = urllib.parse.urlencode(params)
    url = f"{TIKTOK_API_BASE}/report/integrated/get/?{query_string}"

    try:
        req = urllib.request.Request(url, headers={"Access-Token": access_token})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if data.get("code") != 0:
            log.error(f"TikTok Campaign Report Error: {data.get('message', 'Unknown')}")
            return []

        campaigns = []
        for row in data.get("data", {}).get("list", []):
            camp = _parse_campaign(row)
            campaigns.append(camp)
        return campaigns

    except Exception as e:
        log.error(f"TikTok Campaign Report Error: {e}")
        return []


def _fetch_adgroup_report(advertiser_id, access_token, start_date, end_date):
    """広告グループレベルレポート"""
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "data_level": "AUCTION_ADGROUP",
        "dimensions": json.dumps(["adgroup_id", "campaign_id"]),
        "metrics": json.dumps([
            "adgroup_name",
            "impressions",
            "clicks",
            "spend",
            "conversion",
            "ctr",
            "frequency",
            "video_play_actions",
            "video_watched_2s",
            "video_watched_6s",
            "video_views_p25",
            "video_views_p50",
            "video_views_p75",
            "video_views_p100",
        ]),
        "start_date": start_date,
        "end_date": end_date,
        "page": 1,
        "page_size": 200,
    }

    query_string = urllib.parse.urlencode(params)
    url = f"{TIKTOK_API_BASE}/report/integrated/get/?{query_string}"

    try:
        req = urllib.request.Request(url, headers={"Access-Token": access_token})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if data.get("code") != 0:
            log.warning(f"TikTok AdGroup Report Error: {data.get('message', '')}")
            return {}

        adgroups_by_campaign = {}
        for row in data.get("data", {}).get("list", []):
            dims = row.get("dimensions", {})
            camp_id = dims.get("campaign_id", "")
            metrics = row.get("metrics", {})
            if camp_id not in adgroups_by_campaign:
                adgroups_by_campaign[camp_id] = []

            # 動画完視聴率の計算
            plays = float(metrics.get("video_play_actions", 0))
            p100 = float(metrics.get("video_views_p100", 0))
            completion_rate = round(p100 / plays * 100, 1) if plays > 0 else 0

            adgroups_by_campaign[camp_id].append({
                "name": metrics.get("adgroup_name", ""),
                "id": dims.get("adgroup_id", ""),
                "impressions": float(metrics.get("impressions", 0)),
                "spend": float(metrics.get("spend", 0)),
                "frequency": float(metrics.get("frequency", 0)),
                "video_completion_rate": completion_rate,
            })
        return adgroups_by_campaign

    except Exception as e:
        log.warning(f"TikTok AdGroup Report Error: {e}")
        return {}


def _fetch_pixel_status(advertiser_id, access_token):
    """TikTok Pixel / Events API ステータス取得"""
    pixel_data = {
        "pixel_installed": False,
        "events_api_enabled": False,
        "ttclid_passback": False,
    }

    # Pixel リスト取得
    params = urllib.parse.urlencode({
        "advertiser_id": advertiser_id,
    })
    url = f"{TIKTOK_API_BASE}/pixel/list/?{params}"

    try:
        req = urllib.request.Request(url, headers={"Access-Token": access_token})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        if data.get("code") == 0:
            pixels = data.get("data", {}).get("pixels", [])
            if pixels:
                pixel_data["pixel_installed"] = True
                # Events API Gateway 有効チェック
                for px in pixels:
                    if px.get("advanced_matching_fields"):
                        pixel_data["events_api_enabled"] = True
                    if px.get("ttclid_enabled"):
                        pixel_data["ttclid_passback"] = True

    except Exception as e:
        log.warning(f"TikTok Pixel Status Error: {e}")

    return pixel_data


def _parse_campaign(row):
    """APIレスポンスをunified formatに変換"""
    metrics = row.get("metrics", {})
    dimensions = row.get("dimensions", {})

    cost = float(metrics.get("spend", 0))
    conversions = float(metrics.get("conversion", 0))
    purchase_value = float(metrics.get("total_purchase_value", 0))

    camp = {
        "campaign": metrics.get("campaign_name", dimensions.get("campaign_id", "unknown")),
        "campaign_id": dimensions.get("campaign_id", ""),
        "platform": "tiktok",
        "campaign_type": "in_feed",
        "status": "ENABLED",
        "bidding_strategy": "auto",
        "daily_budget": 0,
        "impressions": float(metrics.get("impressions", 0)),
        "clicks": float(metrics.get("clicks", 0)),
        "cost": cost,
        "conversions": conversions,
        "cpa": float(metrics.get("cost_per_conversion", 0)),
        "ctr": float(metrics.get("ctr", 0)),
        "cpm": float(metrics.get("cpm", 0)),
        "frequency": float(metrics.get("frequency", 0)),
        "roas": round(purchase_value / cost, 2) if cost > 0 and purchase_value > 0 else 0,
        "conversion_value": purchase_value,
        "revenue": purchase_value,
        # 拡張フィールド
        "adgroup_count": 0,
        "learning_phase": False,
        "video_completion_rate": 0,
        "ad_count": 0,
    }

    # CPA未算出の場合
    if camp["cpa"] == 0 and conversions > 0:
        camp["cpa"] = round(cost / conversions, 2)

    # 学習フェーズ判定: 50CV/7日 = 日次7.14CV
    if conversions < 7.14:
        camp["learning_phase"] = True

    return camp


def _merge_adgroup_data(campaigns, adgroups_by_campaign):
    """広告グループデータをキャンペーンに統合"""
    for camp in campaigns:
        camp_id = camp.get("campaign_id", "")
        adgroups = adgroups_by_campaign.get(camp_id, [])
        camp["adgroup_count"] = len(adgroups)
        # 動画完視聴率の平均
        completion_rates = [ag["video_completion_rate"] for ag in adgroups if ag.get("video_completion_rate", 0) > 0]
        if completion_rates:
            camp["video_completion_rate"] = round(sum(completion_rates) / len(completion_rates), 1)


def _calc_totals(campaigns):
    """集計値を計算"""
    total_cost = sum(c["cost"] for c in campaigns)
    total_cv = sum(c["conversions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_imps = sum(c["impressions"] for c in campaigns)
    total_revenue = sum(c.get("revenue", 0) for c in campaigns)
    avg_cpa = round(total_cost / total_cv, 2) if total_cv > 0 else 0.0
    avg_ctr = round(total_clicks / total_imps * 100, 2) if total_imps > 0 else 0.0
    total_roas = round(total_revenue / total_cost, 2) if total_cost > 0 else 0.0

    return {
        "campaign_count": len(campaigns),
        "total_cost": total_cost,
        "total_conversions": total_cv,
        "total_clicks": total_clicks,
        "total_impressions": total_imps,
        "total_revenue": total_revenue,
        "total_roas": total_roas,
        "avg_cpa": avg_cpa,
        "avg_ctr": avg_ctr,
    }
