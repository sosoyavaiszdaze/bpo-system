"""v3 ベンチマーク3軸比較モジュール（業界平均 / Zynect推奨水準 / 現状値）。

設計: docs/report_design/v3_structure.md（ページ4-6 媒体別詳細 / ページ2 サマリ）
入力ソース: config/benchmarks.yaml

使い方:
    from engine.benchmark_compare import load_benchmarks, compare_3axis, build_chart_data

    bm = load_benchmarks()
    cmp = compare_3axis("ec_retail", "google_ads", "ctr", current=2.22, bm=bm)
    chart = build_chart_data(cmp)  # 円形チャート用パーセンタイル位置
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("bpo")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
BENCHMARKS_PATH = CONFIG_DIR / "benchmarks.yaml"

# 「大きいほど良い」メトリクス（CTR/CVR/ROAS）と「小さいほど良い」メトリクス（CPA/CPC/Frequency）の判定
HIGHER_IS_BETTER = {"ctr", "cvr", "roas"}
LOWER_IS_BETTER = {"cpa", "cpc", "cpm", "frequency"}


def load_benchmarks(path: Path | None = None) -> dict[str, Any]:
    target = path or BENCHMARKS_PATH
    with open(target, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_benchmark_cell(bm: dict, industry: str, platform: str, metric: str) -> dict | None:
    """benchmarks.yaml から industry × platform × metric のセルを取り出す。

    Returns:
        cell dict { industry_avg, zynect_recommended, unit, source, ... } or None.
    """
    benches = bm.get("benchmarks", {})
    ind_data = benches.get(industry)
    if not ind_data:
        return None
    pf_data = ind_data.get(platform)
    if not pf_data:
        return None
    cell = pf_data.get(metric)
    if not isinstance(cell, dict):
        return None
    return cell


def compare_3axis(
    industry: str,
    platform: str,
    metric: str,
    current: float | None,
    bm: dict | None = None,
) -> dict[str, Any]:
    """現状値・業界平均・Zynect推奨水準の3軸比較データを構築する。

    Args:
        industry: 業界キー（ec_retail / saas_b2b 等）
        platform: 媒体キー（google_ads / meta_ads / tiktok_ads）
        metric: メトリクスキー（ctr / cpa / roas 等）
        current: 現状値（None の場合は「測定なし」扱い）
        bm: benchmarks.yaml の dict（省略時はファイルからロード）

    Returns:
        dict {
          metric, current, industry_avg, zynect_recommended, unit, source,
          status: "above_zynect" | "above_industry" | "below_industry" | "no_benchmark",
          gap_to_industry, gap_to_zynect,
          higher_is_better, has_data, note
        }
    """
    if bm is None:
        bm = load_benchmarks()
    cell = get_benchmark_cell(bm, industry, platform, metric)

    out: dict[str, Any] = {
        "metric": metric,
        "current": current,
        "industry_avg": None,
        "zynect_recommended": None,
        "unit": None,
        "source": None,
        "status": "no_benchmark",
        "gap_to_industry": None,
        "gap_to_zynect": None,
        "higher_is_better": metric in HIGHER_IS_BETTER,
        "has_data": False,
        "note": None,
    }

    if not cell:
        out["note"] = "業界平均データ未収集"
        return out

    out["industry_avg"] = cell.get("industry_avg")
    out["zynect_recommended"] = cell.get("zynect_recommended")
    out["unit"] = cell.get("unit")
    out["source"] = cell.get("source")
    if cell.get("note"):
        out["note"] = cell.get("note")

    if current is None:
        return out

    if out["industry_avg"] is None and out["zynect_recommended"] is None:
        out["note"] = "業界平均データ未収集"
        return out

    out["has_data"] = True
    higher = out["higher_is_better"]

    # status 判定
    zr = out["zynect_recommended"]
    ia = out["industry_avg"]
    if higher:
        if zr is not None and current >= zr:
            out["status"] = "above_zynect"
        elif ia is not None and current >= ia:
            out["status"] = "above_industry"
        else:
            out["status"] = "below_industry"
    else:
        if zr is not None and current <= zr:
            out["status"] = "above_zynect"  # 下限値達成
        elif ia is not None and current <= ia:
            out["status"] = "above_industry"
        else:
            out["status"] = "below_industry"

    if ia is not None:
        out["gap_to_industry"] = round(current - ia, 4)
    if zr is not None:
        out["gap_to_zynect"] = round(current - zr, 4)

    return out


def build_chart_data(cmp: dict[str, Any]) -> dict[str, Any]:
    """円形チャート（SVG ストロークベース）用のパーセンタイル位置を計算する。

    パーセンタイル位置の解釈:
        - 0%: 業界平均より大幅に下（higher_is_betterの場合）
        - 50%: 業界平均
        - 80%: Zynect 推奨水準
        - 100%: Zynect 推奨水準を大幅に超える

    Returns:
        dict {
          current_pct, industry_avg_pct, zynect_pct, status_color,
          arc_dasharray, arc_strokewidth_class
        }
    """
    if not cmp.get("has_data"):
        return {
            "current_pct": None,
            "industry_avg_pct": 50,
            "zynect_pct": 80,
            "status_color": "#aaa",
            "available": False,
        }

    current = cmp["current"]
    ia = cmp["industry_avg"]
    zr = cmp["zynect_recommended"]
    higher = cmp["higher_is_better"]

    # 基準スケール: 0 = ia の半分（または 0）, 50 = ia, 80 = zr, 100 = zr * 1.25
    if ia is None and zr is not None:
        ia = zr * 0.7  # 業界平均が無い場合は zr の 70% と仮定
    if ia is None:
        ia = current  # 完全 fallback

    if zr is None:
        zr = ia * 1.3 if higher else ia * 0.7

    if higher:
        # higher_is_better: scale = current / zr * 80 を中心に補正
        if current >= zr:
            pct = min(100, 80 + (current - zr) / max(zr * 0.25, 1e-9) * 20)
        elif current >= ia:
            pct = 50 + (current - ia) / max(zr - ia, 1e-9) * 30
        else:
            pct = max(0, current / max(ia, 1e-9) * 50)
    else:
        # lower_is_better: 反転スケール
        if current <= zr:
            # zr 以下なら 80-100 帯。値が小さいほど 100 に近づく
            pct = min(100, 80 + (zr - current) / max(zr, 1e-9) * 20)
        elif current <= ia:
            pct = 50 + (ia - current) / max(ia - zr, 1e-9) * 30
        else:
            pct = max(0, 50 - (current - ia) / max(ia, 1e-9) * 50)

    status = cmp.get("status")
    color_map = {
        "above_zynect": "#27500A",
        "above_industry": "#854F0B",
        "below_industry": "#A32D2D",
        "no_benchmark": "#aaa",
    }

    return {
        "current_pct": round(pct, 1),
        "industry_avg_pct": 50,
        "zynect_pct": 80,
        "status_color": color_map.get(status, "#aaa"),
        "available": True,
    }


def build_metric_label(metric: str) -> str:
    """メトリクスキー → 顧客向け表示ラベル"""
    return {
        "ctr": "CTR（クリック率）",
        "cpc": "CPC（1クリック単価）",
        "cpa": "CPA（顧客獲得単価）",
        "cpm": "CPM（広告表示1,000回あたりの費用）",
        "cvr": "CVR（コンバージョン率）",
        "roas": "ROAS（広告費用対効果）",
        "frequency": "フリークエンシー（同一ユーザーへの広告表示回数）",
    }.get(metric, metric)


def build_health_score_3axis(industry: str, current_score: int, bm: dict | None = None) -> dict[str, Any]:
    """Health Score の3軸比較（業界平均 / Zynect推奨 / 現状）。

    health_score_benchmarks セクションを参照する（業界平均は null の場合多し）。
    """
    if bm is None:
        bm = load_benchmarks()
    hsb = bm.get("health_score_benchmarks", {}).get(industry, {})
    ia = hsb.get("industry_avg")
    zr = hsb.get("zynect_recommended", 80)

    return {
        "current": current_score,
        "industry_avg": ia,
        "zynect_recommended": zr,
        "industry_avg_display": str(ia) if ia is not None else "業界平均データ未収集",
        "zynect_display": str(zr),
        "current_display": str(current_score),
        "current_pct": current_score,
        "industry_avg_pct": ia if ia is not None else 65,  # フォールバック描画位置
        "zynect_pct": zr,
        "has_industry_data": ia is not None,
        "source": hsb.get("source", ""),
    }
