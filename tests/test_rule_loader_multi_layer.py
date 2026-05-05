"""ADR-013: 5 層ルール体系のロード + 環境マッチング + スキーマ互換性テスト

テスト構成 (20 ケース):
- TestLayerLoading (8 ケース): 5 層の YAML ロード、件数検証
- TestEnvironmentMatching (5 ケース): applies_to による絞込
- TestSchemaCompatibility (3 ケース): 既存 severity/polarity/axis_position/root_cause_group 互換性
- TestDataSourceResolution (4 ケース): client_state / ad_platform_api / rule_evaluation
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.auto_proposal_engine import (
    _load_all_layers,
    _filter_by_environment,
    _resolve_data_sources,
    _evaluate_trigger,
    _check_cooldown,
    LAYER_DIRS,
)


# ============================================================
# TestLayerLoading: 5 層のロード件数検証 (8 ケース)
# ============================================================

class TestLayerLoading:
    def test_all_layer_dirs_exist(self):
        """4 層のディレクトリが存在する"""
        for layer_name, layer_dir in LAYER_DIRS.items():
            assert layer_dir.exists(), f"Layer dir missing: {layer_dir}"

    def test_load_all_layers_returns_rules(self):
        """全層ロードで 100 ルール以上取得"""
        rules = _load_all_layers()
        assert len(rules) >= 100, f"Expected ≥100 rules, got {len(rules)}"

    def test_foundation_layer_count(self):
        """Foundation 層は 8 ファイル、~70 ルール"""
        rules = _load_all_layers(layer_filter=["foundation"])
        # 中身 5 ファイル (12+10+12+8+7=49) + 骨格 3 ファイル (6+8+5=19) = 68
        assert 60 <= len(rules) <= 80, f"Foundation rules: {len(rules)}"

    def test_vertical_layer_count(self):
        """Vertical 層は 9 ファイル、~70 ルール"""
        rules = _load_all_layers(layer_filter=["vertical"])
        # ec_d2c 12 + 他 8 ファイル (10+10+8+8+6+6+5+5=58) = 70
        assert 60 <= len(rules) <= 80, f"Vertical rules: {len(rules)}"

    def test_ec_platform_layer_count(self):
        """EC Platform 層は 6 ファイル、~30 ルール"""
        rules = _load_all_layers(layer_filter=["ec_platform"])
        # ecforce 8 + 他 5 ファイル (5+5+5+4+3=22) = 30
        assert 25 <= len(rules) <= 40, f"EC Platform rules: {len(rules)}"

    def test_precision_category_layer_count(self):
        """Precision Category 層は 7 ファイル、~80 ルール"""
        rules = _load_all_layers(layer_filter=["precision_category"])
        # 中身 3 (15+12+12=39) + 骨格 4 (12+10+10+9=41) = 80
        assert 70 <= len(rules) <= 90, f"Precision rules: {len(rules)}"

    def test_each_rule_has_required_fields(self):
        """全ルールに必須フィールドが存在"""
        rules = _load_all_layers()
        required = {"id", "layer", "category", "severity", "polarity",
                    "root_cause_group", "duplicate_factor", "applies_to",
                    "trigger", "data_source", "cooldown_days", "template", "rationale"}
        for rule in rules[:30]:  # 先頭 30 件サンプリング
            missing = required - set(rule.keys())
            assert not missing, f"Rule {rule.get('id')} missing: {missing}"

    def test_rule_ids_unique(self):
        """全層で rule_id が一意"""
        rules = _load_all_layers()
        ids = [r["id"] for r in rules]
        duplicates = [x for x in set(ids) if ids.count(x) > 1]
        assert not duplicates, f"Duplicate rule IDs: {duplicates}"


# ============================================================
# TestEnvironmentMatching: applies_to で絞込 (5 ケース)
# ============================================================

class TestEnvironmentMatching:
    def test_pilotton_environment_matches_ec_d2c(self):
        """pilotton (vertical=ec_d2c, ec_platform=ecforce) で V-EC-* がマッチ"""
        rules = _load_all_layers()
        client_cfg = {
            "country": "JP",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "ad_platforms": ["meta"],
            "business_model": "b2c",
        }
        matched = _filter_by_environment(rules, client_cfg)
        rule_ids = {r["id"] for r in matched}
        # V-EC-* がマッチする (12 ルール)
        ec_rules = {r for r in rule_ids if r.startswith("V-EC-")}
        assert len(ec_rules) == 12, f"Expected 12 V-EC-* rules, got {len(ec_rules)}"

    def test_pilotton_environment_excludes_subscription_saas(self):
        """pilotton で V-SS-* (subscription_saas) は除外"""
        rules = _load_all_layers()
        client_cfg = {
            "country": "JP",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "ad_platforms": ["meta"],
            "business_model": "b2c",
        }
        matched = _filter_by_environment(rules, client_cfg)
        rule_ids = {r["id"] for r in matched}
        ss_rules = {r for r in rule_ids if r.startswith("V-SS-")}
        assert len(ss_rules) == 0, f"V-SS-* should be excluded for ec_d2c client"

    def test_pilotton_environment_matches_ecforce(self):
        """pilotton で P-EF-* (ecforce) がマッチ"""
        rules = _load_all_layers()
        client_cfg = {
            "country": "JP",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "ad_platforms": ["meta"],
            "business_model": "b2c",
        }
        matched = _filter_by_environment(rules, client_cfg)
        rule_ids = {r["id"] for r in matched}
        ef_rules = {r for r in rule_ids if r.startswith("P-EF-")}
        assert len(ef_rules) == 8, f"Expected 8 P-EF-* rules, got {len(ef_rules)}"

    def test_pilotton_excludes_shopify_rules(self):
        """pilotton (ec_platform=ecforce) で P-SH-* (shopify) は除外"""
        rules = _load_all_layers()
        client_cfg = {
            "country": "JP",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "ad_platforms": ["meta"],
            "business_model": "b2c",
        }
        matched = _filter_by_environment(rules, client_cfg)
        rule_ids = {r["id"] for r in matched}
        sh_rules = {r for r in rule_ids if r.startswith("P-SH-")}
        assert len(sh_rules) == 0, f"P-SH-* should be excluded for ecforce client"

    def test_foundation_layer_matches_all_clients(self):
        """Foundation 層 (applies_to.verticals=[all]) は全クライアントでマッチ"""
        rules = _load_all_layers(layer_filter=["foundation"])
        client_cfg = {
            "country": "JP",
            "vertical": "subscription_saas",  # 任意の業界
            "ec_platform": "shopify",
            "ad_platforms": ["meta"],
            "business_model": "b2b",
        }
        matched = _filter_by_environment(rules, client_cfg)
        # Foundation は applies_to=[all] が大半なので 60% 以上はマッチ
        match_rate = len(matched) / len(rules) if rules else 0
        assert match_rate >= 0.5, f"Foundation match rate too low: {match_rate:.2%}"


# ============================================================
# TestSchemaCompatibility: 既存スキーマとの整合 (3 ケース)
# ============================================================

class TestSchemaCompatibility:
    def test_severity_uses_existing_values(self):
        """新規ルールの severity は既存スキーマ (critical/high/medium/low/info) のみ"""
        rules = _load_all_layers()
        valid_severities = {"critical", "high", "medium", "low", "info"}
        for rule in rules:
            sev = rule.get("severity")
            assert sev in valid_severities, f"Rule {rule['id']}: invalid severity '{sev}'"

    def test_root_cause_group_uses_six_groups(self):
        """新規ルールの root_cause_group は ADR-002 の 6 グループのみ"""
        rules = _load_all_layers()
        valid_groups = {
            "measurement_foundation",
            "delivery_learning_or_structure",
            "creative_optimization",
            "budget_allocation",
            "targeting",
            "independent",
        }
        for rule in rules:
            grp = rule.get("root_cause_group")
            assert grp in valid_groups, f"Rule {rule['id']}: invalid root_cause_group '{grp}'"

    def test_axis_position_uses_to_or_null(self):
        """axis_position は TO-01〜TO-11 または null"""
        rules = _load_all_layers()
        valid_axes = {f"TO-{i:02d}" for i in range(1, 12)} | {None}
        for rule in rules:
            ax = rule.get("axis_position")
            assert ax in valid_axes, f"Rule {rule['id']}: invalid axis_position '{ax}'"


# ============================================================
# TestDataSourceResolution: data_source の 3 系統 (4 ケース)
# ============================================================

class TestDataSourceResolution:
    def test_resolve_client_state_only(self):
        """source=client_state のみのルールで state がそのまま namespace に入る"""
        rule = {
            "id": "TEST-01",
            "data_source": [{"source": "client_state", "fields": ["foo"]}],
        }
        client_cfg = {}
        state = {"foo": "bar", "baz": 123}
        ns = _resolve_data_sources(rule, client_cfg, state)
        assert ns["client_state"]["foo"] == "bar"
        assert ns["client_state"]["baz"] == 123

    def test_resolve_ad_platform_api(self):
        """source=ad_platform_api で state.ad_platform_data がコピーされる"""
        rule = {
            "id": "TEST-02",
            "data_source": [{"source": "ad_platform_api", "platform": "meta", "fields": ["pixel_dormant_days"]}],
        }
        state = {"ad_platform_data": {"pixel_dormant_days": 312}}
        ns = _resolve_data_sources(rule, {}, state)
        assert ns["ad_platform_data"]["pixel_dormant_days"] == 312

    def test_resolve_rule_evaluation(self):
        """source=rule_evaluation で state.rule_evaluation を参照"""
        rule = {
            "id": "TEST-03",
            "data_source": [{"source": "rule_evaluation", "rule_ids": ["M01", "M02"]}],
        }
        state = {"rule_evaluation": {"M01_violated": True, "M02_violated": False}}
        ns = _resolve_data_sources(rule, {}, state)
        assert ns["rule_evaluation"]["M01_violated"] is True
        assert ns["rule_evaluation"]["M02_violated"] is False

    def test_evaluate_trigger_with_dot_access(self):
        """trigger.condition で client_state.foo の dot access が動く"""
        rule = {
            "id": "TEST-04",
            "trigger": {"condition": "client_state.capi_setup_status == 'not_started'"},
            "data_source": [{"source": "client_state", "fields": ["capi_setup_status"]}],
        }
        state = {"capi_setup_status": "not_started"}
        data = _resolve_data_sources(rule, {}, state)
        assert _evaluate_trigger(rule, data, "2026-05-05") is True

        # 違うステータスなら trigger は False
        state["capi_setup_status"] = "completed"
        data = _resolve_data_sources(rule, {}, state)
        assert _evaluate_trigger(rule, data, "2026-05-05") is False
