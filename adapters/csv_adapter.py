"""CSV Adapter v3.0 - Google Ads エクスポート形式 + 拡張 unified format 対応"""
import csv
import os

COLUMN_MAP = {
    "platform": ["platform", "媒体", "プラットフォーム"],
    "campaign_type": ["campaign_type", "type", "キャンペーンタイプ", "種別", "campaign type"],
    "campaign": ["campaign", "campaign_name", "キャンペーン", "キャンペーン名", "Campaign"],
    "status": ["status", "ステータス", "Campaign state", "キャンペーンの状態"],
    "bidding_strategy": [
        "bidding_strategy", "bid_strategy", "入札戦略",
        "Bid strategy type", "入札戦略タイプ",
    ],
    "daily_budget": [
        "daily_budget", "budget", "日予算", "1日の予算",
        "Budget", "Campaign daily budget",
    ],
    "clicks": ["clicks", "クリック数", "クリック", "Clicks"],
    "impressions": ["impressions", "imps", "表示回数", "インプレッション", "Impr.", "Impressions"],
    "cost": ["cost", "spend", "費用", "コスト", "利用金額", "Cost"],
    "conversions": [
        "conversions", "conv", "cv", "コンバージョン",
        "Conv.", "Conversions", "すべてのコンバージョン",
    ],
    "cpa": [
        "cpa", "cost_per_conversion", "顧客獲得単価",
        "Cost / conv.", "費用/コンバージョン",
    ],
    "roas": ["roas", "return_on_ad_spend", "Conv. value / cost", "コンバージョン値/費用"],
    "ctr": ["ctr", "click_through_rate", "クリック率", "CTR"],
    "cpm": ["cpm", "cost_per_mille", "CPM"],
    "frequency": ["frequency", "フリークエンシー"],
    "revenue": [
        "revenue", "conv_value", "売上", "収益", "コンバージョン値",
        "Conv. value", "Total conv. value", "すべてのコンバージョン値",
    ],
}


def _num(val, default=0.0):
    if val is None or val == "":
        return default
    try:
        cleaned = str(val).replace(",", "").replace("¥", "").replace("$", "").replace("%", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default


def _str(val, default=""):
    if val is None:
        return default
    return str(val).strip()


def _find_col(header, target_key):
    candidates = COLUMN_MAP.get(target_key, [])
    for h in header:
        if h.lower().strip() in [c.lower() for c in candidates]:
            return h
    return None


def load_csv(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"CSV not found: {filepath}")
    campaigns = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        col_map = {}
        for key in COLUMN_MAP:
            found = _find_col(header, key)
            if found:
                col_map[key] = found
        for row in reader:
            camp = {}
            for key, col_name in col_map.items():
                camp[key] = row.get(col_name, "")
            if not camp.get("campaign", "").strip():
                continue
            # 文字列フィールド
            camp["platform"] = _str(camp.get("platform"), "unknown").lower()
            camp["campaign_type"] = _str(camp.get("campaign_type"), "unknown").lower()
            camp["campaign"] = _str(camp.get("campaign"))
            camp["status"] = _str(camp.get("status"), "ENABLED").upper()
            camp["bidding_strategy"] = _str(camp.get("bidding_strategy"), "unknown").lower()
            # 数値フィールド
            camp["clicks"] = _num(camp.get("clicks"))
            camp["impressions"] = _num(camp.get("impressions"))
            camp["cost"] = _num(camp.get("cost"))
            camp["conversions"] = _num(camp.get("conversions"))
            camp["roas"] = _num(camp.get("roas"))
            camp["cpm"] = _num(camp.get("cpm"))
            camp["frequency"] = _num(camp.get("frequency"))
            camp["revenue"] = _num(camp.get("revenue"))
            camp["daily_budget"] = _num(camp.get("daily_budget"))
            # CPA 自動計算
            if _num(camp.get("cpa")) == 0 and camp["conversions"] > 0:
                camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)
            else:
                camp["cpa"] = _num(camp.get("cpa"))
            # CTR 自動計算
            if _num(camp.get("ctr")) == 0 and camp["impressions"] > 0:
                camp["ctr"] = round(camp["clicks"] / camp["impressions"] * 100, 2)
            else:
                camp["ctr"] = _num(camp.get("ctr"))
            # revenue 自動計算（ROAS から逆算）
            if camp["revenue"] == 0 and camp["roas"] > 0 and camp["cost"] > 0:
                camp["revenue"] = round(camp["cost"] * camp["roas"])
            # 拡張フィールドのデフォルト値
            camp.setdefault("conversion_value", camp["revenue"])
            camp.setdefault("ad_count", 0)
            camp.setdefault("keyword_count", 0)
            camp.setdefault("match_types", [])
            camp.setdefault("negative_keyword_lists", [])
            camp.setdefault("quality_score_avg", None)
            camp.setdefault("enhanced_conversions", False)
            camp.setdefault("asset_count", 0)
            camp.setdefault("learning_phase", False)
            campaigns.append(camp)
    totals = _calc_totals(campaigns)
    return {
        "source": "csv",
        "file": os.path.basename(filepath),
        "campaigns": campaigns,
        "totals": totals,
    }


def _calc_totals(campaigns):
    total_cost = sum(c["cost"] for c in campaigns)
    total_cv = sum(c["conversions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_imps = sum(c["impressions"] for c in campaigns)
    total_revenue = sum(c["revenue"] for c in campaigns)
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
