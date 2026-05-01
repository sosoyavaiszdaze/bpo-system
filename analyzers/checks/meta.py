"""Meta Ads チェック — 既存34ルール + Phase 3 新規5ルール (M66-M70)。"""
import logging
import re

log = logging.getLogger("bpo")


def run_meta_checks(campaigns, thresholds, pixel_status=None):
    """Meta Ads 固有チェック実行"""
    meta_camps = [c for c in campaigns if c.get("platform", "").lower() == "meta"]
    if not meta_camps:
        return []

    m_t = thresholds.get("meta", {})
    results = []

    results.extend(_check_pixel_capi(meta_camps, pixel_status))
    results.extend(_check_creative(meta_camps, m_t))
    results.extend(_check_structure(meta_camps, m_t))
    results.extend(_check_audience(meta_camps, m_t))
    results.extend(_check_campaign_config(meta_camps, m_t))
    results.extend(_check_phase3_new_rules(meta_camps, m_t))

    return results


# ========================================
# Pixel / CAPI (M-PI1 ~ M-PI8)
# ========================================
def _check_pixel_capi(camps, pixel_status):
    results = []
    ps = pixel_status or {}

    # M-PI1: Pixel 設置
    results.append(_r("M01", ps.get("pixel_installed", False), "アカウント全体",
                       "" if ps.get("pixel_installed") else "Meta Pixel 未設置"))

    # M-PI2: EMQ ≥ 6
    emq = ps.get("event_match_quality")
    if emq is not None:
        results.append(_r("M03", emq >= 6, "アカウント全体",
                          "" if emq >= 6 else f"EMQ {emq:.1f} (推奨: ≥6.0)"))
    else:
        results.append(_r("M03", False, "アカウント全体", "EMQ不明 — 確認推奨"))

    # M-PI3: CAPI 有効
    results.append(_r("M02", ps.get("capi_enabled", False), "アカウント全体",
                       "" if ps.get("capi_enabled") else "Conversions API 未有効"))

    # M-PI4: イベントパラメータ（必須イベントの送信状況）
    events_configured = ps.get("standard_events_count", 0)
    results.append(_r("M05", events_configured >= 3, "アカウント全体",
                       "" if events_configured >= 3 else f"標準イベント {events_configured}個 (推奨≥3: Purchase, Lead, AddToCart)"))

    # M-PI5: CAPI + Pixelの重複排除
    dedup = ps.get("deduplication_enabled", False)
    if ps.get("capi_enabled"):
        results.append(_r("M06", dedup, "アカウント全体",
                          "" if dedup else "CAPI+Pixel 重複排除未設定 — CV二重計上リスク"))

    # M-PI6: Aggregated Event Measurement
    aem = ps.get("aggregated_event_measurement", None)
    if aem is not None:
        results.append(_r("M56", aem, "アカウント全体",
                          "" if aem else "Aggregated Event Measurement 未構成"))

    # M-PI7: ドメイン検証
    domain_verified = ps.get("domain_verified", None)
    if domain_verified is not None:
        results.append(_r("M04", domain_verified, "アカウント全体",
                          "" if domain_verified else "ドメイン検証未完了"))

    # M-PI8: iOS ATT オプトイン率
    att_rate = ps.get("att_opt_in_rate", None)
    if att_rate is not None:
        results.append(_r("M08", att_rate >= 20, "アカウント全体",
                          "" if att_rate >= 20 else f"ATTオプトイン率 {att_rate:.0f}% (業界平均: 25-30%)"))

    return results


# ========================================
# Creative (M-CR1 ~ M-CR6)
# ========================================
def _check_creative(camps, m_t):
    results = []
    creative_t = m_t.get("creative", {})

    # M-CR1: クリエイティブ多様性（全体で≥10種）
    total_ads = sum(c.get("ad_count", 0) for c in camps)
    results.append(_r("M47", total_ads >= 10 or total_ads == 0, "全Meta",
                       "" if total_ads >= 10 or total_ads == 0 else f"クリエイティブ {total_ads}種 (推奨≥10)"))

    # M-CR2: 画像+動画の混合
    has_video = any(c.get("has_native_video", False) for c in camps)
    has_image = any(c.get("ad_count", 0) > 0 for c in camps)
    if has_image and not has_video:
        results.append(_r("M24", False, "全Meta",
                          "動画クリエイティブなし — 静止画+動画の混合推奨"))

    # M-CR3: フリークエンシー疲弊
    fatigue_freq = creative_t.get("fatigue_frequency", 3.5)
    for camp in camps:
        freq = camp.get("frequency", 0)
        if freq > fatigue_freq:
            results.append(_r("M57", False, camp["campaign"],
                              f"フリークエンシー疲弊: {freq:.1f} (閾値: {fatigue_freq})"))

    # M-CR4: クリエイティブ入替日数
    for camp in camps:
        days_active = camp.get("creative_days_active", 0)
        if days_active > 21:
            results.append(_r("M58", False, camp["campaign"],
                              f"クリエイティブ {days_active}日稼働 — 21日超でリフレッシュ推奨"))

    # M-CR5: UGC/Reels広告
    has_reels = any("reels" in c.get("campaign", "").lower() or c.get("has_reels", False) for c in camps)
    results.append(_r("M35", has_reels, "全Meta",
                       "" if has_reels else "Reels/UGC クリエイティブなし — エンゲージメント向上推奨"))

    # M-CR6: DCO（Dynamic Creative Optimization）
    dco_camps = [c for c in camps if c.get("dynamic_creative", False)]
    if camps and not dco_camps:
        results.append(_r("M59", False, "全Meta",
                          "Dynamic Creative未使用 — テスト効率化に推奨"))

    return results


# ========================================
# Structure (M-ST1 ~ M-ST7)
# ========================================
def _check_structure(camps, m_t):
    results = []
    struct_t = m_t.get("structure", {})
    learning_t = m_t.get("learning_phase", {})

    # M-ST1: キャンペーン構造（目的ベース分離）
    objectives = {}
    for c in camps:
        obj = c.get("objective", c.get("campaign_type", "unknown"))
        objectives.setdefault(obj, []).append(c)
    for obj, group in objectives.items():
        if len(group) > 3:
            results.append(_r("M14", False, f"{obj}グループ",
                              f"同一目的 '{obj}' に {len(group)} キャンペーン — 統合推奨"))

    # M-ST2: 広告セット数 ≤5/キャンペーン
    max_adsets = struct_t.get("max_adsets_per_campaign", 5)
    for camp in camps:
        adset_count = camp.get("adset_count", 0)
        if adset_count > max_adsets:
            results.append(_r("M15", False, camp["campaign"],
                              f"広告セット数 {adset_count} (上限: {max_adsets}) — CV学習分散リスク"))

    # M-ST3: 学習フェーズ ≥50CV/週
    min_weekly_cv = learning_t.get("min_weekly_conversions", 50)
    daily_min = min_weekly_cv / 7
    for camp in camps:
        cv = camp.get("conversions", 0)
        if cv < daily_min and camp.get("cost", 0) > 0:
            results.append(_r("M09", False, camp["campaign"],
                              f"学習フェーズ未達: 日次CV {cv:.1f} (週{min_weekly_cv}必要)",
                              conflict_group="learning_vs_testing"))

    # M-ST4: Advantage+ ショッピング
    for camp in camps:
        is_aplus = camp.get("advantage_plus", False)
        if is_aplus:
            results.append(_r("M44", True, camp["campaign"], "Advantage+ Shopping 使用中 ✓"))

    # M-ST5: CBO（Campaign Budget Optimization）
    for camp in camps:
        cbo = camp.get("campaign_budget_optimization", None)
        if cbo is not None:
            results.append(_r("M11", cbo, camp["campaign"],
                              "" if cbo else "CBO未有効 — 広告セット間の自動配分推奨"))

    # M-ST6: 最低予算チェック
    for camp in camps:
        budget = camp.get("daily_budget", 0)
        target_cpa = camp.get("target_cpa", camp.get("cpa", 0))
        if budget > 0 and target_cpa > 0:
            ratio = budget / target_cpa
            if ratio < 5:
                results.append(_r("M12", False, camp["campaign"],
                                  f"日予算がCPAの{ratio:.1f}倍 — 学習に最低5×CPA必要"))

    # M-ST7: Advantage+ クリエイティブ
    for camp in camps:
        aplus_creative = camp.get("advantage_creative", None)
        if aplus_creative is not None:
            results.append(_r("M60", aplus_creative, camp["campaign"],
                              "" if aplus_creative else "Advantage+ Creative未有効 — 自動最適化推奨"))

    return results


# ========================================
# Audience (M-AU1 ~ M-AU6)
# ========================================
def _check_audience(camps, m_t):
    results = []

    # M-AU1: オーバーラップ検出
    type_groups = {}
    for camp in camps:
        ct = camp.get("campaign_type", "other")
        type_groups.setdefault(ct, []).append(camp)
    for ct, group in type_groups.items():
        if len(group) > 2:
            results.append(_r("M49", False, f"{ct}グループ",
                              f"同一目的 '{ct}' に {len(group)} キャンペーン: オーバーラップリスク"))

    # M-AU2: カスタムオーディエンス
    has_custom = any(c.get("custom_audiences", []) for c in camps)
    results.append(_r("M51", has_custom, "全Meta",
                       "" if has_custom else "カスタムオーディエンス未設定"))

    # M-AU3: Lookalike品質
    for camp in camps:
        lal_pct = camp.get("lookalike_percentage", 0)
        if lal_pct > 0 and lal_pct > 5:
            results.append(_r("M50", False, camp["campaign"],
                              f"Lookalike {lal_pct}% — 精度重視なら1-3%推奨",
                              conflict_group="precision_vs_reach"))

    # M-AU4: 除外オーディエンス（既存顧客除外）
    has_exclusions = any(c.get("audience_exclusions", []) for c in camps)
    results.append(_r("M53", has_exclusions, "全Meta",
                       "" if has_exclusions else "除外オーディエンス未設定 — 既存顧客除外推奨"))

    # M-AU5: Advantage+ ターゲティング展開
    for camp in camps:
        aplus_targeting = camp.get("advantage_targeting", None)
        if aplus_targeting is not None:
            results.append(_r("M54", True, camp["campaign"],
                              f"Advantage+ ターゲティング {'有効' if aplus_targeting else '無効'}",
                              conflict_group="precision_vs_reach"))

    # M-AU6: ファーストパーティデータ活用
    has_first_party = any(c.get("first_party_data", False) for c in camps)
    results.append(_r("M61", has_first_party, "全Meta",
                       "" if has_first_party else "ファーストパーティデータ未活用"))

    return results


# ========================================
# Campaign Config (M-C01 ~ M-C06)
# ========================================
def _check_campaign_config(camps, m_t):
    results = []

    for camp in camps:
        name = camp.get("campaign", "")

        # M-C01: アトリビューション設定
        attr_window = camp.get("attribution_window", "")
        if attr_window:
            results.append(_r("M62", True, name, f"アトリビューション: {attr_window}"))
        else:
            results.append(_r("M62", False, name, "アトリビューション設定不明"))

        # M-C02: 配信最適化目標の妥当性
        opt_goal = camp.get("optimization_goal", "")
        obj = camp.get("objective", "")
        if obj and opt_goal:
            mismatch = False
            if "conversion" in obj.lower() and "impression" in opt_goal.lower():
                mismatch = True
            if "lead" in obj.lower() and "link_click" in opt_goal.lower():
                mismatch = True
            if mismatch:
                results.append(_r("M63", False, name,
                                  f"目的'{obj}'に対し最適化'{opt_goal}'が不適切"))

        # M-C03: コスト上限/入札上限
        cost_cap = camp.get("cost_cap", 0)
        bid_cap = camp.get("bid_cap", 0)
        if cost_cap == 0 and bid_cap == 0 and camp.get("cost", 0) > 0:
            results.append(_r("M45", False, name,
                              "コスト上限/入札上限未設定 — CPA暴騰リスク"))

        # M-C04: 地域ターゲティング精度
        geo_type = camp.get("geo_targeting_type", "")
        if "interested" in geo_type.lower():
            results.append(_r("M64", False, name,
                              "地域: 'People interested' → 'People living' に変更推奨"))

    # M-C05: アカウントレベル — 支払い方法
    if camps:
        payment = camps[0].get("payment_method", "unknown")
        results.append(_r("M65", payment != "unknown", "アカウント全体",
                          "" if payment != "unknown" else "支払い方法ステータス不明"))

    # M-C06: Business Manager検証
    if camps:
        bm_verified = camps[0].get("business_manager_verified", None)
        if bm_verified is not None:
            results.append(_r("M19", bm_verified, "アカウント全体",
                              "" if bm_verified else "Business Manager未検証 — 制限リスク"))

    return results


def _r(check_id, passed, campaign, message, conflict_group=None, context=None):
    """チェック結果 dict を構築"""
    r = {"id": check_id, "passed": passed, "campaign": campaign, "platform": "meta", "message": message}
    if conflict_group:
        r["conflict_group"] = conflict_group
    if context:
        r["context"] = context
    return r


# ========================================
# Phase 3 新規ルール (M66-M70) — 2026-05 追加
# PoC 段階: データが不足する場合は graceful skip し、本番運用で完全版に置換予定。
# ========================================

# M70 で seed 名から LTV 上位層キーワードを検出するための辞書
_LTV_SEED_KEYWORDS = (
    "top", "ltv", "vip", "premium", "high_value", "best",
    "高ltv", "高価値", "上位", "プレミアム", "優良",
)


def _check_phase3_new_rules(camps, m_t):
    """M-λ / M-β / M-δ / M-ι 系の Phase 3 新規ルール判定。

    各ルールは PoC 簡易版実装で、対応データが取得できない場合は graceful skip。
    本番版への置換ポイントは各セクションの NOTE: コメントを参照。
    """
    results = []
    results.extend(_check_m66_ad_lp_alignment(camps, m_t))
    results.extend(_check_m67_lp_reverse_generation(camps, m_t))
    results.extend(_check_m68_learning_reset_events(camps, m_t))
    results.extend(_check_m69_advantage_plus_exclusions(camps, m_t))
    results.extend(_check_m70_lla_seed_ltv_focus(camps, m_t))
    return results


def _check_m66_ad_lp_alignment(camps, m_t):
    """M66: 広告-LP メッセージ整合スコア。

    PoC 簡易版: 広告コピー (ad_creative_text) と LP コピー (landing_page_text) の
    単語 Jaccard 類似度を算出。閾値 0.6 以上で pass。
    NOTE: 本番は sentence-transformers の cosine similarity で多言語対応に置換予定。
    """
    results = []
    threshold = m_t.get("ad_lp_alignment_threshold", 0.6)
    for camp in camps:
        ad_text = (camp.get("ad_creative_text") or "").strip()
        lp_text = (camp.get("landing_page_text") or "").strip()
        if not ad_text or not lp_text:
            continue  # データ不足: skip
        score = _jaccard_similarity(ad_text, lp_text)
        passed = score >= threshold
        if passed:
            msg = f"広告-LP 整合スコア {score:.2f} (閾値≥{threshold:.2f}) ✓"
        else:
            msg = (f"広告-LP 整合スコア {score:.2f} < {threshold:.2f} — "
                   f"LP のヘッドラインを広告コピーに揃える改善余地あり")
        results.append(_r("M66", passed, camp.get("campaign", ""), msg,
                          context={"alignment_score": round(score, 3),
                                   "implementation": "poc_jaccard"}))
    return results


def _check_m67_lp_reverse_generation(camps, m_t):
    """M67: 勝ち広告 LP 逆生成プロセスの実施有無。

    PoC 簡易版: クライアント設定 or campaign 側の lp_reverse_generation_enabled
    フラグを参照。広告と LP のデータがあるが宣言が無い場合は警告 (低優先度)。
    NOTE: 本番は LP 制作日 vs 広告配信開始日の差分比較や、勝ち広告 → LP テキスト
    生成 AI パイプラインの稼働ログ検査に置換予定。
    """
    results = []
    has_lp_data = any(c.get("landing_page_text") for c in camps)
    if not has_lp_data:
        return results  # LP データ不在は skip
    process_declared = any(c.get("lp_reverse_generation_enabled") for c in camps)
    msg = ("LP 逆生成プロセス実施宣言済み ✓" if process_declared
           else "LP 逆生成プロセス未宣言 — 勝ち広告 → LP 逆算で CPA 改善余地")
    results.append(_r("M67", process_declared, "全Meta", msg,
                      context={"implementation": "poc_flag_based"}))
    return results


def _check_m68_learning_reset_events(camps, m_t):
    """M68: 学習リセット要因イベント検出。

    PoC 簡易版: campaign の recent_significant_edits フィールドを参照。
    学習中 (learning_phase_active) かつ significant edit が 1 件以上で fail。
    NOTE: 本番は Meta Marketing API の ad_set_changes endpoint から
    予算/ターゲット/最適化目標の直近14日変更履歴を取得して判定に置換予定。
    """
    results = []
    for camp in camps:
        edits = camp.get("recent_significant_edits", None)
        if edits is None:
            continue  # データ不足: skip
        learning = camp.get("learning_phase_active", False)
        if learning and edits > 0:
            results.append(_r("M68", False, camp.get("campaign", ""),
                              f"学習中に直近 significant edit {edits} 件検出 — 学習リセットリスク",
                              conflict_group="learning_vs_testing",
                              context={"edit_count": edits,
                                       "implementation": "poc_count_based"}))
    return results


def _check_m69_advantage_plus_exclusions(camps, m_t):
    """M69: Advantage+ 利用キャンペーンの除外オーディエンス設定有無。

    Advantage+ Audience を使う camp に対し excluded_custom_audiences (or
    audience_exclusions) が設定されているか確認。Broad 推奨環境では除外リストが
    唯一の制御手段となるため必須。
    """
    results = []
    for camp in camps:
        if not (camp.get("advantage_plus") or camp.get("advantage_targeting")):
            continue
        excluded = (camp.get("excluded_custom_audiences")
                    or camp.get("audience_exclusions")
                    or [])
        passed = bool(excluded)
        if passed:
            msg = f"除外オーディエンス {len(excluded)} 件設定済み ✓"
        else:
            msg = "Advantage+ 利用中だが除外オーディエンス未設定 — 既存購入者重複配信リスク"
        results.append(_r("M69", passed, camp.get("campaign", ""), msg,
                          context={"excluded_count": len(excluded),
                                   "implementation": "poc_field_check"}))
    return results


def _check_m70_lla_seed_ltv_focus(camps, m_t):
    """M70: LLA seed の LTV Top 層集中度。

    PoC 簡易版: lookalike_seed_name に LTV/Top/VIP 等のキーワードが含まれるかを
    確認。データ不足は skip。
    NOTE: 本番は CRM の LTV データ (Twenty 等) と seed リストを照合し、
    seed が LTV 上位 1〜5% に絞られているかを実測値で判定する実装に置換予定。
    """
    results = []
    for camp in camps:
        lal_pct = camp.get("lookalike_percentage", 0)
        seed_name = (camp.get("lookalike_seed_name") or "").strip()
        if lal_pct <= 0 and not seed_name:
            continue  # LLA 利用無し or データ不足: skip
        if not seed_name:
            continue  # seed 名が無いと判定不能
        seed_lower = seed_name.lower()
        has_ltv_kw = any(kw in seed_lower for kw in _LTV_SEED_KEYWORDS)
        if has_ltv_kw:
            msg = f"LLA seed '{seed_name}' に LTV 上位層キーワード検出 ✓"
        else:
            msg = (f"LLA seed '{seed_name}' に LTV 上位層キーワードなし — "
                   f"Top 1〜5% LTV 顧客を seed に推奨")
        results.append(_r("M70", has_ltv_kw, camp.get("campaign", ""), msg,
                          context={"seed_name": seed_name,
                                   "implementation": "poc_keyword_match"}))
    return results


def _jaccard_similarity(text1, text2):
    """単語ベースの Jaccard 類似度 (0.0〜1.0)。

    日本語含むテキスト前提のため `\\w+` トークナイズで形態素解析は行わない。
    本番は sentence-transformers + 多言語モデルで cosine similarity に置換予定。
    """
    tokens1 = set(re.findall(r"\w+", text1.lower()))
    tokens2 = set(re.findall(r"\w+", text2.lower()))
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    return len(intersection) / len(union) if union else 0.0
