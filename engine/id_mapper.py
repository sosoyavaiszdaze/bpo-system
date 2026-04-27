"""YAML rule_id ↔ Python check_id マッピング"""
import os
import yaml
import logging

log = logging.getLogger("bpo")

_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "rules", "id_mapping.yaml"
)

_cache = None


def _load_mapping():
    """マッピング定義を読み込み（キャッシュ付き）"""
    global _cache
    if _cache is not None:
        return _cache

    if not os.path.exists(_MAPPING_PATH):
        log.warning(f"IDマッピングファイル未検出: {_MAPPING_PATH}")
        _cache = {}
        return _cache

    with open(_MAPPING_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    mapping = {}
    for platform in ("google", "meta", "tiktok"):
        platform_map = raw.get(platform, {})
        for python_id, yaml_id in platform_map.items():
            if yaml_id and yaml_id != "_unmapped":
                mapping[(python_id, platform)] = yaml_id

    _cache = mapping
    log.info(f"IDマッピング読込: {len(mapping)}件 (unmapped除外)")
    return _cache


def to_yaml_id(python_check_id, platform):
    """Python check_id → YAML rule_id に変換。マッピングがなければ元IDを返す。"""
    mapping = _load_mapping()
    return mapping.get((python_check_id, platform), python_check_id)


def to_python_id(yaml_rule_id, platform):
    """YAML rule_id → Python check_id に逆変換"""
    mapping = _load_mapping()
    for (pid, plat), yid in mapping.items():
        if yid == yaml_rule_id and plat == platform:
            return pid
    return yaml_rule_id


def get_unmapped_checks(platform):
    """マッピングファイルで _unmapped とされたPython check IDの一覧"""
    if not os.path.exists(_MAPPING_PATH):
        return []
    with open(_MAPPING_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    platform_map = raw.get(platform, {})
    return [pid for pid, yid in platform_map.items() if yid == "_unmapped"]


def get_mapping_coverage(platform):
    """マッピングカバレッジを算出"""
    if not os.path.exists(_MAPPING_PATH):
        return {"total": 0, "mapped": 0, "unmapped": 0, "coverage_pct": 0}
    with open(_MAPPING_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    platform_map = raw.get(platform, {})
    total = len(platform_map)
    mapped = sum(1 for v in platform_map.values() if v != "_unmapped")
    return {
        "total": total,
        "mapped": mapped,
        "unmapped": total - mapped,
        "coverage_pct": round(mapped / total * 100, 1) if total > 0 else 0,
    }


def clear_cache():
    """テスト用キャッシュクリア"""
    global _cache
    _cache = None
