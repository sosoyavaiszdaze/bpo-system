"""Zynect 推奨生成エンジン (ADR-009 第二層)

責務: 灰ゾーン判断時に運用憲章 (operating_charter) と過去判断履歴を参照し、
      Zynect 推奨アクション + 各選択肢の得失を算出する。

主要関数:
    - load_operating_charter(client_id) -> dict
    - load_decision_history(client_id, period) -> list
    - find_similar_past_decisions(rule_context, client_id, top_n=5) -> list
    - generate_recommendation(rule, client_data, ml_status) -> dict
    - check_consistency(client_id, period='monthly') -> dict
    - log_decision(client_id, decision_record) -> None

データソース:
    - outputs/client_preferences/{client_id}.yaml (運用憲章)
    - outputs/client_preferences/{client_id}_decisions.yaml (判断ログ)
"""
from __future__ import annotations

import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
PREFS_DIR = ROOT / "outputs" / "client_preferences"


# ========== Public API ==========

def load_operating_charter(client_id: str) -> dict:
    """運用憲章を読込み。未存在なら tbd-default を返す (inner dict のみ返却)。"""
    path = PREFS_DIR / f"{client_id}.yaml"
    if not path.exists():
        return _default_charter(client_id)["operating_charter"]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("operating_charter") or _default_charter(client_id)["operating_charter"]


def load_decision_history(client_id: str, period: Optional[str] = None) -> list[dict]:
    """判断ログを読込み。period='monthly' なら直近 30 日のみ返却。"""
    path = PREFS_DIR / f"{client_id}_decisions.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    decisions = data.get("decisions", []) or []

    if period == "monthly":
        cutoff = datetime.now() - timedelta(days=30)
        return [d for d in decisions if _parse_iso(d.get("timestamp")) >= cutoff]
    if period == "quarterly":
        cutoff = datetime.now() - timedelta(days=90)
        return [d for d in decisions if _parse_iso(d.get("timestamp")) >= cutoff]
    return decisions


def find_similar_past_decisions(
    rule_context: dict, client_id: str, top_n: int = 5
) -> list[dict]:
    """fraud_score / cv_rate / 媒体 / エンティティ性質が近い過去判断を返す。

    類似度スコア:
      - 媒体一致: +0.5
      - fraud_score 差 < 0.1: +0.3
      - cv_rate 差 < 5%: +0.2
    """
    history = load_decision_history(client_id)
    if not history:
        return []

    scored = []
    for past in history:
        score = 0.0
        gz = past.get("grey_zone_data") or {}
        if gz.get("media") == rule_context.get("media"):
            score += 0.5
        if abs(gz.get("fraud_score", 0) - rule_context.get("fraud_score", 0)) < 0.1:
            score += 0.3
        if abs(gz.get("cv_rate_pct", 0) - rule_context.get("cv_rate_pct", 0)) < 5:
            score += 0.2
        if score > 0:
            scored.append((score, past))

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_n]]


def generate_recommendation(
    rule: dict, client_data: dict, ml_status: Optional[dict] = None
) -> dict:
    """Zynect 推奨を生成

    Args:
        rule: 灰ゾーン検知ルール (rule_id / fraud_score / cv_rate 等)
        client_data: client 状態 (charter + decision history)
        ml_status: 機械学習状況 (in_learning_phase / phase_days_remaining 等)

    Returns:
        {
            "recommended_action": "monitor_with_close_watch" | "block_aggressive" | ...,
            "rationale": "運用憲章 primary_kpi=cv_max + 過去 5 件中 4 件が同方向",
            "expected_outcomes": {
                "option_a": {"net_yen": -50000, ...},
                ...
            },
            "similar_past_decisions": [...],
            "confidence": 0.78,
        }
    """
    charter = client_data.get("charter") or load_operating_charter(rule.get("client_id", ""))
    history = client_data.get("history") or []

    # 1. primary_kpi に基づく初期アクション
    primary_kpi = charter.get("primary_kpi", "balanced")
    base_action = _base_action_from_kpi(primary_kpi, rule)

    # 2. cv_loss_tolerance_pct 評価
    estimated_loss_pct = _estimate_cv_loss_pct(rule)
    tolerance = charter.get("cv_loss_tolerance_pct")
    if isinstance(tolerance, (int, float)) and estimated_loss_pct > tolerance:
        base_action = _downgrade_action(base_action)

    # 3. 過去判断 5 件の傾向を反映
    similar = find_similar_past_decisions(rule, rule.get("client_id", ""))
    if similar:
        majority = _majority_action(similar)
        if majority and majority != base_action:
            # 過去判断と乖離する場合は confidence を下げる
            pass

    # 4. 学習フェーズへの影響
    if ml_status and ml_status.get("in_learning_phase"):
        base_action = _preserve_learning_signal(base_action)

    # confidence 計算 (sample_count 連動、ADR-009 §7.5)
    sample_count = len(history)
    confidence_band_pct = max(15, 50 - 5 * sample_count)
    confidence = (100 - confidence_band_pct) / 100

    rationale = _build_rationale(charter, similar, ml_status, base_action)
    expected_outcomes = _calc_expected_outcomes(rule, base_action)

    return {
        "recommended_action": base_action,
        "rationale": rationale,
        "expected_outcomes": expected_outcomes,
        "similar_past_decisions": similar,
        "confidence": confidence,
        "confidence_band_pct": confidence_band_pct,
    }


def check_consistency(client_id: str, period: str = "monthly") -> dict:
    """整合率を算出 (charter_consistency_pct + past_consistency_pct)"""
    history = load_decision_history(client_id, period=period)
    if not history:
        return {
            "charter_consistency_pct": None,
            "past_consistency_pct": None,
            "decision_count": 0,
            "period": period,
        }

    consistent_charter = sum(1 for d in history if d.get("consistency_with_charter"))
    consistent_past = sum(1 for d in history if d.get("consistency_with_past"))

    return {
        "charter_consistency_pct": round(consistent_charter / len(history) * 100, 1),
        "past_consistency_pct": round(consistent_past / len(history) * 100, 1),
        "decision_count": len(history),
        "period": period,
    }


def log_decision(client_id: str, decision_record: dict) -> None:
    """判断ログに追記"""
    path = PREFS_DIR / f"{client_id}_decisions.yaml"
    PREFS_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"decisions": []}
    else:
        data = {"client_id": client_id, "decisions": [], "metadata": {"created_at": _now_iso()}}

    decisions = data.setdefault("decisions", [])

    # decision_id 自動生成
    if "decision_id" not in decision_record:
        decision_record["decision_id"] = f"D-{datetime.now().strftime('%Y%m%d')}-{len(decisions)+1:03d}"
    decision_record.setdefault("timestamp", _now_iso())

    decisions.append(decision_record)
    data.setdefault("metadata", {})["last_updated"] = _now_iso()

    # サマリ更新
    summary = data.setdefault("summary", {})
    summary["total_count"] = len(decisions)
    by_action = summary.setdefault("by_action", {})
    action = decision_record.get("customer_decision", "unknown")
    by_action[action] = by_action.get(action, 0) + 1

    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


# ========== Private Helpers ==========

def _default_charter(client_id: str) -> dict:
    return {
        "client_id": client_id,
        "operating_charter": {
            "primary_kpi": "tbd",
            "learning_phase_tolerance": "tbd",
            "cv_loss_tolerance_pct": "tbd",
            "decision_frequency": "per_event",
            "delegation_scope": "tbd",
            "charter_version": "0.1-default",
            "established_at": None,
            "next_review_at": None,
        },
    }


def _base_action_from_kpi(primary_kpi: str, rule: dict) -> str:
    """primary_kpi に基づく初期推奨アクション"""
    mapping = {
        "cv_max":   "monitor_with_close_watch",
        "cpa_min":  "block_aggressive",
        "roas_max": "block_with_cv_preservation",
        "balanced": "investigate",
        "tbd":      "monitor_with_close_watch",  # 仮値時は保守的
    }
    return mapping.get(primary_kpi, "investigate")


def _downgrade_action(action: str) -> str:
    """より保守的な (CV 保全側) アクションに下げる"""
    downgrade_map = {
        "block_aggressive": "block_with_cv_preservation",
        "block_with_cv_preservation": "investigate",
        "investigate": "monitor_with_close_watch",
        "monitor_with_close_watch": "monitor_with_close_watch",
    }
    return downgrade_map.get(action, action)


def _preserve_learning_signal(action: str) -> str:
    """学習フェーズ中はブロックを controlled に"""
    if action.startswith("block_"):
        return "investigate"
    return action


def _estimate_cv_loss_pct(rule: dict) -> float:
    """ブロック実行時の推定 CV 損失率 (簡略実装)"""
    cv_count = rule.get("cv_count", 0) or 0
    total_cv = rule.get("total_cv_30d", 1) or 1
    return cv_count / total_cv * 100


def _majority_action(similar: list[dict]) -> Optional[str]:
    """過去判断の多数派アクションを返す"""
    if not similar:
        return None
    counts: dict[str, int] = {}
    for d in similar:
        action = d.get("customer_decision")
        if action:
            counts[action] = counts.get(action, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda x: x[1])[0]


def _build_rationale(
    charter: dict, similar: list, ml_status: Optional[dict], action: str
) -> str:
    """推奨理由の文字列構築"""
    parts = [f"運用憲章 primary_kpi={charter.get('primary_kpi', 'tbd')}"]
    if similar:
        majority = _majority_action(similar)
        if majority:
            same_count = sum(1 for d in similar if d.get("customer_decision") == majority)
            parts.append(f"過去判断 {len(similar)} 件中 {same_count} 件が {majority}")
    if ml_status and ml_status.get("in_learning_phase"):
        parts.append("学習フェーズ中につき保守側")
    return " + ".join(parts)


def _calc_expected_outcomes(rule: dict, recommended_action: str) -> dict:
    """各選択肢の期待効果 (ネット ¥) を簡略試算"""
    cv_count = rule.get("cv_count", 0) or 0
    ad_cost = rule.get("ad_cost", 0) or 0
    aov = rule.get("aov_jpy", 15000)

    return {
        "option_a_custom_audience_exclude": {
            "net_yen": int(ad_cost * 0.7 - cv_count * aov * 0.3),
            "label": "Custom Audience exclusion",
        },
        "option_b_audience_network_block": {
            "net_yen": int(ad_cost * 0.9 - cv_count * aov * 0.5),
            "label": "Audience Network 完全除外",
        },
        "option_c_monitor": {
            "net_yen": int(-ad_cost * 0.3),
            "label": "モニタリング継続",
        },
    }


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(s: Optional[str]) -> datetime:
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else datetime.min
    except (ValueError, TypeError):
        return datetime.min
