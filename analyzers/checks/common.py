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

    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        platform = camp.get("platform", "unknown")

        # C01: CTR最低基準
        ctr_min = common_t.get("ctr_min", 1.0)
        if camp.get("impressions", 0) > 100:
            passed = camp.get("ctr", 0) >= ctr_min
            results.append({
                "id": "C01", "passed": passed, "campaign": name, "platform": platform,
                "message": f"CTR {camp.get('ctr', 0):.2f}% (基準: ≥{ctr_min}%)" if not passed else "",
                "severity": "medium"
            })

        # C02: ゼロCV + コスト超過
        cv_zero_cost = common_t.get("cv_zero_cost_min", 5000)
        if camp.get("conversions", 0) == 0 and camp.get("cost", 0) >= cv_zero_cost:
            results.append({
                "id": "C02", "passed": False, "campaign": name, "platform": platform,
                "message": f"ゼロCV: ¥{camp['cost']:,.0f} 消化 (閾値: ¥{cv_zero_cost:,.0f})",
                "severity": "critical"
            })

        # C03: ROAS最低基準
        roas_min = common_t.get("roas_min", 1.0)
        if camp.get("cost", 0) > 0 and camp.get("roas", 0) > 0:
            passed = camp.get("roas", 0) >= roas_min
            if not passed:
                results.append({
                    "id": "C03", "passed": False, "campaign": name, "platform": platform,
                    "message": f"ROAS {camp.get('roas', 0):.1f} (基準: ≥{roas_min})",
                    "severity": "high"
                })

        # C04: フリークエンシー上限
        freq_max = common_t.get("frequency_max", 4.0)
        if camp.get("frequency", 0) > freq_max:
            results.append({
                "id": "C04", "passed": False, "campaign": name, "platform": platform,
                "message": f"フリークエンシー {camp['frequency']:.1f} (上限: {freq_max})",
                "severity": "high"
            })

        # C05: CPA スパイク （全体平均との比較）
        avg_cpa = _calc_avg_cpa(campaigns)
        cpa_spike = common_t.get("cpa_spike_pct", 20)
        if avg_cpa > 0 and camp.get("cpa", 0) > avg_cpa * (1 + cpa_spike / 100):
            results.append({
                "id": "C05", "passed": False, "campaign": name, "platform": platform,
                "message": f"CPA ¥{camp['cpa']:,.0f} が平均 ¥{avg_cpa:,.0f} の{cpa_spike}%超",
                "severity": "high"
            })

        # C06: コスト集中度
        total_cost = sum(c.get("cost", 0) for c in campaigns)
        conc_pct = common_t.get("cost_concentration_pct", 80)
        if total_cost > 0 and camp.get("cost", 0) / total_cost * 100 > conc_pct:
            results.append({
                "id": "C06", "passed": False, "campaign": name, "platform": platform,
                "message": f"コスト集中: {camp['cost'] / total_cost * 100:.1f}% (閾値: {conc_pct}%)",
                "severity": "medium"
            })

        # C07: 学習フェーズ
        weekly_cv_min = common_t.get("weekly_cv_min", 50)
        daily_cv_min = weekly_cv_min / 7
        if camp.get("conversions", 0) < daily_cv_min and camp.get("cost", 0) > 0:
            if not camp.get("learning_phase"):
                camp["learning_phase"] = True
            results.append({
                "id": "C07", "passed": False, "campaign": name, "platform": platform,
                "message": f"学習フェーズ未達: 日次CV {camp['conversions']:.1f} (目標: {daily_cv_min:.1f})",
                "severity": "medium"
            })

    return results


def _calc_avg_cpa(campaigns):
    """全キャンペーンの平均CPAを計算"""
    total_cost = sum(c.get("cost", 0) for c in campaigns if c.get("conversions", 0) > 0)
    total_cv = sum(c.get("conversions", 0) for c in campaigns if c.get("conversions", 0) > 0)
    return round(total_cost / total_cv, 2) if total_cv > 0 else 0
