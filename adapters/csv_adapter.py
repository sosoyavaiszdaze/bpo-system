import csv
import os

COLUMN_MAP = {
    "campaign": ["campaign", "campaign_name", "キャンペーン", "キャンペーン名"],
    "clicks": ["clicks", "クリック数", "クリック"],
    "impressions": ["impressions", "imps", "表示回数", "インプレッション"],
    "cost": ["cost", "spend", "費用", "コスト", "利用金額"],
    "conversions": ["conversions", "conv", "cv", "コンバージョン"],
    "cpa": ["cpa", "cost_per_conversion", "顧客獲得単価"],
    "roas": ["roas", "return_on_ad_spend"],
    "ctr": ["ctr", "click_through_rate", "クリック率"],
    "cpm": ["cpm", "cost_per_mille"],
    "frequency": ["frequency", "フリークエンシー"],
}

def _num(val, default=0.0):
    if val is None or val == "":
        return default
    try:
        cleaned = str(val).replace(",", "").replace("¥", "").replace("%", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default

def _find_col(header, target_key):
    candidates = COLUMN_MAP.get(target_key, [])
    for h in header:
        if h.lower().strip() in candidates:
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
            camp["clicks"] = _num(camp.get("clicks"))
            camp["impressions"] = _num(camp.get("impressions"))
            camp["cost"] = _num(camp.get("cost"))
            camp["conversions"] = _num(camp.get("conversions"))
            camp["roas"] = _num(camp.get("roas"))
            camp["cpm"] = _num(camp.get("cpm"))
            camp["frequency"] = _num(camp.get("frequency"))
            if _num(camp.get("cpa")) == 0 and camp["conversions"] > 0:
                camp["cpa"] = round(camp["cost"] / camp["conversions"], 2)
            else:
                camp["cpa"] = _num(camp.get("cpa"))
            if _num(camp.get("ctr")) == 0 and camp["impressions"] > 0:
                camp["ctr"] = round(camp["clicks"] / camp["impressions"] * 100, 2)
            else:
                camp["ctr"] = _num(camp.get("ctr"))
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
