"""全YAMLルールのスキーマ検証・カバレッジテスト"""
import os
import sys
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "rules")

VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_POLARITIES = {"neutral", "preserve", "monitor_only", "aggregate",
                    "separate", "open", "context_dependent", "budget_first"}
VALID_POSITIONS = {"left", "right", "neutral"}

YAML_FILES = [
    ("google_rules.yaml", 108),
    ("meta_rules.yaml", 65),
    ("tiktok_rules.yaml", 46),
    ("seo_rules.yaml", 45),
    ("adtruth_rules.yaml", 15),
]


def _load_yaml(filename):
    """YAMLファイルを読み込み"""
    path = os.path.join(RULES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("filename,expected_count", YAML_FILES)
def test_yaml_total_rule_count(filename, expected_count):
    """各YAMLファイルの総ルール数が仕様通りか"""
    data = _load_yaml(filename)
    total = len(data["rules"])
    assert total == expected_count, f"{filename}: expected {expected_count}, got {total}"


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_no_duplicate_ids(filename, _):
    """ID重複がないか"""
    data = _load_yaml(filename)
    ids = [r["id"] for r in data["rules"]]
    duplicates = [x for x in set(ids) if ids.count(x) > 1]
    assert not duplicates, f"Duplicate IDs: {duplicates}"


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_required_fields_present(filename, _):
    """必須フィールドが全ルールに存在するか"""
    data = _load_yaml(filename)
    required = {"id", "name", "category", "severity", "weight", "platform"}
    errors = []
    for rule in data["rules"]:
        missing = required - set(rule.keys())
        if missing:
            errors.append(f"{rule.get('id', '?')}: missing {missing}")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_valid_severity_values(filename, _):
    """severityが有効な値か"""
    data = _load_yaml(filename)
    errors = []
    for rule in data["rules"]:
        if rule.get("severity") not in VALID_SEVERITIES:
            errors.append(f"{rule['id']}: invalid severity '{rule.get('severity')}'")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_valid_polarity_values(filename, _):
    """polarityが有効な値か"""
    data = _load_yaml(filename)
    errors = []
    for rule in data["rules"]:
        pol = rule.get("polarity", "neutral")
        if pol not in VALID_POLARITIES:
            errors.append(f"{rule['id']}: invalid polarity '{pol}'")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_valid_axis_position(filename, _):
    """axis_positionが有効な値か"""
    data = _load_yaml(filename)
    errors = []
    for rule in data["rules"]:
        pos = rule.get("axis_position", "neutral")
        if pos not in VALID_POSITIONS:
            errors.append(f"{rule['id']}: invalid axis_position '{pos}'")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("filename,_", YAML_FILES)
def test_prerequisite_ids_exist(filename, _):
    """prerequisiteに指定されたIDが同YAML内に存在するか"""
    data = _load_yaml(filename)
    all_ids = {r["id"] for r in data["rules"]}
    missing = []
    for rule in data["rules"]:
        for prereq in rule.get("prerequisite", []):
            if prereq not in all_ids:
                missing.append(f"{rule['id']} → {prereq}")
    assert not missing, "Missing prerequisite targets:\n" + "\n".join(missing)


def test_tradeoff_axes_11():
    """トレードオフ軸が11件"""
    data = _load_yaml("tradeoff_axes.yaml")
    assert len(data["axes"]) == 11


def test_category_weights_exist():
    """各YAMLにcategory_weightsが定義されている"""
    for filename, _ in YAML_FILES:
        data = _load_yaml(filename)
        assert "category_weights" in data, f"{filename}: missing category_weights"
        assert len(data["category_weights"]) > 0, f"{filename}: empty category_weights"


class TestRuleCoverage:
    """rule_coverage モジュールのテスト"""

    def test_analyze_coverage(self):
        """カバレッジ分析が動作する"""
        from engine.rule_coverage import analyze_coverage
        checks = [
            {"id": "G01", "passed": True, "platform": "google"},
            {"id": "ORPHAN_CHECK", "passed": False, "platform": "google"},
        ]
        report = analyze_coverage(checks)
        assert report["total_yaml_rules"] > 0
        assert report["covered"] >= 1
        assert "ORPHAN_CHECK" in report["orphan_ids"]
        assert report["coverage_percent"] < 100

    def test_empty_checks(self):
        """空のチェック結果"""
        from engine.rule_coverage import analyze_coverage
        report = analyze_coverage([])
        assert report["covered"] == 0
        assert report["uncovered"] > 0
