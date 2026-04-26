"""共通チェック — 全プラットフォーム共通の基本チェック (C01-C15)"""
import logging

log = logging.getLogger("bpo")


def run_common_checks(campaigns, thresholds):
    """共通チェック実行

    Args:
        campaigns: キャンペーンリスト
        thresholds: 閾値設定 (common セクション)
    Returns:
        list[dict]: [{"id": "C01", "passed": bool, "message": str, ...}, ...]
    """
    common_t = thresholds.get("common", {})
    results = []
    avg_cpa = _calc_avg_cpa(campaigns)
    avg_cpm = _calc_avg(campaigns, "cpm")
    total_cost = sum(c.get("cost", 0) for c in campaigns)

    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        platform = camp.get("platform", "unknown")
        imps = camp.get("impressions", 0)
        clicks = camp.get("clicks", 0)
        cost = camp.get("cost", 0)
        cv = camp.get("conversions", 0)
        cpa = camp.get("cpa", 0)
        ctr = camp.get("ctr", 0)
        cpm = camp.get("cpm", 0)
        freq = camp.get("frequency", 0)
        roas = camp.get("roas", 0)
        status = camp.get("status", "ENABLED")
        budget = camp.get("daily_budget", 0)
        revenue = camp.get("revenue", 0) or camp.get("conversion_value", 0)

        # C01: CTR最低基準
        ctr_min = common_t.get("ctr_min", 1.0)
        if imps > 100:
            passed = ctr >= ctr_min
            results.append({
                "id": "C01", "passed": passed, "campaign": name, "platform": platform,
                "message": f"CTR {ctr:.2f}% (基準: ≥{ctr_min}%)" if not passed else "",
                "severity": "medium"
            })

        # C02: ゼロCV + コスト超過
        cv_zero_cost = common_t.get("cv_zero_cost_min", 5000)
        if cost >= cv_zero_cost:
            c02_passed = cv > 0
            results.append({
                "id": "C02", "passed": c02_passed, "campaign": name, "platform": platform,
                "message": f"ゼロCV: ¥{cost:,.0f} 消化 (閾値: ¥{cv_zero_cost:,.0f})" if not c02_passed else "",
                "severity": "critical"
            })

        # C03: ROAS最低基準
        roas_min = common_t.get("roas_min", 1.0)
        if cost > 0 and roas > 0:
            c03_passed = roas >= roas_min
            results.append({
                "id": "C03", "passed": c03_passed, "campaign": name, "platform": platform,
                "message": f"ROAS {roas:.1f} (基準: ≥{roas_min})" if not c03_passed else "",
                "severity": "high"
            })

        # C04: フリークエンシー上限
        freq_max = common_t.get("frequency_max", 4.0)
        if freq > 0:
            c04_passed = freq <= freq_max
            results.append({
                "id": "C04", "passed": c04_passed, "campaign": name, "platform": platform,
                "message": f"フリークエンシー {freq:.1f} (上限: {freq_max})" if not c04_passed else "",
                "severity": "high"
            })

        # C05: CPA スパイク （全体平均との比較）
        cpa_spike = common_t.get("cpa_spike_pct", 20)
        if avg_cpa > 0 and cpa > avg_cpa * (1 + cpa_spike / 100):
            results.append({
                "id": "C05", "passed": False, "campaign": name, "platform": platform,
                "message": f"CPA ¥{cpa:,.0f} が平均 ¥{avg_cpa:,.0f} の{cpa_spike}%超",
                "severity": "high",
                "conflict_group": "cpa_vs_volume",
            })

        # C06: コスト集中度
        conc_pct = common_t.get("cost_concentration_pct", 80)
        if total_cost > 0 and cost / total_cost * 100 > conc_pct:
            results.append({
                "id": "C06", "passed": False, "campaign": name, "platform": platform,
                "message": f"コスト集中: {cost / total_cost * 100:.1f}% (閾値: {conc_pct}%)",
                "severity": "medium"
            })

        # C07: 学習フェーズ
        weekly_cv_min = common_t.get("weekly_cv_min", 50)
        daily_cv_min = weekly_cv_min / 7
        if cv < daily_cv_min and cost > 0:
            if not camp.get("learning_phase"):
                camp["learning_phase"] = True
            results.append({
                "id": "C07", "passed": False, "campaign": name, "platform": platform,
                "message": f"学習フェーズ未達: 日次CV {cv:.1f} (目標: {daily_cv_min:.1f})",
                "severity": "medium",
                "conflict_group": "cpa_vs_volume",
            })

        # C08: CPM スパイク
        cpm_spike_pct = common_t.get("cpm_spike_pct", 50)
        if avg_cpm > 0 and cpm > avg_cpm * (1 + cpm_spike_pct / 100):
            results.append({
                "id": "C08", "passed": False, "campaign": name, "platform": platform,
                "message": f"CPM ¥{cpm:,.0f} が平均 ¥{avg_cpm:,.0f} の{cpm_spike_pct}%超",
                "severity": "medium"
            })

        # C09: インプレッション急減（前日比 — データがあれば）
        prev_imps = camp.get("prev_impressions", 0)
        if prev_imps > 100 and imps > 0:
            drop_pct = (1 - imps / prev_imps) * 100
            if drop_pct > 50:
                results.append({
                    "id": "C09", "passed": False, "campaign": name, "platform": platform,
                    "message": f"インプレッション急減: {drop_pct:.0f}%減 ({prev_imps:,.0f}→{imps:,.0f})",
                    "severity": "high"
                })

        # C10: コスト対効果比（コスト÷CV値 > 1 は赤字）
        if cost > 0 and revenue > 0:
            cost_ratio = cost / revenue
            if cost_ratio > 1.0:
                results.append({
                    "id": "C10", "passed": False, "campaign": name, "platform": platform,
                    "message": f"コスト対効果: 費用¥{cost:,.0f}/収益¥{revenue:,.0f} = {cost_ratio:.2f}（赤字）",
                    "severity": "high"
                })

        # C11: キャンペーン数過多 — アカウント全体で後でチェック
        # (ループ外でやる)

        # C12: 停止キャンペーン放置
        if status == "PAUSED" and cost > 0:
            results.append({
                "id": "C12", "passed": False, "campaign": name, "platform": platform,
                "message": f"停止中なのにコスト ¥{cost:,.0f} が計上されている",
                "severity": "medium"
            })
        elif status == "PAUSED" and cost == 0 and imps == 0:
            # 停止して長期放置 — 低severity
            results.append({
                "id": "C12", "passed": False, "campaign": name, "platform": platform,
                "message": "停止キャンペーン放置中 — 削除またはアーカイブ推奨",
                "severity": "low"
            })

        # C13: 日予算消化率
        if budget > 0 and cost > 0:
            utilization = cost / budget
            if utilization > 0.95:
                results.append({
                    "id": "C13", "passed": False, "campaign": name, "platform": platform,
                    "message": f"日予算制約: 消化率 {utilization * 100:.0f}% — 機会損失の可能性",
                    "severity": "medium"
                })
            elif utilization < 0.3 and cost > 1000:
                results.append({
                    "id": "C13", "passed": False, "campaign": name, "platform": platform,
                    "message": f"日予算未消化: 消化率 {utilization * 100:.0f}% — ターゲティング/入札見直し",
                    "severity": "low"
                })

        # C14: クリック/CV 乖離（CVRが極端に低い）
        cvr_floor = common_t.get("cvr_floor_pct", 0.5)
        if clicks > 50 and cv > 0:
            cvr = cv / clicks * 100
            if cvr < cvr_floor:
                results.append({
                    "id": "C14", "passed": False, "campaign": name, "platform": platform,
                    "message": f"CVR {cvr:.2f}% (基準: ≥{cvr_floor}%) — LP改善またはターゲティング見直し",
                    "severity": "high"
                })

        # C15: ROAS基準（全体平均との比較）
        avg_roas = _calc_avg_roas(campaigns)
        roas_floor_pct = common_t.get("roas_floor_pct", 50)
        if avg_roas > 0 and roas > 0 and roas < avg_roas * (roas_floor_pct / 100):
            results.append({
                "id": "C15", "passed": False, "campaign": name, "platform": platform,
                "message": f"ROAS {roas:.1f} が全体平均 {avg_roas:.1f} の{roas_floor_pct}%未満",
                "severity": "medium"
            })

    # C11: キャンペーン数過多（アカウント全体チェック）
    max_campaigns = common_t.get("max_campaigns", 20)
    active_camps = [c for c in campaigns if c.get("status", "ENABLED") == "ENABLED"]
    if len(active_camps) > max_campaigns:
        results.append({
            "id": "C11", "passed": False, "campaign": "アカウント全体", "platform": "cross",
            "message": f"アクティブキャンペーン {len(active_camps)}個 (推奨上限: {max_campaigns})",
            "severity": "medium"
        })

    return results


def _calc_avg_cpa(campaigns):
    """全キャンペーンの平均CPAを計算"""
    total_cost = sum(c.get("cost", 0) for c in campaigns if c.get("conversions", 0) > 0)
    total_cv = sum(c.get("conversions", 0) for c in campaigns if c.get("conversions", 0) > 0)
    return round(total_cost / total_cv, 2) if total_cv > 0 else 0


def _calc_avg(campaigns, field):
    """全キャンペーンの指定フィールド平均を計算"""
    vals = [c.get(field, 0) for c in campaigns if c.get(field, 0) > 0]
    return round(sum(vals) / len(vals), 2) if vals else 0


def _calc_avg_roas(campaigns):
    """全キャンペーンの平均ROASを計算"""
    total_rev = sum(c.get("revenue", 0) or c.get("conversion_value", 0) for c in campaigns if c.get("cost", 0) > 0)
    total_cost = sum(c.get("cost", 0) for c in campaigns if c.get("cost", 0) > 0)
    return round(total_rev / total_cost, 2) if total_cost > 0 else 0
