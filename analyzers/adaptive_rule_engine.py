"""新種の不正パターンを自動検知し、プラットフォーム変更との区別を行うエンジン。"""
import logging

log = logging.getLogger("bpo")

FRAUD_PATTERNS = {
    "click_flood": {
        "signals": {"ctr": "increase", "cvr": "decrease", "cpa": "increase", "cpc": "stable"},
        "confidence": 0.85,
        "description": "クリックフラッド: クリック急増だがCV率低下",
    },
    "impression_fraud": {
        "signals": {"ctr": "decrease", "impressions": "increase", "cvr": "stable", "reach": "increase"},
        "confidence": 0.80,
        "description": "インプレッション不正: 表示数だけ急増",
    },
    "attribution_hijack": {
        "signals": {"ctr": "increase", "cvr": "increase", "click_to_cv_time": "decrease", "crm_ghost_ratio": "increase"},
        "confidence": 0.90,
        "description": "帰属ハイジャック: CV帰属を奪取",
    },
    "bot_traffic": {
        "signals": {"ctr": "increase", "bounce_rate": "increase", "time_on_site": "decrease", "cvr": "decrease"},
        "confidence": 0.85,
        "description": "ボットトラフィック: クリック後すぐ離脱",
    },
}

PLATFORM_CHANGE_PATTERN = {
    "algorithm_update": {
        "signals": {"ctr": "increase", "cvr": "increase", "cpa": "decrease"},
        "additional_checks": {"all_campaigns_affected": True, "cross_platform_consistent": False},
        "description": "プラットフォームアルゴリズム変更の可能性",
    },
}


def detect_changes_cusum(values, threshold=5.0, drift=0.5):
    """CUSUM法による変化点検知"""
    if len(values) < 14:
        return {"change_detected": False, "reason": "データ不足"}

    half = len(values) // 2
    mean = sum(values[:half]) / half
    variance = sum((v - mean) ** 2 for v in values[:half]) / half
    std = variance ** 0.5 + 1e-6

    s_pos, s_neg = 0.0, 0.0
    change_points = []

    for i, v in enumerate(values):
        normalized = (v - mean) / std
        s_pos = max(0, s_pos + normalized - drift)
        s_neg = max(0, s_neg - normalized - drift)

        if s_pos > threshold or s_neg > threshold:
            change_points.append({
                "index": i, "value": v,
                "direction": "increase" if s_pos > threshold else "decrease",
                "magnitude": max(s_pos, s_neg),
            })
            s_pos, s_neg = 0.0, 0.0

    return {
        "change_detected": len(change_points) > 0,
        "change_points": change_points,
        "baseline_mean": mean,
        "baseline_std": std,
    }


def classify_anomaly(metric_changes, context):
    """検出された異常が「不正」か「プラットフォーム変更」かを判別"""
    best_match = None
    best_score = 0.0

    for pattern_name, pattern in FRAUD_PATTERNS.items():
        match_count = sum(
            1 for signal, expected in pattern["signals"].items()
            if metric_changes.get(signal) == expected
        )
        match_ratio = match_count / len(pattern["signals"])
        score = match_ratio * pattern["confidence"]

        if score > best_score:
            best_score = score
            best_match = {
                "pattern": pattern_name,
                "match_ratio": match_ratio,
                "confidence": score,
                "description": pattern["description"],
                "classification": "fraud",
            }

    for pattern_name, pattern in PLATFORM_CHANGE_PATTERN.items():
        match_count = sum(
            1 for signal, expected in pattern["signals"].items()
            if metric_changes.get(signal) == expected
        )
        match_ratio = match_count / len(pattern["signals"])
        additional_score = 0
        checks = pattern["additional_checks"]
        if checks.get("all_campaigns_affected") and context.get("campaigns_affected_ratio", 0) >= 0.80:
            additional_score += 0.3
        if not checks.get("cross_platform_consistent") and not context.get("other_platforms_affected"):
            additional_score += 0.2
        platform_score = (match_ratio * 0.5) + additional_score

        if platform_score > best_score:
            best_score = platform_score
            best_match = {
                "pattern": pattern_name,
                "match_ratio": match_ratio,
                "confidence": platform_score,
                "description": pattern["description"],
                "classification": "platform_change",
            }

    if best_match is None or best_score < 0.40:
        return {
            "classification": "unknown", "confidence": best_score,
            "recommendation": "flag_for_human_review",
            "description": "パターンマッチ不十分。人間レビュー推奨。",
        }

    if best_match["classification"] == "fraud" and best_score >= 0.70:
        best_match["recommendation"] = "auto_apply_countermeasure"
        best_match["action"] = _get_countermeasure(best_match["pattern"])
    elif best_match["classification"] == "platform_change":
        best_match["recommendation"] = "no_action_monitor"
        best_match["action"] = "プラットフォーム変更の可能性。ルール閾値変更不要。1週間監視。"
    else:
        best_match["recommendation"] = "flag_for_human_review"

    return best_match


def _get_countermeasure(pattern):
    """不正パターンに対する対策を返す"""
    countermeasures = {
        "click_flood": "該当配信面のブロック閾値を0.05引き下げ。IP Intelligenceスキャン実施。",
        "impression_fraud": "該当配信面を除外リストに追加。インプレッション単価を監視。",
        "attribution_hijack": "クリック→CV時間フィルタを有効化（5秒未満を自動除外）。",
        "bot_traffic": "該当IPレンジを/24でブロック。デバイスフィンガープリント分析実施。",
    }
    return countermeasures.get(pattern, "手動調査を推奨。")


def auto_update_rules(daily_metrics, current_thresholds):
    """過去30日のデータから閾値を自動調整"""
    updates = []
    metrics_to_check = ["ctr", "cvr", "cpa", "fraud_rate"]

    for metric in metrics_to_check:
        values = [d.get(metric, 0) for d in daily_metrics]
        result = detect_changes_cusum(values)

        if not result["change_detected"]:
            continue

        metric_changes = {}
        for m in metrics_to_check:
            m_values = [d.get(m, 0) for d in daily_metrics]
            if len(m_values) < 14:
                metric_changes[m] = "stable"
                continue
            recent = sum(m_values[-7:]) / 7
            baseline = sum(m_values[:14]) / 14
            if baseline > 0 and recent > baseline * 1.10:
                metric_changes[m] = "increase"
            elif baseline > 0 and recent < baseline * 0.90:
                metric_changes[m] = "decrease"
            else:
                metric_changes[m] = "stable"

        classification = classify_anomaly(metric_changes, {
            "campaigns_affected_ratio": 0.5,
            "other_platforms_affected": False,
        })

        if classification["classification"] == "fraud":
            old_threshold = current_thresholds.get(f"{metric}_threshold", 0)
            adjustment = -0.05 if metric in ["ctr", "fraud_rate"] else 0.05
            new_threshold = old_threshold + adjustment
            auto_applied = classification["confidence"] >= 0.70

            updates.append({
                "metric": metric,
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
                "reason": classification["description"],
                "confidence": classification["confidence"],
                "auto_applied": auto_applied,
            })

            if auto_applied:
                current_thresholds[f"{metric}_threshold"] = new_threshold
                log.info(f"閾値自動更新: {metric} {old_threshold} → {new_threshold}")
                # Twenty CRM にルール変更を記録
                try:
                    from notifiers.crm_twenty import TwentyCRM
                    TwentyCRM().save_rule_change(updates[-1])
                except Exception:
                    pass
        elif classification["classification"] == "platform_change":
            updates.append({
                "metric": metric,
                "reason": "プラットフォーム変更と判定。閾値変更なし。",
                "confidence": classification["confidence"],
                "auto_applied": False,
            })

    return {
        "updates": updates,
        "auto_applied_count": sum(1 for u in updates if u.get("auto_applied")),
        "needs_human_review": sum(1 for u in updates if not u.get("auto_applied")),
        "updated_thresholds": current_thresholds,
    }
