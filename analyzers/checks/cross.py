"""クロスプラットフォームチェック — プライバシー/クリエイティブ/リフレッシュ (X01-X07)"""
import logging

log = logging.getLogger("bpo")


def run_cross_checks(campaigns, thresholds, pixel_statuses=None):
    """クロスプラットフォームチェック実行"""
    if not campaigns:
        return []

    results = []
    ps = pixel_statuses or {}

    # X-PI1: プライバシーインフラ完全性
    platforms_with_tracking = set()
    for platform, status in ps.items():
        if status and status.get("pixel_installed"):
            platforms_with_tracking.add(platform)

    active_platforms = set(c.get("platform", "unknown") for c in campaigns)
    missing = active_platforms - platforms_with_tracking - {"unknown", "csv"}
    if missing:
        results.append({
            "id": "X-PI1", "passed": False, "campaign": "クロス媒体", "platform": "cross",
            "message": f"トラッキング未設置: {', '.join(missing)}",
            "severity": "critical",
        })
    elif platforms_with_tracking:
        results.append({
            "id": "X-PI1", "passed": True, "campaign": "クロス媒体", "platform": "cross",
            "message": "全プラットフォームでトラッキング設置済み",
        })

    # X-CD1: クリエイティブ多様性（全プラットフォーム合計）
    total_ads = sum(c.get("ad_count", 0) for c in campaigns)
    if total_ads > 0:
        results.append({
            "id": "X-CD1", "passed": total_ads >= 15, "campaign": "クロス媒体", "platform": "cross",
            "message": "" if total_ads >= 15 else f"全体クリエイティブ数 {total_ads} (推奨≥15)",
        })

    # X-RF1: コスト配分バランス
    platform_costs = {}
    for camp in campaigns:
        p = camp.get("platform", "unknown")
        platform_costs[p] = platform_costs.get(p, 0) + camp.get("cost", 0)

    total_cost = sum(platform_costs.values())
    if total_cost > 0 and len(platform_costs) > 1:
        max_share = max(platform_costs.values()) / total_cost
        if max_share > 0.8:
            dominant = max(platform_costs, key=platform_costs.get)
            results.append({
                "id": "X-RF1", "passed": False, "campaign": "クロス媒体", "platform": "cross",
                "message": f"コスト集中: {dominant} が {max_share * 100:.0f}% (分散推奨)",
            })

    return results
