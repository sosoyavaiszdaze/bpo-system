"""広告監査 v2.0 - Google / Meta / TikTok 3媒体対応 25チェック"""
import logging

log = logging.getLogger("bpo")


def run_audit(client_id, data, thresholds):
    campaigns = data.get("campaigns", [])
    totals = data.get("totals", {})

    if not campaigns:
        return {"score": 0, "grade": "F", "error": "No campaign data"}

    issues = []
    quick_wins = []
    common = thresholds.get("common", {})
    google_cfg = thresholds.get("google", {})
    meta_cfg = thresholds.get("meta", {})
    tiktok_cfg = thresholds.get("tiktok", {})

    avg_cpa = totals.get("avg_cpa", 0)
    avg_ctr = totals.get("avg_ctr", 0)
    total_cost = totals.get("total_cost", 0)

    by_platform = {"google": [], "meta": [], "tiktok": [], "unknown": []}
    for camp in campaigns:
        p = camp.get("platform", "unknown").lower()
        by_platform.setdefault(p, []).append(camp)

    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        platform = camp.get("platform", "unknown").lower()
        ctype = camp.get("campaign_type", "unknown").lower()
        cpa = camp.get("cpa", 0)
        ctr = camp.get("ctr", 0)
        cost = camp.get("cost", 0)
        cv = camp.get("conversions", 0)
        roas = camp.get("roas", 0)
        imps = camp.get("impressions", 0)
        freq = camp.get("frequency", 0)
        revenue = camp.get("revenue", 0)

        # === 共通チェック ===
        if cv == 0 and cost > common.get("cv_zero_cost_min", 5000):
            issues.append({"check_id": "C01", "severity": "critical", "platform": platform, "campaign": name,
                "issue": f"CV 0件でコスト ¥{cost:,.0f} 発生", "action": "即時停止または大幅な改善が必要"})

        if revenue > 0 and roas < common.get("roas_min", 1.0):
            issues.append({"check_id": "C02", "severity": "critical", "platform": platform, "campaign": name,
                "issue": f"ROAS {roas:.2f} で赤字運用", "action": "予算縮小・ターゲット変更・LP改善"})

        if avg_cpa > 0 and cpa > avg_cpa * 3 and cv > 0:
            issues.append({"check_id": "C03", "severity": "critical", "platform": platform, "campaign": name,
                "issue": f"CPA ¥{cpa:,.0f} が全体平均 ¥{avg_cpa:,.0f} の {cpa/avg_cpa:.1f}倍",
                "action": "入札戦略見直しまたは一時停止"})

        if imps == 0 and cost > 0:
            issues.append({"check_id": "C04", "severity": "critical", "platform": platform, "campaign": name,
                "issue": f"インプレッション0件でコスト発生（配信異常）", "action": "配信ステータス・審査状況を確認"})

        if freq > common.get("frequency_max", 4.0):
            issues.append({"check_id": "C05", "severity": "high", "platform": platform, "campaign": name,
                "issue": f"フリークエンシー {freq:.1f} が上限超過", "action": "クリエイティブ追加・オーディエンス拡張"})

        if total_cost > 0 and (cost / total_cost * 100) >= common.get("cost_concentration_pct", 80):
            issues.append({"check_id": "C06", "severity": "medium", "platform": platform, "campaign": name,
                "issue": f"全体コストの {cost/total_cost*100:.0f}% が集中", "action": "他キャンペーンへの分散を検討"})

        if ctr < common.get("ctr_min", 1.0) and imps > 500:
            issues.append({"check_id": "C07", "severity": "medium", "platform": platform, "campaign": name,
                "issue": f"CTR {ctr:.2f}% が共通下限未満", "action": "クリエイティブ・ターゲティングの見直し"})

        # === Google 固有 ===
        if platform == "google":
            if ctype == "search":
                g_ctr = google_cfg.get("search", {}).get("ctr_min", 3.0)
                if ctr < g_ctr and imps > 500:
                    issues.append({"check_id": "G01", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"検索CTR {ctr:.2f}% が基準 {g_ctr}% 未満", "action": "広告文改善・除外KW追加"})
                g_cpa_r = google_cfg.get("search", {}).get("cpa_ratio_max", 2.0)
                if avg_cpa > 0 and cpa > avg_cpa * g_cpa_r and cv > 0:
                    issues.append({"check_id": "G02", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"検索CPA ¥{cpa:,.0f} が平均の {cpa/avg_cpa:.1f}倍", "action": "キーワード精査・入札調整"})

            if ctype == "shopping":
                g_roas = google_cfg.get("shopping", {}).get("roas_min", 3.0)
                if roas > 0 and roas < g_roas:
                    issues.append({"check_id": "G03", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"ショッピングROAS {roas:.2f} が基準 {g_roas} 未満", "action": "商品フィード最適化"})

            if ctype == "pmax":
                pmax_cv = google_cfg.get("pmax", {}).get("conversion_min_weekly", 30)
                if cv * 7 < pmax_cv:
                    issues.append({"check_id": "G04", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"PMax推定週間CV {cv*7} が基準 {pmax_cv} 未満", "action": "マイクロCV追加または予算増"})
                pmax_roas = google_cfg.get("pmax", {}).get("roas_min", 2.0)
                if roas > 0 and roas < pmax_roas:
                    issues.append({"check_id": "G05", "severity": "medium", "platform": platform, "campaign": name,
                        "issue": f"PMax ROAS {roas:.2f} が基準 {pmax_roas} 未満", "action": "アセットグループ見直し"})

        # === Meta 固有 ===
        if platform == "meta":
            if ctype == "feed":
                m_ctr = meta_cfg.get("feed", {}).get("ctr_min", 1.0)
                if ctr < m_ctr and imps > 1000:
                    issues.append({"check_id": "M01", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"フィードCTR {ctr:.2f}% が基準 {m_ctr}% 未満", "action": "クリエイティブ差し替え"})

            if "retarget" in name.lower() or "rtg" in name.lower():
                m_freq = meta_cfg.get("creative", {}).get("fatigue_frequency", 3.5)
                if freq > m_freq:
                    issues.append({"check_id": "M02", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"リタゲ頻度 {freq:.1f} が疲弊基準超過", "action": "除外設定・ウィンドウ短縮"})

            if ctype == "reels":
                r_ctr = meta_cfg.get("reels", {}).get("ctr_min", 0.7)
                if ctr < r_ctr and imps > 1000:
                    issues.append({"check_id": "M03", "severity": "medium", "platform": platform, "campaign": name,
                        "issue": f"Reels CTR {ctr:.2f}% が基準 {r_ctr}% 未満", "action": "冒頭フック改善・UGC風に"})

            m_cpa_r = meta_cfg.get("feed", {}).get("cpa_ratio_max", 2.5)
            if avg_cpa > 0 and cpa > avg_cpa * m_cpa_r and cv > 0:
                issues.append({"check_id": "M04", "severity": "high", "platform": platform, "campaign": name,
                    "issue": f"Meta CPA ¥{cpa:,.0f} が平均の {cpa/avg_cpa:.1f}倍", "action": "Advantage+活用検討"})

            m_wcv = meta_cfg.get("learning_phase", {}).get("min_weekly_conversions", 50)
            if cv * 7 < m_wcv:
                issues.append({"check_id": "M05", "severity": "high", "platform": platform, "campaign": name,
                    "issue": f"推定週間CV {cv*7} が学習安定基準 {m_wcv} 未満", "action": "CV地点上流化・広告セット統合"})

            meta_camps = by_platform.get("meta", [])
            m_max = meta_cfg.get("structure", {}).get("max_adsets_per_campaign", 5)
            if len(meta_camps) > m_max and camp == meta_camps[0]:
                issues.append({"check_id": "M06", "severity": "medium", "platform": platform, "campaign": "Meta全体",
                    "issue": f"Metaキャンペーン数 {len(meta_camps)} が推奨上限超過", "action": "統合・CBO活用"})

        # === TikTok 固有 ===
        if platform == "tiktok":
            if ctype == "in_feed":
                t_ctr = tiktok_cfg.get("in_feed", {}).get("ctr_min", 0.8)
                if ctr < t_ctr and imps > 1000:
                    issues.append({"check_id": "T01", "severity": "high", "platform": platform, "campaign": name,
                        "issue": f"In-Feed CTR {ctr:.2f}% が基準 {t_ctr}% 未満", "action": "冒頭フック強化・トレンド音源"})

            t_cpa_r = tiktok_cfg.get("in_feed", {}).get("cpa_ratio_max", 2.5)
            if avg_cpa > 0 and cpa > avg_cpa * t_cpa_r and cv > 0:
                issues.append({"check_id": "T02", "severity": "high", "platform": platform, "campaign": name,
                    "issue": f"TikTok CPA ¥{cpa:,.0f} が平均の {cpa/avg_cpa:.1f}倍", "action": "Smart+活用検討"})

            if revenue > 0 and roas < 1.0:
                issues.append({"check_id": "T03", "severity": "critical", "platform": platform, "campaign": name,
                    "issue": f"TikTok ROAS {roas:.2f} で赤字", "action": "クリエイティブ全面刷新"})

            t_wcv = tiktok_cfg.get("learning_phase", {}).get("min_weekly_conversions", 50)
            if cv * 7 < t_wcv:
                issues.append({"check_id": "T04", "severity": "high", "platform": platform, "campaign": name,
                    "issue": f"推定週間CV {cv*7} がTikTok学習基準 {t_wcv} 未満", "action": "CV地点上流化・Ad Group統合"})

            tt_camps = by_platform.get("tiktok", [])
            t_max = tiktok_cfg.get("structure", {}).get("max_adgroups_per_campaign", 5)
            if len(tt_camps) > t_max and camp == tt_camps[0]:
                issues.append({"check_id": "T05", "severity": "medium", "platform": platform, "campaign": "TikTok全体",
                    "issue": f"TikTokキャンペーン数 {len(tt_camps)} が推奨上限超過", "action": "Ad Group統合"})

        # === Quick Wins ===
        if roas >= 3.0 and cv >= 5:
            quick_wins.append({"check_id": "QW01", "platform": platform, "campaign": name,
                "action": f"ROAS {roas:.1f} と好調。予算20-30%増加を推奨", "expected_impact": "CV増加"})
        if avg_cpa > 0 and cpa > avg_cpa * 2 and cv > 0:
            savings = (cpa - avg_cpa) * cv
            quick_wins.append({"check_id": "QW02", "platform": platform, "campaign": name,
                "action": f"CPA改善で ¥{savings:,.0f} 削減可能", "expected_impact": "コスト削減"})
        if freq < 2.0 and ctr > 2.0 and cv > 0:
            quick_wins.append({"check_id": "QW03", "platform": platform, "campaign": name,
                "action": f"CTR {ctr:.2f}%/頻度 {freq:.1f} でスケール余地あり", "expected_impact": "リーチ拡大"})

    # === クロスプラットフォーム ===
    platform_roas = {}
    platform_cpa = {}
    for p, camps in by_platform.items():
        if camps and p != "unknown":
            p_rev = sum(c.get("revenue", 0) for c in camps)
            p_cost = sum(c.get("cost", 0) for c in camps)
            p_cv = sum(c.get("conversions", 0) for c in camps)
            if p_cost > 0:
                platform_roas[p] = round(p_rev / p_cost, 2)
            if p_cv > 0:
                platform_cpa[p] = round(p_cost / p_cv)

    if len(platform_roas) >= 2:
        best = max(platform_roas, key=platform_roas.get)
        worst = min(platform_roas, key=platform_roas.get)
        if platform_roas[worst] > 0:
            gap = platform_roas[best] / platform_roas[worst]
            if gap >= 3.0:
                issues.append({"check_id": "X01", "severity": "high", "platform": "cross", "campaign": "全体",
                    "issue": f"媒体間ROAS格差 {gap:.1f}倍（{best}: {platform_roas[best]} vs {worst}: {platform_roas[worst]}）",
                    "action": f"{worst} の予算を {best} にシフト検討"})

    if len(platform_cpa) >= 2:
        best = min(platform_cpa, key=platform_cpa.get)
        worst = max(platform_cpa, key=platform_cpa.get)
        if platform_cpa[best] > 0:
            gap = platform_cpa[worst] / platform_cpa[best]
            if gap >= 3.0:
                issues.append({"check_id": "X02", "severity": "high", "platform": "cross", "campaign": "全体",
                    "issue": f"媒体間CPA格差 {gap:.1f}倍（{worst}: ¥{platform_cpa[worst]:,} vs {best}: ¥{platform_cpa[best]:,}）",
                    "action": f"{worst} の効率改善または予算再配分"})

    # === スコア算定 ===
    severity_weights = thresholds.get("scoring", {}).get("severity_weights", {})
    total_penalty = sum(severity_weights.get(i.get("severity", "low"), 1.0) for i in issues)
    max_penalty = max(len(campaigns) * 5 * 0.5, 50)
    score = max(0, min(100, round(100 - (total_penalty / max_penalty * 100))))

    grades_cfg = thresholds.get("scoring", {}).get("grades", {})
    if score >= grades_cfg.get("A", 90): grade = "A"
    elif score >= grades_cfg.get("B", 75): grade = "B"
    elif score >= grades_cfg.get("C", 60): grade = "C"
    elif score >= grades_cfg.get("D", 40): grade = "D"
    else: grade = "F"

    platform_summary = {}
    for p in ["google", "meta", "tiktok"]:
        p_issues = [i for i in issues if i.get("platform") == p]
        p_camps = by_platform.get(p, [])
        if p_camps:
            platform_summary[p] = {
                "campaigns": len(p_camps), "issues": len(p_issues),
                "critical": len([i for i in p_issues if i["severity"] == "critical"]),
                "cost": sum(c.get("cost", 0) for c in p_camps),
                "conversions": sum(c.get("conversions", 0) for c in p_camps),
                "roas": platform_roas.get(p, 0),
            }

    result = {
        "score": score, "grade": grade,
        "total_campaigns": len(campaigns),
        "total_cost": totals.get("total_cost", 0),
        "total_conversions": totals.get("total_conversions", 0),
        "avg_cpa": avg_cpa, "avg_ctr": avg_ctr,
        "issues": issues, "quick_wins": quick_wins,
        "critical_count": len([i for i in issues if i["severity"] == "critical"]),
        "high_count": len([i for i in issues if i["severity"] == "high"]),
        "medium_count": len([i for i in issues if i["severity"] == "medium"]),
        "platform_summary": platform_summary,
        "check_count": 25,
    }
    log.info(f"[{client_id}] 監査完了: Score {score} ({grade}), Issues {len(issues)}, QuickWins {len(quick_wins)}")
    return result
