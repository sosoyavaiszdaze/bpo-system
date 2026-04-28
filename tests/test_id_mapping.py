"""Phase 2: Python check_id = YAML rule_id 直接一致テスト"""
import os
import sys
import re
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
CHECKS_DIR = os.path.join(ROOT, "analyzers", "checks")


def _load_yaml_rule_ids(platform):
    """プラットフォーム別YAML定義のIDセットを返す"""
    path = os.path.join(RULES_DIR, f"{platform}_rules.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {rule["id"] for rule in data.get("rules", [])}


def _extract_check_ids_from_py(filepath):
    """Pythonファイルから _r("XXX", ...) 形式で発行されるcheck IDを抽出"""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    return set(re.findall(r'_r\(\s*"([A-Z][\w-]+)"', source))


class TestDirectIdMatch:
    """Phase 2: Python check_idがYAML rule_idに直接一致するか検証"""

    def test_google_ids_in_yaml(self):
        """google.py の全check IDがgoogle_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("google")
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "google.py"))
        missing = python_ids - yaml_ids
        assert not missing, f"Google: YAML未登録ID: {missing}"

    def test_meta_ids_in_yaml(self):
        """meta.py の全check IDがmeta_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("meta")
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "meta.py"))
        missing = python_ids - yaml_ids
        assert not missing, f"Meta: YAML未登録ID: {missing}"

    def test_tiktok_ids_in_yaml(self):
        """tiktok.py の全check IDがtiktok_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("tiktok")
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "tiktok.py"))
        missing = python_ids - yaml_ids
        assert not missing, f"TikTok: YAML未登録ID: {missing}"

    def test_no_hyphenated_ids_google(self):
        """google.py に旧ハイフン形式IDが残っていない"""
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "google.py"))
        old = [i for i in python_ids if "-" in i]
        assert not old, f"Google: 旧形式ID残存: {old}"

    def test_no_hyphenated_ids_meta(self):
        """meta.py に旧ハイフン形式IDが残っていない"""
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "meta.py"))
        old = [i for i in python_ids if "-" in i]
        assert not old, f"Meta: 旧形式ID残存: {old}"

    def test_no_hyphenated_ids_tiktok(self):
        """tiktok.py に旧ハイフン形式IDが残っていない"""
        python_ids = _extract_check_ids_from_py(os.path.join(CHECKS_DIR, "tiktok.py"))
        old = [i for i in python_ids if "-" in i]
        assert not old, f"TikTok: 旧形式ID残存: {old}"


class TestIdMapperPassthrough:
    """Phase 2: id_mapper.pyがパススルーとして動作する"""

    def test_passthrough_google(self):
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("G25", "google") == "G25"

    def test_passthrough_meta(self):
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("M01", "meta") == "M01"

    def test_passthrough_tiktok(self):
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("T01", "tiktok") == "T01"

    def test_coverage_100(self):
        from engine.id_mapper import get_mapping_coverage
        for p in ["google", "meta", "tiktok"]:
            cov = get_mapping_coverage(p)
            assert cov["coverage_pct"] == 100.0

    def test_no_unmapped(self):
        from engine.id_mapper import get_unmapped_checks
        for p in ["google", "meta", "tiktok"]:
            assert get_unmapped_checks(p) == []

    def test_clear_cache_noop(self):
        from engine.id_mapper import clear_cache
        clear_cache()  # Should not raise
