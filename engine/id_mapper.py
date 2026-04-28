"""ID Mapper — Phase 2: パススルー実装（YAML ID = Python ID）"""
import logging

log = logging.getLogger("bpo")


def to_yaml_id(check_id, platform=""):
    """Phase 2: Python ID と YAML ID は同一のためそのまま返す"""
    return check_id


def to_python_id(yaml_id, platform=""):
    """Phase 2: 逆変換も不要、そのまま返す"""
    return yaml_id


def get_unmapped_checks(platform):
    """Phase 2: 未マッピングは存在しない"""
    return []


def get_mapping_coverage(platform):
    """Phase 2: カバレッジは常に 100%"""
    return {"total": 0, "mapped": 0, "unmapped": 0, "coverage_pct": 100.0}


def clear_cache():
    """Phase 2: キャッシュ不要"""
    pass
