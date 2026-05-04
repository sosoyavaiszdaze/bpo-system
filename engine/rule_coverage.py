"""ルールカバレッジ分析 — YAML定義と実チェック結果の乖離を検出 (v2.0 IDマッピング対応)"""
import logging
import os
import re
from engine.yaml_evaluator import load_rules
from engine.id_mapper import to_yaml_id, get_mapping_coverage

log = logging.getLogger("bpo")

PLATFORMS = ["google", "meta", "tiktok", "seo", "adtruth"]

# 静的スキャン対象ファイル: 条件依存実行のチェック (例: M44 は advantage_plus=True 時のみ
# emit) はランタイム結果だけでは「実装済み」と判定できないため、ソースコードから
# 実装済み rule_id を抽出して補完する。
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCAN_FILES = [
    os.path.join(_BASE_DIR, "analyzers", "checks", "common.py"),
    os.path.join(_BASE_DIR, "analyzers", "checks", "google.py"),
    os.path.join(_BASE_DIR, "analyzers", "checks", "meta.py"),
    os.path.join(_BASE_DIR, "analyzers", "checks", "tiktok.py"),
    os.path.join(_BASE_DIR, "analyzers", "checks", "cross.py"),
    os.path.join(_BASE_DIR, "analyzers", "fraud_audit.py"),
    os.path.join(_BASE_DIR, "seo", "seo_audit.py"),
]

# `_r("M44", ...)` と `"id": "C01"` の 2 系統を検出
_RULE_ID_PATTERNS = [
    re.compile(r'_r\("([A-Z][A-Z0-9\-]*[0-9][a-z]?)"'),
    re.compile(r'"id":\s*"([A-Z][A-Z0-9\-]*[0-9][a-z]?)"'),
]

_implemented_ids_cache = None


def _scan_implemented_rule_ids():
    """analyzer ソースから実装済み rule_id を抽出 (キャッシュ付き)。"""
    global _implemented_ids_cache
    if _implemented_ids_cache is not None:
        return _implemented_ids_cache
    found = set()
    for path in _SCAN_FILES:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except OSError:
            continue
        for pat in _RULE_ID_PATTERNS:
            found.update(pat.findall(src))
    _implemented_ids_cache = found
    log.debug(f"静的スキャンで {len(found)} 件の実装済み rule_id を検出")
    return found


def analyze_coverage(check_results):
    """YAMLルール定義と実チェック結果の突合（IDマッピング考慮）"""
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

    # 2. 実チェック結果のIDをYAML IDに変換してマーク
    executed_python_ids = set()
    for check in check_results:
        pid = check.get("id", "")
        platform = check.get("platform", "unknown")
        executed_python_ids.add(pid)

        # YAML ID に変換してマッチ
        yid = to_yaml_id(pid, platform)
        if yid in all_yaml_ids:
            all_yaml_ids[yid]["has_check"] = True
        # 変換前のIDでも直接マッチ（common/cross用）
        if pid in all_yaml_ids:
            all_yaml_ids[pid]["has_check"] = True

    # 2.5. 静的スキャン: 条件依存で結果が emit されない実装も「実装済み」として扱う
    # (例: M44 は advantage_plus=True 時のみ、M49 は同一 type が >2 件時のみ発火)
    if check_results:
        for sid in _scan_implemented_rule_ids():
            if sid in all_yaml_ids:
                all_yaml_ids[sid]["has_check"] = True

    # 3. カバレッジ集計
    covered = [rid for rid, info in all_yaml_ids.items() if info["has_check"]]
    uncovered = [rid for rid, info in all_yaml_ids.items() if not info["has_check"]]
    orphan = [pid for pid in executed_python_ids
              if pid not in all_yaml_ids and to_yaml_id(pid, "google") not in all_yaml_ids]

    # 4. severity別の未カバー
    uncovered_by_severity = {}
    for rid in uncovered:
        sev = all_yaml_ids[rid]["severity"]
        uncovered_by_severity.setdefault(sev, []).append(rid)

    coverage_pct = round(len(covered) / len(all_yaml_ids) * 100, 1) if all_yaml_ids else 100

    # 5. マッピングカバレッジも含める
    mapping_stats = {}
    for p in ["google", "meta", "tiktok"]:
        mapping_stats[p] = get_mapping_coverage(p)

    report = {
        "total_yaml_rules": len(all_yaml_ids),
        "total_executed_checks": len(executed_python_ids),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "orphan_checks": len(orphan),
        "coverage_percent": coverage_pct,
        "uncovered_ids": uncovered,
        "uncovered_by_severity": uncovered_by_severity,
        "orphan_ids": list(orphan),
        "uncovered_critical": uncovered_by_severity.get("critical", []),
        "mapping_stats": mapping_stats,
    }

    if uncovered_by_severity.get("critical"):
        log.warning(f"未実装のcriticalルール: {uncovered_by_severity['critical']}")
    log.info(
        f"ルールカバレッジ: {coverage_pct}% "
        f"({len(covered)}/{len(all_yaml_ids)}), "
        f"未カバー: {len(uncovered)}, 孤立チェック: {len(orphan)}"
    )

    return report
