"""トレードオフ検出エンジン — 矛盾するチェック結果を検知し解決"""
import os
import yaml
import logging
import json
import urllib.request
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

CONFLICT_GROUPS = {
    "cpa_vs_volume": {
        "name": "CPA最小化 vs CV量最大化",
        "description": "CPA を下げるとCV数が減る可能性",
    },
    "learning_vs_testing": {
        "name": "学習フェーズ安定 vs クリエイティブテスト",
        "description": "学習フェーズ維持にはCVを集約するが、テストには分散が必要",
    },
    "precision_vs_reach": {
        "name": "詳細ターゲティング vs Advantage+ リーチ",
        "description": "精密ターゲティングとAI最適化リーチのトレードオフ",
    },
    "exclude_vs_opportunity": {
        "name": "ネガKW除外 vs ロングテール機会",
        "description": "除外しすぎるとロングテールの機会を逃す",
    },
}


def detect_conflicts(audit_results, client_cfg):
    """トレードオフ（矛盾するチェック結果）を検知

    Args:
        audit_results: ads_audit の結果
        client_cfg: クライアント設定
    Returns:
        list[dict]: 検出された矛盾リスト
    """
    conflicts = []
    issues = audit_results.get("issues", [])

    # YAML ルールの conflict_group タグを使って矛盾を検知
    grouped = {}
    for issue in issues:
        # issue にルール詳細がある場合 (platform_details 経由)
        conflict_group = issue.get("conflict_group")
        if conflict_group:
            grouped.setdefault(conflict_group, []).append(issue)

    for group_id, group_issues in grouped.items():
        if len(group_issues) >= 2:
            group_info = CONFLICT_GROUPS.get(group_id, {"name": group_id, "description": ""})
            impact = _calc_conflict_impact(group_issues)
            conflicts.append({
                "conflict_group": group_id,
                "name": group_info["name"],
                "description": group_info["description"],
                "issues": group_issues,
                "issue_count": len(group_issues),
                "impact_score": impact["score"],
                "max_severity": impact["max_severity"],
                "affected_platforms": impact["platforms"],
                "auto_resolved": False,
                "resolution": None,
            })

    # impact_score 降順でソート（最も影響が大きいものを先に）
    conflicts.sort(key=lambda c: c["impact_score"], reverse=True)

    return conflicts


SEVERITY_SCORE = {"critical": 10, "high": 6, "medium": 3, "warning": 3, "low": 1}


def _calc_conflict_impact(issues):
    """トレードオフの影響度を算出"""
    severities = [i.get("severity", "medium") for i in issues]
    platforms = list(set(i.get("platform", "unknown") for i in issues))
    max_sev = "low"
    total_score = 0
    for s in severities:
        total_score += SEVERITY_SCORE.get(s, 1)
        if SEVERITY_SCORE.get(s, 0) > SEVERITY_SCORE.get(max_sev, 0):
            max_sev = s
    # 複数媒体にまたがるとインパクト増
    if len(platforms) > 1:
        total_score = int(total_score * 1.5)
    return {"score": total_score, "max_severity": max_sev, "platforms": platforms}


def resolve_conflicts(conflicts, client_cfg):
    """矛盾を解決（自動 or Slack質問）

    Args:
        conflicts: detect_conflicts() の出力
        client_cfg: クライアント設定 (objective フィールド含む)
    Returns:
        list[dict]: 解決済み矛盾リスト
    """
    objective = client_cfg.get("objective", "balanced")

    for conflict in conflicts:
        group = conflict.get("conflict_group", "")

        if objective == "cpa_minimize":
            conflict["resolution"] = _resolve_for_cpa(group)
            conflict["auto_resolved"] = True
        elif objective == "cv_maximize":
            conflict["resolution"] = _resolve_for_volume(group)
            conflict["auto_resolved"] = True
        elif objective == "roas_target":
            conflict["resolution"] = _resolve_for_roas(group)
            conflict["auto_resolved"] = True
        else:
            # balanced or 未設定 → Slack質問
            conflict["resolution"] = "Slack経由でクライアントに確認が必要"
            conflict["auto_resolved"] = False

    # 解決結果を context-overrides.yaml に記録
    _save_overrides(conflicts)

    return conflicts


def _resolve_for_cpa(group):
    """CPA最小化目標での解決"""
    resolutions = {
        "cpa_vs_volume": "CPA最小化を優先: 目標CPA厳格化、低効率キーワード停止",
        "learning_vs_testing": "学習フェーズ安定を優先: CV集約してCPA安定化",
        "precision_vs_reach": "精密ターゲティングを優先: Advantage+を制限",
        "exclude_vs_opportunity": "ネガKW除外を優先: 無駄クリック削減でCPA改善",
    }
    return resolutions.get(group, "CPA最小化方向で対応")


def _resolve_for_volume(group):
    """CV量最大化目標での解決"""
    resolutions = {
        "cpa_vs_volume": "CV量最大化を優先: 目標CPA緩和、予算拡大",
        "learning_vs_testing": "テスト優先: 新クリエイティブ投入でCV増加を狙う",
        "precision_vs_reach": "Advantage+リーチを優先: AI最適化で新規CV獲得",
        "exclude_vs_opportunity": "ロングテール機会を優先: 除外を最小限に",
    }
    return resolutions.get(group, "CV量最大化方向で対応")


def _resolve_for_roas(group):
    """ROAS目標での解決"""
    resolutions = {
        "cpa_vs_volume": "ROAS目標を優先: 高価値CVに集中",
        "learning_vs_testing": "学習安定を優先: 高ROASクリエイティブを維持",
        "precision_vs_reach": "バランス: 高ROASセグメントでのリーチ拡大",
        "exclude_vs_opportunity": "ROASベースで判断: 低ROAS検索語句のみ除外",
    }
    return resolutions.get(group, "ROAS目標方向で対応")


def _save_overrides(conflicts):
    """解決結果を context-overrides.yaml に保存（dedup + 期限切れ削除）"""
    path = os.path.join(CONFIG_DIR, "context-overrides.yaml")

    existing = {"version": "1.0", "overrides": []}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or existing

    now = datetime.now()

    # 期限切れエントリーを削除
    existing["overrides"] = [
        o for o in existing.get("overrides", [])
        if _parse_iso(o.get("expires_at", "")) > now
    ]

    # 既存のconflict_groupをインデックス化（dedup用）
    existing_groups = {
        o.get("conflict_group"): i
        for i, o in enumerate(existing["overrides"])
    }

    for conflict in conflicts:
        group_id = conflict.get("conflict_group")
        override = {
            "conflict_group": group_id,
            "chosen_priority": conflict.get("resolution", ""),
            "decided_by": "auto" if conflict.get("auto_resolved") else "pending",
            "decided_at": now.isoformat(),
            "expires_at": (now + timedelta(days=90)).isoformat(),
        }
        if group_id in existing_groups:
            existing["overrides"][existing_groups[group_id]] = override
        else:
            existing["overrides"].append(override)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False)


def _parse_iso(iso_str):
    """ISO形式の日時文字列をパース（不正値は過去日を返す）"""
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return datetime.min
