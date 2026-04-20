"""Meta Ads API Adapter - Meta Marketing APIからデータ取得"""
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

META_API_VERSION = "v21.0"
META_API_BASE = f"https://graph.facebook.com/{META_API_VERSION}"


def fetch_meta_ads(config):
    """Meta広告データを取得してunified formatに変換"""
    account_id = config.get("account_id", "")
    token_env = config.get("access_token_env", "")
    access_token = os.environ.get(token_env, "")

    if not access_token:
        access_token = config.get("access_token", "")
    if not account_id or not access_token:
        log.warning("Meta: account_id or access_token missing")
        return None

    log.info(f"Meta API: アカウント {account_id} からデータ取得中")

    # 日付範囲（昨日）
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # キャンペーンレベルのインサイト取得
    fields = ",".join([
        "campaign_name",
        "impressions",
        "clicks",
        "spend",
        "actions",
        "action_values",
        "ctr",
        "cpm",
        "cpp",
        "frequency",
    ])

    params = urllib.parse.urlencode({
        "fields": fields,
        "time_range": json.dumps({"since": yesterday, "until": today}),
        "level": "campaign",
        "limit": 100,
        "access_token": access_token,
    })

    url = f"{META_API_BASE}/{account_id}/insights?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        campaigns = []
        for row in data.get("data", []):
            camp = _parse_campaign(row)
            campaigns.append(camp)

        if not campaigns:
            log.warning("Meta: データが0件")
            return None

        totals = _calc_totals(campaigns)

        result = {
            "source": "meta_api",
            "account_id": account_id,
            "date_range": {"since": yesterday, "until": today},
            "campaigns": campaigns,
            "totals": totals,
        }

        log.info(f"Meta API: {len(campaigns)}キャンペーン取得完了")
        return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:500]
        log.error(f"Meta API Error {e.code}: {error_body}")
        return None
    except Exception as e:
        log.error(f"Meta API Error: {e}")
        return None


def _parse_campaign(row):
    """APIレスポンスをunified formatに変換"""
    camp = {
        "campaign": row.get("campaign_name", "unknown"),
        "impressions": float(row.get("impressions", 0)),
        "clicks": float(row.get("clicks", 0)),
        "cost": float(row.get("spend", 0)),
        "ctr": float(row.get("ctr", 0)),
        "cpm": float(row.get("cpm", 0)),
        "frequency": float(row.get("frequency", 0)),
        "conversions": 0,
        "cpa": 0,
        "roas": 0,
        "conversion_value": 0,
    }

    # actions からコンバージョン数を抽出
    actions = row.get("actions", [])
    for action in actions:
        if action.get("action_type") in ["purchase", "offsite_conversion.fb_pixel_purchase", "complete_registration", "lead"]:
            camp["conversions"] += float(action.get("value", 0))

    # action_values からコンバージョン値を抽出
    action_values = row.get("action_values", [])
    for av in action_values:
        if av.get("action_type") in ["purchase", "offsite_conversion.fb_pixel_purchase"]:
            camp["conversion_value"] += float(av.get("value", 0))

    # CPA計算
    if camp["conversions"] > 0:
        camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)

    # ROAS計算
    if camp["cost"] > 0 and camp["conversion_value"] > 0:
        camp["roas"] = round(camp["conversion_value"] / camp["cost"], 2)

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
