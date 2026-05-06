"""ADR-015 client tech stack validator + evaluator 拡張のテスト"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validators.client_tech_stack_validator import (
    score_signals, detect_stack_from_html, _signal_matches, _diagnose,
)
from engine.auto_proposal_engine import _filter_by_environment


# ============================================================
# score_signals: ADR-015 §2.2 修正版合算ロジック (cookie 残存問題対策)
# ============================================================

class TestScoreSignals:
    """合算ロジック: weak は medium/strong との同時ヒットでのみ medium に昇格"""

    def test_strong_alone_returns_high(self):
        assert score_signals(1, 0, 0) == "high"

    def test_two_medium_returns_medium(self):
        assert score_signals(0, 2, 0) == "medium"

    def test_one_medium_one_weak_promotes_to_medium(self):
        """1 medium + 1 weak → effective 2 → medium 昇格"""
        assert score_signals(0, 1, 1) == "medium"

    def test_one_medium_two_weak_returns_medium(self):
        assert score_signals(0, 1, 2) == "medium"

    def test_one_medium_alone_returns_low(self):
        assert score_signals(0, 1, 0) == "low"

    def test_weak_alone_returns_unknown(self):
        """weak のみ (cookie 残存問題) → 未検出扱い"""
        assert score_signals(0, 0, 1) == "unknown"

    def test_multiple_weak_alone_returns_unknown(self):
        """weak 複数でも単独なら未検出"""
        assert score_signals(0, 0, 3) == "unknown"

    def test_no_signals_returns_unknown(self):
        assert score_signals(0, 0, 0) == "unknown"

    def test_strong_overrides_everything(self):
        assert score_signals(2, 1, 5) == "high"


# ============================================================
# _signal_matches: 6 種類のシグナル prefix を解釈できるか
# ============================================================

class TestSignalMatches:
    def test_html_contains(self):
        probed = {"html": "<script src='https://googletagmanager.com/gtm.js'></script>"}
        assert _signal_matches("html_contains:googletagmanager.com/gtm.js", probed)

    def test_html_contains_negative(self):
        probed = {"html": "no relevant content"}
        assert not _signal_matches("html_contains:foo.bar.baz", probed)

    def test_http_header(self):
        probed = {"headers": {"x-shopid": "12345"}}
        assert _signal_matches("http_header:X-ShopId", probed)

    def test_http_header_case_insensitive(self):
        probed = {"headers": {"x-shopid": "12345"}}
        assert _signal_matches("http_header:x-shopid", probed)

    def test_cookie(self):
        probed = {"cookies": ["_shopify_y", "session_id"]}
        assert _signal_matches("cookie:_shopify_y", probed)

    def test_cookie_negative(self):
        probed = {"cookies": ["session_id"]}
        assert not _signal_matches("cookie:_shopify_y", probed)

    def test_js_global(self):
        probed = {"html": "<script>window.Shopify = {};</script>"}
        assert _signal_matches("js_global:Shopify", probed)

    def test_domain_regex(self):
        probed = {"final_url": "https://shop.myshopify.com/products/foo"}
        assert _signal_matches(r"domain_regex:\.myshopify\.com$", probed)

    def test_url_path_regex(self):
        probed = {"final_url": "https://lp.ec-force.com/lp?u=abc123"}
        assert _signal_matches(r"url_path_regex:/lp\?u=", probed)


# ============================================================
# detect_stack_from_html: 統合検出 (mock probed dict 使用)
# ============================================================

SIGNATURES = {
    "ec_platform": {
        "shopify": {
            "strong": ["domain_regex:\\.myshopify\\.com$"],
            "medium": ["html_contains:cdn.shopify.com"],
            "weak":   ["cookie:_shopify_y"],
        },
        "ecforce": {
            "strong": ["domain_regex:\\.ec-force\\.com$"],
            "medium": ["html_contains:ec-force"],
        },
    },
    "tag_manager": {
        "gtm": {
            "strong": ["html_contains:googletagmanager.com/gtm.js"],
            "medium": ["js_global:dataLayer"],
        },
    },
}


class TestDetectStackFromHtml:
    def test_shopify_strong_match(self):
        probed = {
            "html": "",
            "headers": {},
            "cookies": [],
            "final_url": "https://shop.myshopify.com/",
        }
        result = detect_stack_from_html(probed, signatures=SIGNATURES)
        assert result["ec_platform"]["detected"] == "shopify"
        assert result["ec_platform"]["confidence"] == "high"

    def test_cookie_alone_not_detected(self):
        """cookie だけマッチするケースは weak のみ → unknown 扱い"""
        probed = {
            "html": "",
            "headers": {},
            "cookies": ["_shopify_y"],     # weak のみ
            "final_url": "https://example.com/",
        }
        result = detect_stack_from_html(probed, signatures=SIGNATURES)
        assert result["ec_platform"]["detected"] is None  # 未検出
        assert result["ec_platform"]["confidence"] == "unknown"

    def test_medium_plus_weak_promotion(self):
        """medium 1 + weak 1 → medium 昇格、shopify が検出される"""
        probed = {
            "html": "<link href='https://cdn.shopify.com/s/foo.css'>",
            "headers": {},
            "cookies": ["_shopify_y"],
            "final_url": "https://example.com/",   # myshopify.com ではない
        }
        result = detect_stack_from_html(probed, signatures=SIGNATURES)
        assert result["ec_platform"]["detected"] == "shopify"
        assert result["ec_platform"]["confidence"] == "medium"


# ============================================================
# _diagnose: 4 状態判定
# ============================================================

class TestDiagnose:
    def test_match(self):
        det = {"detected": "ecforce", "confidence": "high"}
        assert _diagnose(det, "ecforce", "ec_platform") == "match"

    def test_detected_only(self):
        det = {"detected": "shopify", "confidence": "high"}
        assert _diagnose(det, None, "ec_platform") == "detected_only"

    def test_declared_only(self):
        det = {"detected": None, "confidence": "unknown"}
        assert _diagnose(det, "ecforce", "ec_platform") == "declared_only"

    def test_mismatch(self):
        det = {"detected": "shopify", "confidence": "high"}
        assert _diagnose(det, "ecforce", "ec_platform") == "mismatch"

    def test_pending_when_both_unknown(self):
        det = {"detected": None, "confidence": "unknown"}
        assert _diagnose(det, None, "ec_platform") == "pending"


# ============================================================
# evaluator フェイルセーフ: confidence:low なルールはスキップ
# ============================================================

class TestEvaluatorFailsafe:
    """auto_proposal_engine._filter_by_environment が tech_stack 依存ルールを正しく扱えるか"""

    def _client_cfg_pilotton_low_confidence(self):
        return {
            "country": "JP",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "ad_platforms": ["meta"],
            "business_model": "b2c",
            "tech_stack": {
                "ec_platform": {"value": "ecforce", "confidence": "high"},
                "tag_manager": {"value": "unknown", "confidence": "low"},
                "ma":          {"value": "unknown", "confidence": "low"},
                "crm":         {"value": "unknown", "confidence": "low"},
                "capi_status": {"meta": "not_configured"},
            },
        }

    def test_no_applies_to_extension_passes(self):
        """既存ルール (新カテゴリ未指定) は影響を受けない"""
        rule = {"id": "TEST-1", "applies_to": {}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule in _filter_by_environment([rule], client_cfg)

    def test_ma_dependent_rule_skipped_when_low_confidence(self):
        """ma:hubspot を要求するルールは confidence:low のためスキップ"""
        rule = {"id": "TEST-MA", "applies_to": {"mas": ["hubspot"]}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule not in _filter_by_environment([rule], client_cfg)

    def test_crm_dependent_rule_skipped(self):
        rule = {"id": "TEST-CRM", "applies_to": {"crms": ["salesforce"]}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule not in _filter_by_environment([rule], client_cfg)

    def test_capi_status_match_passes(self):
        """capi_status: {meta: not_configured} を要求するルールは tech_stack と一致 → IN"""
        rule = {"id": "TEST-CAPI-OFF", "applies_to": {"capi_status": {"meta": "not_configured"}}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule in _filter_by_environment([rule], client_cfg)

    def test_capi_status_mismatch_filtered(self):
        """capi_status: {meta: enabled} を要求 → 一致せず OUT"""
        rule = {"id": "TEST-CAPI-ON", "applies_to": {"capi_status": {"meta": "enabled"}}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule not in _filter_by_environment([rule], client_cfg)

    def test_high_confidence_ec_platform_passes(self):
        """ec_platform は confidence:high 宣言なので通常マッチ"""
        rule = {"id": "TEST-EC", "applies_to": {"ec_platforms": ["ecforce"]}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule in _filter_by_environment([rule], client_cfg)

    def test_ec_platform_mismatch_filtered(self):
        rule = {"id": "TEST-EC-WRONG", "applies_to": {"ec_platforms": ["shopify"]}}
        client_cfg = self._client_cfg_pilotton_low_confidence()
        assert rule not in _filter_by_environment([rule], client_cfg)
