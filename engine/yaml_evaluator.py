"""YAML ルール評価エンジン — YAML ルール定義を読み込み、チェック結果に重みを適用"""
import os
import yaml
import logging

log = logging.getLogger("bpo")

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rules")


def load_rules(platform):
    """プラットフォーム別ルール定義を読み込み

    Args:
        platform: "google" | "meta" | "tiktok" | "seo"
    Returns:
        dict: ルール定義 (category_weights, rules リスト)
    """
    filename = f"{platform}_rules.yaml"
    path = os.path.join(RULES_DIR, filename)
    if not os.path.exists(path):
        log.warning(f"ルールファイル未検出: {path}")
        return {"category_weights": {}, "rules": []}

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"category_weights": {}, "rules": []}


def get_rule(rules_data, check_id):
    """チェックIDからルール定義を取得"""
    for rule in rules_data.get("rules", []):
        if rule.get("id") == check_id:
            return rule
    return None


def calc_check_weight(rule, severity_weights=None):
    """個別チェックの重みを計算: severity_weight × rule_weight

    Args:
        rule: ルール定義 dict
        severity_weights: severity → weight のマッピング
    Returns:
        float: チェックの重み
    """
    if severity_weights is None:
        severity_weights = {
            "critical": 5.0,
            "high": 3.0,
            "medium": 1.5,
            "low": 0.5,
        }

    severity = rule.get("severity", "medium")
    sev_w = severity_weights.get(severity, 1.0)
    rule_w = rule.get("weight", 1.0)
    return sev_w * rule_w


def evaluate_checks(check_results, platform, severity_weights=None):
    """チェック結果にルール重みを適用してスコアデータを返す

    Args:
        check_results: [{"id": "G01", "passed": True/False, ...}, ...]
        platform: "google" | "meta" | "tiktok"
        severity_weights: severity → weight マッピング
    Returns:
        dict: {
            "weighted_pass": float, "weighted_total": float,
            "by_category": {category: {"pass": float, "total": float}},
            "details": [{"id", "name", "passed", "weight", "category", "severity"}, ...]
        }
    """
    rules_data = load_rules(platform)
    category_weights = rules_data.get("category_weights", {})

    weighted_pass = 0.0
    weighted_total = 0.0
    by_category = {}
    details = []

    for check in check_results:
        check_id = check.get("id", "")
        passed = check.get("passed", True)

        rule = get_rule(rules_data, check_id)
        if not rule:
            # ルール定義が見つからない場合はデフォルト medium
            rule = {"id": check_id, "severity": "medium", "weight": 1.0, "category": "other"}

        w = calc_check_weight(rule, severity_weights)
        cat = rule.get("category", "other")
        cat_w = category_weights.get(cat, 1.0)
        effective_w = w * cat_w

        weighted_total += effective_w
        if passed:
            weighted_pass += effective_w

        # カテゴリ別集計
        if cat not in by_category:
            by_category[cat] = {"pass": 0.0, "total": 0.0, "checks": 0, "passed": 0}
        by_category[cat]["total"] += effective_w
        by_category[cat]["checks"] += 1
        if passed:
            by_category[cat]["pass"] += effective_w
            by_category[cat]["passed"] += 1

        details.append({
            "id": check_id,
            "name": rule.get("name", check.get("name", "")),
            "passed": passed,
            "weight": round(effective_w, 2),
            "category": cat,
            "severity": rule.get("severity", "medium"),
            "conflict_group": rule.get("conflict_group"),
            "message": check.get("message", ""),
        })

    return {
        "weighted_pass": weighted_pass,
        "weighted_total": weighted_total,
        "by_category": by_category,
        "details": details,
    }
