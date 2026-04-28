"""IDマッピング整合性テスト — Phase 1: マッピング経由の変換を検証"""
import os
import sys
import re
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
CHECKS_DIR = os.path.join(ROOT, "analyzers", "checks")
MAPPING_PATH = os.path.join(RULES_DIR, "id_mapping.yaml")


def _load_yaml_rule_ids(platform):
    path = os.path.join(RULES_DIR, f"{platform}_rules.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {rule["id"] for rule in data.get("rules", [])}


def _extract_check_ids_from_py(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    return set(re.findall(r'_r\(\s*"([A-Z][\w-]+)"', source))


@pytest.fixture(scope="module")
def mapping():
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestMappingTargetsExist:
    """マッピング先のYAML IDが実際のYAMLファイルに存在するか"""

    def test_google_targets(self, mapping):
        yaml_ids = _load_yaml_rule_ids("google")
        errors = []
        for pid, yid in mapping.get("google", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "Missing targets:\n" + "\n".join(errors)

    def test_meta_targets(self, mapping):
        yaml_ids = _load_yaml_rule_ids("meta")
        errors = []
        for pid, yid in mapping.get("meta", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "Missing targets:\n" + "\n".join(errors)

    def test_tiktok_targets(self, mapping):
        yaml_ids = _load_yaml_rule_ids("tiktok")
        errors = []
        for pid, yid in mapping.get("tiktok", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "Missing targets:\n" + "\n".join(errors)


class TestMappingCompleteness:
    """Python checkの全IDがマッピングに含まれるか"""

    def test_google_check_ids_covered(self, mapping):
        google_map = set(mapping.get("google", {}).keys())
        py_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "google.py"))
        yaml_ids = _load_yaml_rule_ids("google")
        uncovered = [pid for pid in py_ids if pid not in google_map and pid not in yaml_ids]
        assert not uncovered, f"Google: マッピング未登録: {uncovered}"

    def test_meta_check_ids_covered(self, mapping):
        meta_map = set(mapping.get("meta", {}).keys())
        py_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "meta.py"))
        yaml_ids = _load_yaml_rule_ids("meta")
        uncovered = [pid for pid in py_ids if pid not in meta_map and pid not in yaml_ids]
        assert not uncovered, f"Meta: マッピング未登録: {uncovered}"

    def test_tiktok_check_ids_covered(self, mapping):
        tiktok_map = set(mapping.get("tiktok", {}).keys())
        py_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "tiktok.py"))
        yaml_ids = _load_yaml_rule_ids("tiktok")
        uncovered = [pid for pid in py_ids if pid not in tiktok_map and pid not in yaml_ids]
        assert not uncovered, f"TikTok: マッピング未登録: {uncovered}"


class TestIdMapperModule:
    """engine/id_mapper.py の機能テスト"""

    def test_to_yaml_id_mapped(self):
        from engine.id_mapper import to_yaml_id, clear_cache
        clear_cache()
        assert to_yaml_id("G01", "google") == "G25"
        assert to_yaml_id("M-PI1", "meta") == "M01"
        assert to_yaml_id("T-TC1", "tiktok") == "T01"

    def test_unmapped_returns_original(self):
        from engine.id_mapper import to_yaml_id, clear_cache
        clear_cache()
        assert to_yaml_id("G-PM1", "google") == "G-PM1"

    def test_common_passthrough(self):
        from engine.id_mapper import to_yaml_id, clear_cache
        clear_cache()
        assert to_yaml_id("C01", "common") == "C01"

    def test_mapping_coverage(self):
        from engine.id_mapper import get_mapping_coverage, clear_cache
        clear_cache()
        cov = get_mapping_coverage("google")
        assert cov["total"] > 0
        assert cov["coverage_pct"] > 0
