"""ルールカバレッジ分析 — YAML定義と実チェック結果の乖離を検出"""
import logging
from engine.yaml_evaluator import load_rules

log = logging.getLogger("bpo")

PLATFORMS = ["google", "meta", "tiktok", "seo", "adtruth"]


def analyze_coverage(check_results):
    """YAMLルール定義と実チェック結果の突合を行い、カバレッジを算出

    Args:
        check_results: 全チェック結果リスト
    Returns:
        dict: カバレッジレポート
    """
    # 1. 全YAMLルールIDを収集（enabled:trueのみ）
    all_yaml_ids = {}
    for platform in PLATFORMS:
        rules_data = load_rules(platform)
        for rule in rules_data.get("rules", []):
            if rule.get("enabled", True):
                all_yaml_ids[rule["id"]] = {
                    "platform": platform,
                    "name": rule.get("name", ""),
                    "severity": rule.get("severity", "medium"),
                    "has_check": False,
                }

    # 2. 実チェック結果に存在するIDをマーク
    executed_ids = set()
    for check in check_results:
        cid = check.get("id", "")
        executed_ids.add(cid)
        if cid in all_yaml_ids:
            all_yaml_ids[cid]["has_check"] = True

    # 3. カバレッジ集計
    covered = [rid for rid, info in all_yaml_ids.items() if info["has_check"]]
    uncovered = [rid for rid, info in all_yaml_ids.items() if not info["has_check"]]
    orphan = [cid for cid in executed_ids if cid not in all_yaml_ids]

    # 4. severity別の未カバー
    uncovered_by_severity = {}
    for rid in uncovered:
        sev = all_yaml_ids[rid]["severity"]
        uncovered_by_severity.setdefault(sev, []).append(rid)

    coverage_pct = round(len(covered) / len(all_yaml_ids) * 100, 1) if all_yaml_ids else 100

    report = {
        "total_yaml_rules": len(all_yaml_ids),
        "total_executed_checks": len(executed_ids),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "orphan_checks": len(orphan),
        "coverage_percent": coverage_pct,
        "uncovered_ids": uncovered,
        "uncovered_by_severity": uncovered_by_severity,
        "orphan_ids": orphan,
        "uncovered_critical": uncovered_by_severity.get("critical", []),
    }

    # 5. 警告ログ
    if uncovered_by_severity.get("critical"):
        log.warning(f"未実装のcriticalルール: {uncovered_by_severity['critical']}")
    log.info(
        f"ルールカバレッジ: {coverage_pct}% "
        f"({len(covered)}/{len(all_yaml_ids)}), "
        f"未カバー: {len(uncovered)}, 孤立チェック: {len(orphan)}"
    )

    return report
