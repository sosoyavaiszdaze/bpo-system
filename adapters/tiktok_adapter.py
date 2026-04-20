"""TikTok Ads API Adapter - TikTok Business APIからデータ取得"""
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

    # 日付範囲（昨日）
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # レポートAPIでキャンペーンデータ取得
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
        "start_date": yesterday,
        "end_date": today,
        "page": 1,
        "page_size": 100,
    }

    query_string = urllib.parse.urlencode(params)
    url = f"{TIKTOK_API_BASE}/report/integrated/get/?{query_string}"

    try:
        req = urllib.request.Request(url, headers={
            "Access-Token": access_token,
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if data.get("code") != 0:
            log.error(f"TikTok API Error: {data.get('message', 'Unknown error')}")
            return None

        rows = data.get("data", {}).get("list", [])
        campaigns = []
        for row in rows:
            camp = _parse_campaign(row)
            campaigns.append(camp)

        if not campaigns:
            log.warning("TikTok: データが0件")
            return None

        totals = _calc_totals(campaigns)

        result = {
            "source": "tiktok_api",
            "advertiser_id": advertiser_id,
            "date_range": {"since": yesterday, "until": today},
            "campaigns": campaigns,
            "totals": totals,
        }

        log.info(f"TikTok API: {len(campaigns)}キャンペーン取得完了")
        return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:500]
        log.error(f"TikTok API Error {e.code}: {error_body}")
        return None
    except Exception as e:
        log.error(f"TikTok API Error: {e}")
        return None


def _parse_campaign(row):
    """APIレスポンスをunified formatに変換"""
    metrics = row.get("metrics", {})
    dimensions = row.get("dimensions", {})

    cost = float(metrics.get("spend", 0))
    conversions = float(metrics.get("conversion", 0))
    purchase_value = float(metrics.get("total_purchase_value", 0))

    camp = {
        "campaign": metrics.get("campaign_name", dimensions.get("campaign_id", "unknown")),
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
    }

    # CPA未算出の場合
    if camp["cpa"] == 0 and conversions > 0:
        camp["cpa"] = round(cost / conversions, 2)

    return camp


def _calc_totals(campaigns):
    """集計値を計算"""
    total_cost = sum(c["cost"] for c in campaigns)
    total_cv = sum(c["conversions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_imps = sum(c["impressions"] for c in campaigns)
    avg_cpa = round(total_cost / total_cv, 2) if total_cv > 0 else 0.0
    avg_ctr = round(total_clicks / total_imps * 100, 2) if total_imps > 0 else 0.0

    return {
        "campaign_count": len(campaigns),
        "total_cost": total_cost,
        "total_conversions": total_cv,
        "total_clicks": total_clicks,
        "total_impressions": total_imps,
        "avg_cpa": avg_cpa,
        "avg_ctr": avg_ctr,
    }
