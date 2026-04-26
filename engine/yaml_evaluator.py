"""YAML ルール評価エンジン v2.0 — polarity multiplier, prerequisite chain, enabled skip"""
import os
import yaml
import logging

log = logging.getLogger("bpo")

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rules")

# §5-1: polarity → multiplier マッピング
POLARITY_MULTIPLIERS = {
    "neutral": 1.0,
    "preserve": 1.2,
    "monitor_only": 0.3,
    "aggregate": 1.0,
    "separate": 1.0,
    "open": 0.5,
    # context_dependent / budget_first は動的解決
}

# §5-2: 入札戦略分類
AUTO_BIDDING = {"target_cpa", "target_roas", "max_conversions", "max_conv_value",
                "maximize_conversions", "maximize_conversion_value", "target_impression_share"}
MANUAL_BIDDING = {"manual_cpc", "manual_cpm", "manual_cpv"}


def load_rules(platform):
    """プラットフォーム別ルール定義を読み込み"""
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
    """個別チェックの重みを計算: severity_weight × rule_weight"""
    if severity_weights is None:
        severity_weights = {"critical": 5.0, "high": 3.0, "medium": 1.5, "low": 0.5}
    severity = rule.get("severity", "medium")
    sev_w = severity_weights.get(severity, 1.0)
    rule_w = rule.get("weight", 1.0)
    return sev_w * rule_w


def evaluate_checks(check_results, platform, severity_weights=None):
    """チェック結果にルール重みを適用してスコアデータを返す (v2.0)

    v2.0追加: polarity multiplier, prerequisite chain, enabled:false skip
    """
    rules_data = load_rules(platform)
    common_rules_data = load_rules("common")
    category_weights = rules_data.get("category_weights", {})
    common_cw = common_rules_data.get("category_weights", {})
    for k, v in common_cw.items():
        if k not in category_weights:
            category_weights[k] = v

    # インデックス構築（prerequisite/budget_first のO(1)ルックアップ用）
    result_index = _build_result_index(check_results)

    weighted_pass = 0.0
    weighted_total = 0.0
    by_category = {}
    details = []

    for check in check_results:
        check_id = check.get("id", "")
        passed = check.get("passed", True)

        rule = get_rule(rules_data, check_id)
        if not rule:
            rule = get_rule(common_rules_data, check_id)
        if not rule:
            rule = {"id": check_id, "severity": "medium", "weight": 1.0, "category": "other"}

        # §8: enabled:false はスキップ
        if not rule.get("enabled", True):
            details.append({
                "id": check_id, "name": rule.get("name", ""),
                "passed": passed, "scoring_passed": None,
                "weight": 0, "category": rule.get("category", "other"),
                "severity": rule.get("severity", "medium"),
                "conflict_group": rule.get("conflict_group"),
                "message": "Phase 2/3: evaluation skipped",
                "polarity": rule.get("polarity", "neutral"),
                "polarity_multiplier": 0,
                "primary_axis": rule.get("primary_axis"),
                "axis_position": rule.get("axis_position", "neutral"),
                "enabled": False, "blocked_by": [],
            })
            continue

        # §5: polarity multiplier
        polarity = rule.get("polarity", "neutral")
        polarity_mult = _resolve_polarity(polarity, check, check_results, result_index)

        # 基本重み計算
        w = calc_check_weight(rule, severity_weights)
        cat = rule.get("category", "other")
        cat_w = category_weights.get(cat, 1.0)
        base_effective_w = w * cat_w

        # §6: prerequisite 評価
        is_blocked, blocked_by_list = _evaluate_prerequisite(rule, check_results, check, result_index)

        if is_blocked:
            effective_w = base_effective_w * 0.3
            scoring_passed = False
            weighted_total += effective_w
            # weighted_pass += 0 (ブロック時は加算しない)
        else:
            # polarity multiplier 適用
            effective_w = base_effective_w * polarity_mult
            scoring_passed = passed
            weighted_total += effective_w
            if passed:
                weighted_pass += effective_w

        # カテゴリ別集計
        if cat not in by_category:
            by_category[cat] = {"pass": 0.0, "total": 0.0, "checks": 0, "passed": 0}
        by_category[cat]["total"] += effective_w
        by_category[cat]["checks"] += 1
        if scoring_passed:
            by_category[cat]["pass"] += effective_w
            by_category[cat]["passed"] += 1

        details.append({
            # v1.0 互換
            "id": check_id,
            "name": rule.get("name", check.get("name", "")),
            "passed": passed,
            "weight": round(effective_w, 2),
            "category": cat,
            "severity": rule.get("severity", "medium"),
            "conflict_group": rule.get("conflict_group"),
            "message": check.get("message", ""),
            # v2.0 追加
            "scoring_passed": scoring_passed,
            "polarity": polarity,
            "polarity_multiplier": round(polarity_mult, 2),
            "primary_axis": rule.get("primary_axis"),
            "axis_position": rule.get("axis_position", "neutral"),
            "enabled": True,
            "blocked_by": blocked_by_list,
        })

    return {
        "weighted_pass": weighted_pass,
        "weighted_total": weighted_total,
        "by_category": by_category,
        "details": details,
    }


# ========================================
# §5: Polarity Resolution
# ========================================

def _resolve_polarity(polarity, check_result, all_results, result_index=None):
    """polarity文字列をmultiplierに解決"""
    if polarity == "context_dependent":
        return _resolve_context_dependent(check_result)
    if polarity == "budget_first":
        return _resolve_budget_first(check_result, all_results, result_index)
    return POLARITY_MULTIPLIERS.get(polarity, 1.0)


def _get_bidding_strategy(check_result):
    """チェック結果から入札戦略を抽出（3段階フォールバック）"""
    bs = check_result.get("bidding_strategy")
    if bs:
        return bs.lower().replace(" ", "_")
    ctx = check_result.get("context") or {}
    bs = ctx.get("bidding_strategy")
    if bs:
        return bs.lower().replace(" ", "_")
    return "unknown"


def _resolve_context_dependent(check_result):
    """§5-2: 自動入札→0.05(ほぼゼロだが評価済みとして可視化), 手動入札→1.0, 不明→0.5"""
    bidding = _get_bidding_strategy(check_result)
    if bidding in AUTO_BIDDING:
        return 0.05
    elif bidding in MANUAL_BIDDING:
        return 1.0
    else:
        return 0.5


def _resolve_budget_first(check_result, all_results, result_index=None):
    """§5-3: G39(予算制約) の結果を参照"""
    campaign = check_result.get("campaign", "")
    platform = check_result.get("platform", "")
    lookup = _find_result_indexed if result_index else _find_result_scoped
    source = result_index if result_index else all_results
    g39 = lookup(source, "G39", platform, campaign)
    if g39 is None:
        g13 = lookup(source, "G13", platform, campaign)
        if g13 is not None and not g13.get("passed", True):
            return 0.3
        return 0.5
    if not g39.get("passed", True):
        return 0.3
    return 1.0


# ========================================
# §6: Prerequisite Chain
# ========================================

def _find_result_scoped(all_results, check_id, platform, campaign):
    """§6-1: キャンペーンスコープのルックアップ"""
    # 1. same platform + same campaign
    for r in all_results:
        if r.get("id") == check_id and r.get("platform") == platform and r.get("campaign") == campaign:
            return r
    # 2. same platform + account-level
    for r in all_results:
        if r.get("id") == check_id and r.get("platform") == platform and r.get("campaign") == "アカウント全体":
            return r
    return None


def _evaluate_prerequisite(rule, all_results, check_result, result_index=None):
    """§6-2: prerequisite 評価 — ブロックされていれば (True, [blocked_ids])"""
    prereqs = rule.get("prerequisite", [])
    if not prereqs:
        return False, []
    campaign = check_result.get("campaign", "")
    platform = check_result.get("platform", "")
    lookup = _find_result_indexed if result_index else _find_result_scoped
    source = result_index if result_index else all_results
    blocked_by = []
    for prereq_id in prereqs:
        prereq = lookup(source, prereq_id, platform, campaign)
        if prereq is None:
            blocked_by.append(prereq_id)
        elif not prereq.get("passed", True):
            blocked_by.append(prereq_id)
    return len(blocked_by) > 0, blocked_by


def _build_result_index(check_results):
    """check_id + platform + campaign でインデックス辞書を構築 (O(n)で1回)"""
    index = {}
    for r in check_results:
        cid = r.get("id", "")
        plat = r.get("platform", "")
        camp = r.get("campaign", "")
        index.setdefault((cid, plat, camp), r)
        if camp == "アカウント全体":
            index.setdefault((cid, plat, "__account__"), r)
    return index


def _find_result_indexed(index, check_id, platform, campaign):
    """インデックスからO(1)で結果を引く"""
    r = index.get((check_id, platform, campaign))
    if r:
        return r
    r = index.get((check_id, platform, "__account__"))
    if r:
        return r
    r = index.get((check_id, platform, "アカウント全体"))
    if r:
        return r
    return None
