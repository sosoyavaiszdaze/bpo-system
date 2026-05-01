"""Phase 3 新規ルール (M66-M70) の単体テスト。"""
import pytest

from analyzers.checks.meta import (
    _check_m66_ad_lp_alignment,
    _check_m67_lp_reverse_generation,
    _check_m68_learning_reset_events,
    _check_m69_advantage_plus_exclusions,
    _check_m70_lla_seed_ltv_focus,
    _jaccard_similarity,
    run_meta_checks,
)


def _camp(**overrides):
    base = {"platform": "meta", "campaign": "Test Campaign"}
    base.update(overrides)
    return base


# === Helper ===

def test_jaccard_similarity_identical():
    assert _jaccard_similarity("foo bar baz", "foo bar baz") == 1.0


def test_jaccard_similarity_disjoint():
    assert _jaccard_similarity("foo bar", "baz qux") == 0.0


def test_jaccard_similarity_partial_overlap():
    score = _jaccard_similarity("foo bar baz", "foo qux")
    assert 0.0 < score < 1.0


def test_jaccard_similarity_empty():
    assert _jaccard_similarity("", "anything") == 0.0
    assert _jaccard_similarity("anything", "") == 0.0


# === M66: 広告-LP メッセージ整合スコア ===

def test_m66_skip_when_data_missing():
    """データ不足時は emit しない (graceful skip)。"""
    camps = [_camp(ad_creative_text="", landing_page_text="")]
    results = _check_m66_ad_lp_alignment(camps, {})
    assert results == []


def test_m66_pass_when_high_alignment():
    """両側で空白区切りされた整合度の高いテキストは pass。

    PoC 版 Jaccard は単語境界 (`\\w+`) で分割するため、日本語は事前に空白区切り
    されたテキストを前提とする (本番は sentence-transformers + 形態素解析に置換)。
    """
    camps = [_camp(
        ad_creative_text="送料 無料 革靴 職人 手作り 上質 仕上げ",
        landing_page_text="送料 無料 革靴 職人 手作り 上質 仕上げ",
    )]
    results = _check_m66_ad_lp_alignment(camps, {})
    assert len(results) == 1
    assert results[0]["id"] == "M66"
    assert results[0]["passed"] is True
    assert results[0]["context"]["alignment_score"] >= 0.6


def test_m66_fail_when_low_alignment():
    camps = [_camp(
        ad_creative_text="送料無料 革靴 職人",
        landing_page_text="今だけ 50% OFF セール 開催中",
    )]
    results = _check_m66_ad_lp_alignment(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["context"]["alignment_score"] < 0.6


def test_m66_threshold_override_via_thresholds():
    camps = [_camp(
        ad_creative_text="foo bar baz",
        landing_page_text="foo bar qux",  # Jaccard ≒ 0.5
    )]
    # 閾値を 0.4 に下げると pass する
    results = _check_m66_ad_lp_alignment(camps, {"ad_lp_alignment_threshold": 0.4})
    assert results[0]["passed"] is True


# === M67: 勝ち広告 LP 逆生成プロセス ===

def test_m67_skip_when_no_lp_data():
    camps = [_camp(landing_page_text="")]
    results = _check_m67_lp_reverse_generation(camps, {})
    assert results == []


def test_m67_pass_when_process_declared():
    camps = [_camp(landing_page_text="any LP", lp_reverse_generation_enabled=True)]
    results = _check_m67_lp_reverse_generation(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is True


def test_m67_fail_when_lp_exists_but_process_not_declared():
    camps = [_camp(landing_page_text="any LP", lp_reverse_generation_enabled=False)]
    results = _check_m67_lp_reverse_generation(camps, {})
    assert results[0]["passed"] is False


# === M68: 学習リセット要因イベント検出 ===

def test_m68_skip_when_data_unavailable():
    camps = [_camp()]  # recent_significant_edits なし
    results = _check_m68_learning_reset_events(camps, {})
    assert results == []


def test_m68_fail_during_learning_with_edits():
    camps = [_camp(learning_phase_active=True, recent_significant_edits=2)]
    results = _check_m68_learning_reset_events(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is False
    assert results[0]["context"]["edit_count"] == 2


def test_m68_pass_when_learning_with_no_edits():
    camps = [_camp(learning_phase_active=True, recent_significant_edits=0)]
    results = _check_m68_learning_reset_events(camps, {})
    # 学習中で 0件は emit せず (健全状態)
    assert results == []


def test_m68_pass_when_not_in_learning():
    camps = [_camp(learning_phase_active=False, recent_significant_edits=5)]
    results = _check_m68_learning_reset_events(camps, {})
    # 学習外ならば編集回数は問題視しない
    assert results == []


# === M69: Advantage+ 文脈の除外オーディエンス ===

def test_m69_skip_when_not_advantage_plus():
    camps = [_camp()]
    results = _check_m69_advantage_plus_exclusions(camps, {})
    assert results == []


def test_m69_fail_when_advantage_plus_but_no_exclusions():
    camps = [_camp(advantage_plus=True, audience_exclusions=[])]
    results = _check_m69_advantage_plus_exclusions(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is False


def test_m69_pass_when_advantage_plus_with_exclusions():
    camps = [_camp(advantage_plus=True,
                   excluded_custom_audiences=["existing_customers", "vip_list"])]
    results = _check_m69_advantage_plus_exclusions(camps, {})
    assert results[0]["passed"] is True
    assert results[0]["context"]["excluded_count"] == 2


def test_m69_uses_advantage_targeting_field():
    """advantage_targeting フィールドでも検出される。"""
    camps = [_camp(advantage_targeting=True, audience_exclusions=["customers"])]
    results = _check_m69_advantage_plus_exclusions(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is True


# === M70: LLA seed の LTV Top 層集中度 ===

def test_m70_skip_when_no_lla():
    camps = [_camp()]
    results = _check_m70_lla_seed_ltv_focus(camps, {})
    assert results == []


def test_m70_pass_when_seed_name_has_ltv_keyword():
    camps = [_camp(lookalike_percentage=1, lookalike_seed_name="VIP_Top_Customers")]
    results = _check_m70_lla_seed_ltv_focus(camps, {})
    assert len(results) == 1
    assert results[0]["passed"] is True


def test_m70_fail_when_seed_name_lacks_ltv_keyword():
    camps = [_camp(lookalike_percentage=3, lookalike_seed_name="all_purchasers_180d")]
    results = _check_m70_lla_seed_ltv_focus(camps, {})
    assert results[0]["passed"] is False


def test_m70_skip_when_lla_used_but_seed_name_missing():
    camps = [_camp(lookalike_percentage=2)]  # seed 名なし → 判定不能
    results = _check_m70_lla_seed_ltv_focus(camps, {})
    assert results == []


def test_m70_japanese_keyword_detection():
    camps = [_camp(lookalike_percentage=1, lookalike_seed_name="高LTV_顧客リスト")]
    results = _check_m70_lla_seed_ltv_focus(camps, {})
    assert results[0]["passed"] is True


# === 統合: run_meta_checks 経由で M66-M70 が emit されることを確認 ===

def test_run_meta_checks_includes_phase3_rules():
    """run_meta_checks 経由で Phase 3 新規ルールが評価される。"""
    camps = [_camp(
        ad_creative_text="送料無料 革靴",
        landing_page_text="送料無料 革靴",
        lp_reverse_generation_enabled=True,
        learning_phase_active=True,
        recent_significant_edits=1,
        advantage_plus=True,
        audience_exclusions=["existing"],
        lookalike_percentage=1,
        lookalike_seed_name="VIP_Top_Buyers",
    )]
    results = run_meta_checks(camps, thresholds={"meta": {}}, pixel_status={})
    rule_ids = {r["id"] for r in results}
    assert {"M66", "M67", "M68", "M69", "M70"}.issubset(rule_ids), \
        f"Phase 3 ルール欠落: 検出={rule_ids & {'M66','M67','M68','M69','M70'}}"
