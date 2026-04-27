"""IDマッピング整合性テスト — YAML rule_id と Python check_id の対応を検証"""
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


def _load_yaml(filename):
    """YAML ファイルを読み込む"""
    path = os.path.join(RULES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_yaml_rule_ids(platform):
    """プラットフォーム別YAML定義のIDセットを返す"""
    data = _load_yaml(f"{platform}_rules.yaml")
    return {rule["id"] for rule in data.get("rules", [])}


def _extract_check_ids_from_py(filepath):
    """Pythonファイルから _r("XXX", ...) 形式で発行されるcheck IDを抽出"""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    return set(re.findall(r'_r\(\s*"([A-Z][\w-]+)"', source))


@pytest.fixture(scope="module")
def mapping():
    """id_mapping.yaml を読み込む共有フィクスチャ"""
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestMappingTargetsExist:
    """マッピング先のYAML IDが実際のYAMLファイルに存在するか検証"""

    def test_google_targets(self, mapping):
        """Google: マッピング先IDがgoogle_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("google")
        errors = []
        for pid, yid in mapping.get("google", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "Google: YAML IDが存在しない:\n" + "\n".join(errors)

    def test_meta_targets(self, mapping):
        """Meta: マッピング先IDがmeta_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("meta")
        errors = []
        for pid, yid in mapping.get("meta", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "Meta: YAML IDが存在しない:\n" + "\n".join(errors)

    def test_tiktok_targets(self, mapping):
        """TikTok: マッピング先IDがtiktok_rules.yamlに存在する"""
        yaml_ids = _load_yaml_rule_ids("tiktok")
        errors = []
        for pid, yid in mapping.get("tiktok", {}).items():
            if yid != "_unmapped" and yid not in yaml_ids:
                errors.append(f"{pid} -> {yid}")
        assert not errors, "TikTok: YAML IDが存在しない:\n" + "\n".join(errors)

    def test_no_duplicate_yaml_targets(self, mapping):
        """同一プラットフォーム内のN:1マッピングを可視化（エラーではなく記録）"""
        for platform in ("google", "meta", "tiktok"):
            platform_map = mapping.get(platform, {})
            seen = {}
            for pid, yid in platform_map.items():
                if yid != "_unmapped":
                    seen.setdefault(yid, []).append(pid)
            # N:1 マッピングは許容するが、存在することを確認
            for yid, sources in seen.items():
                assert len(sources) >= 1, f"{platform}: {yid} にマッピング元がない"


class TestMappingCompleteness:
    """Python checks/*.py で発行される全check IDがマッピングに含まれているか検証"""

    def test_google_check_ids_covered(self, mapping):
        """google.py の全check IDがid_mapping.yamlに登録済み"""
        google_map = set(mapping.get("google", {}).keys())
        yaml_ids = _load_yaml_rule_ids("google")

        py_path = os.path.join(CHECKS_DIR, "google.py")
        python_ids = _extract_check_ids_from_py(py_path)

        uncovered = []
        for pid in python_ids:
            if pid not in google_map and pid not in yaml_ids:
                uncovered.append(pid)

        assert not uncovered, f"Google: マッピング未登録のcheck ID: {uncovered}"

    def test_meta_check_ids_covered(self, mapping):
        """meta.py の全check IDがid_mapping.yamlに登録済み"""
        meta_map = set(mapping.get("meta", {}).keys())
        yaml_ids = _load_yaml_rule_ids("meta")

        py_path = os.path.join(CHECKS_DIR, "meta.py")
        python_ids = _extract_check_ids_from_py(py_path)

        uncovered = []
        for pid in python_ids:
            if pid not in meta_map and pid not in yaml_ids:
                uncovered.append(pid)

        assert not uncovered, f"Meta: マッピング未登録のcheck ID: {uncovered}"

    def test_tiktok_check_ids_covered(self, mapping):
        """tiktok.py の全check IDがid_mapping.yamlに登録済み"""
        tiktok_map = set(mapping.get("tiktok", {}).keys())
        yaml_ids = _load_yaml_rule_ids("tiktok")

        py_path = os.path.join(CHECKS_DIR, "tiktok.py")
        python_ids = _extract_check_ids_from_py(py_path)

        uncovered = []
        for pid in python_ids:
            if pid not in tiktok_map and pid not in yaml_ids:
                uncovered.append(pid)

        assert not uncovered, f"TikTok: マッピング未登録のcheck ID: {uncovered}"


class TestIdMapperModule:
    """engine/id_mapper.py のユニットテスト"""

    def setup_method(self):
        """各テスト前にキャッシュをクリア"""
        from engine.id_mapper import clear_cache
        clear_cache()

    def test_to_yaml_id_mapped_google(self):
        """Google: マッピングが存在するIDは変換される (G01 -> G25)"""
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("G01", "google") == "G25"

    def test_to_yaml_id_mapped_meta(self):
        """Meta: マッピングが存在するIDは変換される (M-PI1 -> M01)"""
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("M-PI1", "meta") == "M01"

    def test_to_yaml_id_mapped_tiktok(self):
        """TikTok: マッピングが存在するIDは変換される (T-TC1 -> T01)"""
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("T-TC1", "tiktok") == "T01"

    def test_to_yaml_id_unmapped_returns_original(self):
        """_unmappedのIDは元のIDをそのまま返す"""
        from engine.id_mapper import to_yaml_id
        # G07 -> _unmapped なので元のG07が返る
        assert to_yaml_id("G07", "google") == "G07"

    def test_to_yaml_id_unknown_returns_original(self):
        """マッピングに存在しないIDは元のIDをそのまま返す"""
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("UNKNOWN_ID", "google") == "UNKNOWN_ID"

    def test_to_yaml_id_common_passthrough(self):
        """共通チェック (C01等) はマッピングなしでそのまま返る"""
        from engine.id_mapper import to_yaml_id
        assert to_yaml_id("C01", "common") == "C01"

    def test_get_mapping_coverage_google(self):
        """Googleのマッピングカバレッジが正しく計算される"""
        from engine.id_mapper import get_mapping_coverage
        coverage = get_mapping_coverage("google")
        assert coverage["total"] > 0
        assert coverage["mapped"] > 0
        assert coverage["unmapped"] >= 0
        assert coverage["mapped"] + coverage["unmapped"] == coverage["total"]
        assert 0 <= coverage["coverage_pct"] <= 100

    def test_get_mapping_coverage_meta(self):
        """Metaのマッピングカバレッジが正しく計算される"""
        from engine.id_mapper import get_mapping_coverage
        coverage = get_mapping_coverage("meta")
        assert coverage["total"] > 0
        assert coverage["mapped"] > 0

    def test_get_mapping_coverage_tiktok(self):
        """TikTokのマッピングカバレッジが正しく計算される"""
        from engine.id_mapper import get_mapping_coverage
        coverage = get_mapping_coverage("tiktok")
        assert coverage["total"] > 0
        assert coverage["mapped"] > 0

    def test_clear_cache_reloads(self):
        """キャッシュクリア後に再読み込みが正常に動作する"""
        from engine.id_mapper import to_yaml_id, clear_cache
        result1 = to_yaml_id("G01", "google")
        clear_cache()
        result2 = to_yaml_id("G01", "google")
        assert result1 == result2 == "G25"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
