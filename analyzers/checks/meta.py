"""Meta Ads チェック — Pixel/CAPI/クリエイティブ/構造/オーディエンス (M01-M56)"""
import logging

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

    return results


def _check_pixel_capi(camps, pixel_status):
    """Pixel / CAPI チェック M-PI1-PI10"""
    results = []
    ps = pixel_status or {}

    # M-PI1: Pixel 設置
    results.append({
        "id": "M-PI1", "passed": ps.get("pixel_installed", False),
        "campaign": "アカウント全体", "platform": "meta",
        "message": "" if ps.get("pixel_installed") else "Meta Pixel 未設置",
    })

    # M-PI2: EMQ ≥ 6
    emq = ps.get("event_match_quality")
    if emq is not None:
        results.append({
            "id": "M-PI2", "passed": emq >= 6,
            "campaign": "アカウント全体", "platform": "meta",
            "message": "" if emq >= 6 else f"EMQ {emq:.1f} (推奨: ≥6.0)",
        })

    # M-PI3: CAPI 有効
    results.append({
        "id": "M-PI3", "passed": ps.get("capi_enabled", False),
        "campaign": "アカウント全体", "platform": "meta",
        "message": "" if ps.get("capi_enabled") else "Conversions API 未有効",
    })

    return results


def _check_creative(camps, m_t):
    """クリエイティブチェック M-CR1-CR12"""
    results = []
    creative_t = m_t.get("creative", {})

    # M-CR1: クリエイティブ多様性（全体で≥10種）
    total_ads = sum(c.get("ad_count", 0) for c in camps)
    results.append({
        "id": "M-CR1", "passed": total_ads >= 10 or total_ads == 0,
        "campaign": "全Meta", "platform": "meta",
        "message": "" if total_ads >= 10 or total_ads == 0 else f"クリエイティブ {total_ads}種 (推奨≥10)",
    })

    # M-CR3: フリークエンシー疲弊
    fatigue_freq = creative_t.get("fatigue_frequency", 3.5)
    for camp in camps:
        if camp.get("frequency", 0) > fatigue_freq:
            results.append({
                "id": "M-CR3", "passed": False, "campaign": camp["campaign"], "platform": "meta",
                "message": f"フリークエンシー疲弊: {camp['frequency']:.1f} (閾値: {fatigue_freq})",
            })

    return results


def _check_structure(camps, m_t):
    """構造チェック M-ST1-ST18"""
    results = []
    struct_t = m_t.get("structure", {})
    learning_t = m_t.get("learning_phase", {})

    # M-ST2: 広告セット数 ≤5/キャンペーン
    max_adsets = struct_t.get("max_adsets_per_campaign", 5)
    for camp in camps:
        adset_count = camp.get("adset_count", 0)
        if adset_count > max_adsets:
            results.append({
                "id": "M-ST2", "passed": False, "campaign": camp["campaign"], "platform": "meta",
                "message": f"広告セット数 {adset_count} (上限: {max_adsets})",
            })

    # M-ST3: 学習フェーズ ≥50CV/週
    min_weekly_cv = learning_t.get("min_weekly_conversions", 50)
    daily_min = min_weekly_cv / 7
    for camp in camps:
        if camp.get("conversions", 0) < daily_min and camp.get("cost", 0) > 0:
            results.append({
                "id": "M-ST3", "passed": False, "campaign": camp["campaign"], "platform": "meta",
                "message": f"学習フェーズ未達: 日次CV {camp['conversions']:.1f} (週{min_weekly_cv}必要)",
            })

    return results


def _check_audience(camps, m_t):
    """オーディエンスチェック M-AU1-AU6"""
    results = []

    # M-AU1: オーバーラップ検出 — 同じタイプの複数キャンペーンがある場合
    type_groups = {}
    for camp in camps:
        ct = camp.get("campaign_type", "other")
        type_groups.setdefault(ct, []).append(camp)

    for ct, group in type_groups.items():
        if len(group) > 2:
            results.append({
                "id": "M-AU1", "passed": False, "campaign": f"{ct}グループ", "platform": "meta",
                "message": f"同一目的 '{ct}' に {len(group)} キャンペーン: オーバーラップリスク",
            })

    return results
