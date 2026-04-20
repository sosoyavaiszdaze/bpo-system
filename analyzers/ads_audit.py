"""広告監査 - Claude APIでチェックリスト評価"""
import os
import json
import logging

log = logging.getLogger("bpo")

def run_audit(client_id, data, thresholds):
    """広告データを監査してスコア・改善提案を返す"""
    campaigns = data.get("campaigns", [])
    totals = data.get("totals", {})

    if not campaigns:
        return {"score": 0, "grade": "F", "error": "No campaign data"}

    issues = []
    quick_wins = []
    score = 100

    avg_cpa = totals.get("avg_cpa", 0)
    avg_ctr = totals.get("avg_ctr", 0)

    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        cpa = camp.get("cpa", 0)
        ctr = camp.get("ctr", 0)
        cost = camp.get("cost", 0)
        cv = camp.get("conversions", 0)
        roas = camp.get("roas", 0)

        # CPA が平均の3倍以上
        if avg_cpa > 0 and cpa > avg_cpa * 3:
            issues.append({
                "severity": "critical",
                "campaign": name,
                "issue": f"CPA ¥{cpa:,.0f} が平均 ¥{avg_cpa:,.0f} の{cpa/avg_cpa:.1f}倍",
                "action": "入札戦略見直しまたは一時停止を検討",
            })
            score -= 15

        # CV 0 でコスト発生
        if cv == 0 and cost > thresholds.get("anomaly", {}).get("cv_zero_cost_min", 5000):
            issues.append({
                "severity": "critical",
                "campaign": name,
                "issue": f"CV 0件でコスト ¥{cost:,.0f} 発生",
                "action": "即時停止または大幅な改善が必要",
            })
            score -= 20

        # CTR が1%未満
        if ctr < 1.0 and camp.get("impressions", 0) > 500:
            issues.append({
                "severity": "warning",
                "campaign": name,
                "issue": f"CTR {ctr:.2f}% が低い（1%未満）",
                "action": "クリエイティブ・ターゲティングの見直し",
            })
            score -= 5

        # ROAS が1.0未満（赤字）
        if roas > 0 and roas < 1.0:
            issues.append({
                "severity": "critical",
                "campaign": name,
                "issue": f"ROAS {roas:.1f} で赤字運用",
                "action": "予算縮小またはターゲット変更",
            })
            score -= 10

        # Quick wins: 高ROAS キャンペーンの予算増加
        if roas >= 3.0 and cv >= 5:
            quick_wins.append({
                "campaign": name,
                "action": f"ROAS {roas:.1f} と好調。予算20-30%増加を推奨",
                "expected_impact": "CV増加",
            })

        # Quick wins: 高CPA キャンペーンの改善
        if avg_cpa > 0 and cpa > avg_cpa * 2 and cv > 0:
            quick_wins.append({
                "campaign": name,
                "action": f"CPA ¥{cpa:,.0f} を平均水準に改善すれば ¥{(cpa - avg_cpa) * cv:,.0f} 削減可能",
                "expected_impact": "コスト削減",
            })

    score = max(0, min(100, score))

    # グレード判定
    grades = thresholds.get("scoring", {}).get("grades", {})
    if score >= grades.get("A", 90):
        grade = "A"
    elif score >= grades.get("B", 75):
        grade = "B"
    elif score >= grades.get("C", 60):
        grade = "C"
    elif score >= grades.get("D", 40):
        grade = "D"
    else:
        grade = "F"

    result = {
        "score": score,
        "grade": grade,
        "total_campaigns": len(campaigns),
        "total_cost": totals.get("total_cost", 0),
        "total_conversions": totals.get("total_conversions", 0),
        "avg_cpa": avg_cpa,
        "avg_ctr": avg_ctr,
        "issues": issues,
        "quick_wins": quick_wins,
        "critical_count": len([i for i in issues if i["severity"] == "critical"]),
        "warning_count": len([i for i in issues if i["severity"] == "warning"]),
    }

    log.info(f"[{client_id}] 監査完了: Score {score} ({grade}), Issues {len(issues)}, QuickWins {len(quick_wins)}")
    return result
