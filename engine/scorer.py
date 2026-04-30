"""スコアリングエンジン v2.0 — 設計文書 v1.3 準拠
S_total = Σ(C_pass × W_sev × W_cat) / Σ(C_total × W_sev × W_cat) × 100
"""
import logging

log = logging.getLogger("bpo")

GRADE_THRESHOLDS = {"A": 90, "B": 75, "C": 60, "D": 40, "F": 0}


def calc_platform_score(eval_result):
    """プラットフォーム別スコアを計算

    Args:
        eval_result: yaml_evaluator.evaluate_checks() の出力
    Returns:
        dict: {"score": float, "grade": str, "by_category": {...}}
    """
    total = eval_result.get("weighted_total", 0)
    passed = eval_result.get("weighted_pass", 0)

    if total == 0:
        return {"score": None, "grade": "N/A", "by_category": {}}

    score = round(passed / total * 100, 1)
    grade = _get_grade(score)

    # カテゴリ別スコア
    by_cat = {}
    for cat, data in eval_result.get("by_category", {}).items():
        cat_total = data["total"]
        cat_pass = data["pass"]
        cat_score = round(cat_pass / cat_total * 100, 1) if cat_total > 0 else 100
        by_cat[cat] = {
            "score": cat_score,
            "checks": data["checks"],
            "passed": data["passed"],
            "grade": _get_grade(cat_score),
        }

    return {
        "score": score,
        "grade": grade,
        "by_category": by_cat,
    }


def calc_cross_platform_score(platform_scores, budget_shares=None):
    """クロスプラットフォーム集計スコア（予算シェア加重平均）

    Args:
        platform_scores: {"google": {"score": 85, ...}, "meta": {"score": 72, ...}}
        budget_shares: {"google": 0.5, "meta": 0.3, "tiktok": 0.2}
    Returns:
        dict: {"score": float, "grade": str, "platform_scores": {...}}
    """
    if not platform_scores:
        return {"score": 0, "grade": "F", "platform_scores": {}}

    # score が None (データなし) のプラットフォームを除外
    valid_scores = {p: ps for p, ps in platform_scores.items() if ps.get("score") is not None}
    if not valid_scores:
        return {"score": 0, "grade": "F", "platform_scores": platform_scores}

    if budget_shares:
        weighted_score = 0
        total_weight = 0
        for platform, ps in valid_scores.items():
            share = budget_shares.get(platform, 1.0 / len(valid_scores))
            weighted_score += ps["score"] * share
            total_weight += share
        score = round(weighted_score / total_weight, 1) if total_weight > 0 else 0
    else:
        scores = [ps["score"] for ps in valid_scores.values()]
        score = round(sum(scores) / len(scores), 1)

    return {
        "score": score,
        "grade": _get_grade(score),
        "platform_scores": platform_scores,
    }


def calc_budget_shares(data):
    """データからプラットフォーム別予算シェアを計算

    Args:
        data: unified format データ
    Returns:
        dict: {"google": 0.5, "meta": 0.3, "tiktok": 0.2}
    """
    platform_costs = {}
    total_cost = 0

    for camp in data.get("campaigns", []):
        platform = camp.get("platform", "unknown")
        cost = camp.get("cost", 0)
        platform_costs[platform] = platform_costs.get(platform, 0) + cost
        total_cost += cost

    if total_cost == 0:
        return {}

    return {p: round(c / total_cost, 3) for p, c in platform_costs.items()}


def _get_grade(score):
    """スコアからグレードを判定"""
    for grade, threshold in GRADE_THRESHOLDS.items():
        if score >= threshold:
            return grade
    return "F"
