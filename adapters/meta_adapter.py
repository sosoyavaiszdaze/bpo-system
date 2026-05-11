"""Meta Ads API Adapter v2.0 - Meta Marketing API v22.0 + AdSet + Pixel/CAPI"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

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

    # 3. Ad / Placement レベルの切り分けデータ取得
    ad_data = _fetch_ad_insights(account_id, access_token, since, today)
    placement_data = _fetch_placement_insights(account_id, access_token, since, today)

    # 4. Pixel / CAPI / domain / account health ステータス取得
    pixel_data = _fetch_pixel_status(account_id, access_token)
    account_status = _fetch_account_status(account_id, access_token)
    domain_verification = _fetch_domain_verification(config, access_token)
    capi_dedup = _fetch_capi_dedup_status(config)

    if not campaigns:
        log.warning("Meta: キャンペーンデータは0件。接続監査と設定系API結果のみ返します")

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
        "ad_insights": ad_data,
        "placement_insights": placement_data,
        "account_status": account_status,
        "domain_verification": domain_verification,
        "capi_dedup": capi_dedup,
        "performance_diagnostics": _build_performance_diagnostics(
            campaigns, adset_data, ad_data, placement_data,
        ),
    }
    try:
        from engine.meta_rule_evidence import (
            build_meta_connection_audit,
            build_meta_rule_evidence,
            build_rule_group_index,
        )
        result["meta_connection_audit"] = build_meta_connection_audit(config, result)
        result["meta_rule_evidence"] = build_meta_rule_evidence(result)
        result["meta_rule_groups"] = build_rule_group_index()
    except Exception as e:
        log.warning(f"Meta rule evidence build failed: {e}")

    log.info(f"Meta API: {len(campaigns)}キャンペーン取得完了")
    return result


def _api_get(path, access_token, params=None, timeout=30):
    """Small Graph API GET wrapper used by optional Meta diagnostics."""
    params = dict(params or {})
    params["access_token"] = access_token
    url = f"{META_API_BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


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


def _fetch_ad_insights(account_id, access_token, since, until):
    """広告レベルのCPA/CV悪化切り分け用 insights."""
    fields = ",".join([
        "ad_name", "ad_id", "adset_id", "campaign_id",
        "impressions", "clicks", "spend", "actions", "action_values",
        "ctr", "cpm", "frequency",
    ])
    params = {
        "fields": fields,
        "time_range": json.dumps({"since": since, "until": until}),
        "level": "ad",
        "limit": 500,
    }
    try:
        data = _api_get(f"{account_id}/insights", access_token, params=params, timeout=30)
        return [_parse_ad(row) for row in data.get("data", [])]
    except Exception as e:
        log.warning(f"Meta Ad Insights Error: {e}")
        return []


def _fetch_placement_insights(account_id, access_token, since, until):
    """Placement breakdown for Audience Network / feed / story diagnosis."""
    fields = ",".join([
        "campaign_id", "campaign_name", "adset_id",
        "impressions", "clicks", "spend", "actions", "action_values",
        "ctr", "cpm", "frequency",
    ])
    params = {
        "fields": fields,
        "time_range": json.dumps({"since": since, "until": until}),
        "level": "adset",
        "breakdowns": "publisher_platform,platform_position",
        "limit": 500,
    }
    try:
        data = _api_get(f"{account_id}/insights", access_token, params=params, timeout=30)
        rows = []
        for row in data.get("data", []):
            parsed = _parse_metric_row(row)
            parsed.update({
                "campaign_id": row.get("campaign_id", ""),
                "campaign": row.get("campaign_name", ""),
                "adset_id": row.get("adset_id", ""),
                "publisher_platform": row.get("publisher_platform", ""),
                "platform_position": row.get("platform_position", ""),
            })
            rows.append(parsed)
        return rows
    except Exception as e:
        log.warning(f"Meta Placement Insights Error: {e}")
        return []


def _fetch_account_status(account_id, access_token):
    """Ad account status / restrictions hints.

    This is not a full Account Quality replacement, but it gives operators the
    official account_status / disable_reason fields when available.
    """
    fields = ",".join([
        "account_id", "name", "account_status", "disable_reason",
        "currency", "timezone_name", "business",
    ])
    try:
        data = _api_get(account_id, access_token, params={"fields": fields}, timeout=15)
        return {
            "status": "ok",
            "account_id": data.get("account_id"),
            "name": data.get("name"),
            "account_status": data.get("account_status"),
            "disable_reason": data.get("disable_reason"),
            "currency": data.get("currency"),
            "timezone_name": data.get("timezone_name"),
            "business": data.get("business"),
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        log.warning(f"Meta Account Status Error {e.code}: {body}")
        return {"status": "error", "error": body, "http_status": e.code}
    except Exception as e:
        log.warning(f"Meta Account Status Error: {e}")
        return {"status": "unknown", "error": str(e)}


def _fetch_domain_verification(config, access_token):
    """Fetch Business owned domains when business_id is configured."""
    business_id = config.get("business_id") or config.get("meta_business_id")
    expected_domains = config.get("domains") or config.get("verified_domains") or []
    if not business_id:
        return {
            "status": "missing_config",
            "business_id": None,
            "expected_domains": expected_domains,
            "domains": [],
            "reason": "business_id 未設定のため domain verification API 未実行",
        }
    fields = "domain,verification_status"
    try:
        data = _api_get(f"{business_id}/owned_domains", access_token, params={"fields": fields, "limit": 100}, timeout=15)
        domains = data.get("data", [])
        verified = [
            d for d in domains
            if str(d.get("verification_status", "")).lower() in {"verified", "confirmed"}
        ]
        return {
            "status": "ok" if verified else "manual_required",
            "business_id": business_id,
            "expected_domains": expected_domains,
            "domains": domains,
            "verified_count": len(verified),
        }
    except Exception as e:
        log.warning(f"Meta Domain Verification Error: {e}")
        return {
            "status": "unknown",
            "business_id": business_id,
            "expected_domains": expected_domains,
            "domains": [],
            "error": str(e),
        }


def _fetch_capi_dedup_status(config):
    """Inspect optional CAPI/Payload logs for event_id dedup readiness.

    Expected JSON/JSONL shape is deliberately loose:
      {"event_id": "...", "source": "browser"|"server", "event_name": "Purchase"}
    or {"dedup_key": "...", "channel": "..."}.
    """
    path_value = config.get("capi_event_log_path") or config.get("capi_dedup_log_path")
    if not path_value:
        return {
            "status": "missing_log",
            "reason": "capi_event_log_path 未設定のため dedup_key はログ確認待ち",
            "matched_event_ids": 0,
        }
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    if not path.exists():
        return {"status": "missing_log", "path": str(path), "matched_event_ids": 0}

    browser_ids, server_ids, total = set(), set(), 0
    try:
        for rec in _iter_json_records(path):
            total += 1
            event_id = rec.get("event_id") or rec.get("dedup_key")
            if not event_id:
                continue
            source = str(rec.get("source") or rec.get("channel") or rec.get("event_source") or "").lower()
            if source in {"browser", "pixel", "web"}:
                browser_ids.add(str(event_id))
            elif source in {"server", "capi", "conversions_api"}:
                server_ids.add(str(event_id))
        matched = browser_ids & server_ids
        status = "ok" if matched else ("manual_required" if browser_ids or server_ids else "unknown")
        return {
            "status": status,
            "path": str(path),
            "records": total,
            "browser_event_ids": len(browser_ids),
            "server_event_ids": len(server_ids),
            "matched_event_ids": len(matched),
        }
    except Exception as e:
        return {"status": "error", "path": str(path), "error": str(e), "matched_event_ids": 0}


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
        "pixel_count": 0,
        "pixels": [],
        "primary_pixel_id": None,
        "last_fired_time": None,
        "capi_enabled": False,
        "event_match_quality": None,
        "server_events": False,
        # 未接続の確認項目を False にすると「未完了」と誤検知する。
        # Business/domain verification API を接続するまでは unknown(None) として扱う。
        "domain_verified": None,
    }

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        pixels = data.get("data", [])
        pixel_data["pixel_count"] = len(pixels)
        pixel_data["pixels"] = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "is_unavailable": p.get("is_unavailable"),
                "automatic_matching_fields": p.get("automatic_matching_fields"),
            }
            for p in pixels
        ]
        if pixels:
            pixel_data["pixel_installed"] = True
            pixel_id = pixels[0].get("id", "")
            pixel_data["primary_pixel_id"] = pixel_id

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
                            pixel_data["last_fired_time"] = detail.get("last_fired_time")
                            pixel_data["server_events"] = True
                        pixel_data["primary_pixel_name"] = detail.get("name")
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


def _parse_ad(row):
    parsed = _parse_metric_row(row)
    parsed.update({
        "ad": row.get("ad_name", "unknown"),
        "ad_id": row.get("ad_id", ""),
        "adset_id": row.get("adset_id", ""),
        "campaign_id": row.get("campaign_id", ""),
        "platform": "meta",
    })
    return parsed


def _parse_metric_row(row):
    from engine.conversion_mapping import aggregate_actions, load_conversion_mapping

    cm = load_conversion_mapping()
    cv_aggregated = aggregate_actions("meta", "conversion", row.get("actions", []), mapping=cm)
    rev_aggregated = aggregate_actions("meta", "revenue", row.get("action_values", []), mapping=cm)
    conversions = sum(cv_aggregated.values())
    revenue = rev_aggregated.get("purchase", 0.0)
    cost = float(row.get("spend", 0) or 0)
    impressions = float(row.get("impressions", 0) or 0)
    clicks = float(row.get("clicks", 0) or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost": cost,
        "ctr": float(row.get("ctr", 0) or 0),
        "cpm": float(row.get("cpm", 0) or 0),
        "frequency": float(row.get("frequency", 0) or 0),
        "conversions": conversions,
        "conversion_value": revenue,
        "revenue": revenue,
        "cpa": round(cost / conversions, 2) if conversions else 0,
        "roas": round(revenue / cost, 2) if cost and revenue else 0,
    }


def _build_performance_diagnostics(campaigns, adsets_by_campaign, ads, placements):
    """Rank where CPA is high or CV is weak without making stop/go decisions."""
    return {
        "campaigns": _rank_segments(campaigns, "campaign", "campaign_id"),
        "adsets": _rank_segments(
            [a | {"campaign_id": cid} for cid, rows in (adsets_by_campaign or {}).items() for a in rows],
            "name",
            "id",
        ),
        "ads": _rank_segments(ads, "ad", "ad_id"),
        "placements": _rank_segments(placements, "platform_position", "adset_id"),
    }


def _rank_segments(rows, name_key, id_key, limit=10):
    ranked = []
    for row in rows or []:
        cost = float(row.get("cost") or row.get("spend") or 0)
        cv = float(row.get("conversions") or 0)
        impressions = float(row.get("impressions") or 0)
        cpa = float(row.get("cpa") or (cost / cv if cv else 0) or 0)
        if cost <= 0 and impressions <= 0:
            continue
        risk_score = (cpa if cv else cost * 2) + max(0, 1000 - impressions) / 100
        ranked.append({
            "name": row.get(name_key) or row.get("campaign") or row.get("publisher_platform") or "unknown",
            "id": row.get(id_key) or "",
            "campaign_id": row.get("campaign_id", ""),
            "cost": round(cost),
            "conversions": cv,
            "cpa": round(cpa, 2) if cpa else None,
            "roas": row.get("roas"),
            "impressions": round(impressions),
            "frequency": row.get("frequency"),
            "publisher_platform": row.get("publisher_platform"),
            "platform_position": row.get("platform_position"),
            "risk_score": round(risk_score, 2),
            "diagnosis_hint": _diagnosis_hint(cost, cv, cpa),
        })
    ranked.sort(key=lambda r: r["risk_score"], reverse=True)
    return ranked[:limit]


def _diagnosis_hint(cost, conversions, cpa):
    if cost > 0 and conversions == 0:
        return "spend_without_cv"
    if cpa and cpa > 0:
        return "high_cpa_segment"
    return "monitor"


def _iter_json_records(path):
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return
    if stripped.startswith("["):
        for rec in json.loads(stripped):
            if isinstance(rec, dict):
                yield rec
        return
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if isinstance(rec, dict):
            yield rec


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
