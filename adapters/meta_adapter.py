"""Meta Ads API Adapter v2.0 - Meta Marketing API v22.0 + AdSet + Pixel/CAPI"""
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

META_API_VERSION = "v22.0"
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

    lookback_days = config.get("lookback_days", 90)
    log.info(f"Meta API (v22.0): アカウント {account_id} からデータ取得中 (過去{lookback_days}日)")

    # 日付範囲
    since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. キャンペーンレベルのインサイト取得
    campaigns = _fetch_campaign_insights(account_id, access_token, since, today)

    # 2. 広告セットレベルのインサイト取得
    adset_data = _fetch_adset_insights(account_id, access_token, since, today)

    # 3. Pixel / CAPI ステータス取得
    pixel_data = _fetch_pixel_status(account_id, access_token)

    if not campaigns:
        log.warning("Meta: データが0件")
        return None

    # 広告セットデータをキャンペーンに統合
    _merge_adset_data(campaigns, adset_data)

    totals = _calc_totals(campaigns)

    result = {
        "source": "meta_api",
        "account_id": account_id,
        "date_range": {"since": since, "until": today},
        "campaigns": campaigns,
        "totals": totals,
        "pixel_status": pixel_data,
    }

    log.info(f"Meta API: {len(campaigns)}キャンペーン取得完了")
    return result


def _fetch_campaign_insights(account_id, access_token, since, until):
    """キャンペーンレベルインサイト取得"""
    fields = ",".join([
        "campaign_name",
        "campaign_id",
        "objective",
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
        "time_range": json.dumps({"since": since, "until": until}),
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
        return campaigns

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:500]
        log.error(f"Meta Campaign Insights Error {e.code}: {error_body}")
        return []
    except Exception as e:
        log.error(f"Meta Campaign Insights Error: {e}")
        return []


def _fetch_adset_insights(account_id, access_token, since, until):
    """広告セットレベルインサイト取得"""
    fields = ",".join([
        "adset_name",
        "adset_id",
        "campaign_id",
        "impressions",
        "clicks",
        "spend",
        "actions",
        "frequency",
        "optimization_goal",
    ])

    params = urllib.parse.urlencode({
        "fields": fields,
        "time_range": json.dumps({"since": since, "until": until}),
        "level": "adset",
        "limit": 200,
        "access_token": access_token,
    })

    url = f"{META_API_BASE}/{account_id}/insights?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        # キャンペーンID別に広告セットを集約
        adsets_by_campaign = {}
        for row in data.get("data", []):
            camp_id = row.get("campaign_id", "")
            if camp_id not in adsets_by_campaign:
                adsets_by_campaign[camp_id] = []
            adsets_by_campaign[camp_id].append({
                "name": row.get("adset_name", ""),
                "id": row.get("adset_id", ""),
                "impressions": float(row.get("impressions", 0)),
                "spend": float(row.get("spend", 0)),
                "frequency": float(row.get("frequency", 0)),
                "optimization_goal": row.get("optimization_goal", ""),
            })
        return adsets_by_campaign

    except Exception as e:
        log.warning(f"Meta AdSet Insights Error: {e}")
        return {}


def _fetch_pixel_status(account_id, access_token):
    """Pixel / CAPI ステータス取得"""
    # アカウントに接続されたPixelを取得
    params = urllib.parse.urlencode({
        "fields": "name,id,is_unavailable,data_use_setting,automatic_matching_fields",
        "access_token": access_token,
    })

    url = f"{META_API_BASE}/{account_id}/adspixels?{params}"

    pixel_data = {
        "pixel_installed": False,
        "capi_enabled": False,
        "event_match_quality": None,
        "server_events": False,
        "domain_verified": False,
    }

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        pixels = data.get("data", [])
        if pixels:
            pixel_data["pixel_installed"] = True
            pixel_id = pixels[0].get("id", "")

            # Pixel の詳細ステータスを取得
            if pixel_id:
                detail_params = urllib.parse.urlencode({
                    "fields": "name,last_fired_time,is_unified_entry,automatic_matching_fields",
                    "access_token": access_token,
                })
                detail_url = f"{META_API_BASE}/{pixel_id}?{detail_params}"
                try:
                    req2 = urllib.request.Request(detail_url)
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        detail = json.loads(resp2.read())
                        if detail.get("last_fired_time"):
                            pixel_data["server_events"] = True
                except Exception:
                    pass

                # Event Match Quality 取得
                emq_params = urllib.parse.urlencode({
                    "fields": "event_name,event_match_quality",
                    "access_token": access_token,
                })
                emq_url = f"{META_API_BASE}/{pixel_id}/stats?{emq_params}"
                try:
                    req3 = urllib.request.Request(emq_url)
                    with urllib.request.urlopen(req3, timeout=15) as resp3:
                        emq_data = json.loads(resp3.read())
                        stats = emq_data.get("data", [])
                        if stats:
                            emq_scores = [s.get("event_match_quality", 0) for s in stats if s.get("event_match_quality")]
                            if emq_scores:
                                pixel_data["event_match_quality"] = round(sum(emq_scores) / len(emq_scores), 1)
                                pixel_data["capi_enabled"] = any(s > 0 for s in emq_scores)
                except Exception:
                    pass

    except Exception as e:
        log.warning(f"Meta Pixel Status Error: {e}")

    return pixel_data


def _parse_campaign(row):
    """APIレスポンスをunified formatに変換"""
    camp = {
        "campaign": row.get("campaign_name", "unknown"),
        "campaign_id": row.get("campaign_id", ""),
        "platform": "meta",
        "campaign_type": _map_objective(row.get("objective", "")),
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
        "revenue": 0,
        # 拡張フィールド
        "status": "ENABLED",
        "bidding_strategy": "auto",
        "daily_budget": 0,
        "ad_count": 0,
        "adset_count": 0,
        "learning_phase": False,
    }

    # ADR-004 (Day 5.3): action_type 正規化を config/conversion_mapping.yaml に外部化。
    # synonym 集約 → canonical 単位で集計し、Meta API の重複報告（同一購入を 9 種類超の
    # ラベルで報告する仕様）による二重計上を回避する。
    # 旧ハードコード定数 UNIFIED_CV_TYPES / UNIFIED_REVENUE_TYPES は廃止。
    from engine.conversion_mapping import aggregate_actions, load_conversion_mapping

    cm = load_conversion_mapping()
    cv_aggregated = aggregate_actions("meta", "conversion", row.get("actions", []), mapping=cm)
    rev_aggregated = aggregate_actions("meta", "revenue", row.get("action_values", []), mapping=cm)

    # canonical 単位の集計値を camp の集約フィールドへ反映
    # CV 数は「全 canonical の合計」（purchase + lead + complete_registration 等を独立 CV として加算）
    camp["conversions"] += sum(cv_aggregated.values())
    # Revenue は purchase の value のみ（複数の収益タイプは現状想定外）
    purchase_value = rev_aggregated.get("purchase", 0.0)
    camp["conversion_value"] += purchase_value
    camp["revenue"] += purchase_value

    # CPA計算
    if camp["conversions"] > 0:
        camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)

    # ROAS計算
    if camp["cost"] > 0 and camp["conversion_value"] > 0:
        camp["roas"] = round(camp["conversion_value"] / camp["cost"], 2)

    return camp


def _map_objective(objective):
    """Meta キャンペーン目的を内部タイプに変換"""
    mapping = {
        "OUTCOME_SALES": "conversions",
        "OUTCOME_LEADS": "lead_gen",
        "OUTCOME_ENGAGEMENT": "engagement",
        "OUTCOME_AWARENESS": "awareness",
        "OUTCOME_TRAFFIC": "traffic",
        "OUTCOME_APP_PROMOTION": "app_install",
        "CONVERSIONS": "conversions",
        "LEAD_GENERATION": "lead_gen",
        "BRAND_AWARENESS": "awareness",
        "REACH": "reach",
        "TRAFFIC": "traffic",
        "LINK_CLICKS": "traffic",
    }
    return mapping.get(objective, "other")


def _merge_adset_data(campaigns, adsets_by_campaign):
    """広告セットデータをキャンペーンに統合"""
    for camp in campaigns:
        camp_id = camp.get("campaign_id", "")
        adsets = adsets_by_campaign.get(camp_id, [])
        camp["adset_count"] = len(adsets)
        # 50CV/週 の学習フェーズ判定: 日次CV < 7.14 ≈ 週50
        if camp["conversions"] < 7.14:
            camp["learning_phase"] = True


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
