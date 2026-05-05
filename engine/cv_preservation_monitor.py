"""CV 保全モニタリング (ADR-009 + TO-12 連携)

責務: ブロック実行後のCV保全状況を監視。想定超のCV損失検知時に自動緩和を提案。

主要関数:
    - record_block_event(client_id, block_event)
    - monitor_post_block_metrics(client_id, block_event_id, days=14)
    - detect_cv_drop(metrics, threshold_pct=15) -> bool
    - auto_relax_threshold(client_id, block_event_id) -> dict
    - generate_preservation_report(client_id, period) -> dict

データソース:
    - outputs/{client_id}/block_events.yaml (本ファイルが管理)
    - adapters/{platform}_adapter.py の最新 KPI (Phase B Week 2-3 で接続)
"""
from __future__ import annotations

import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"


# ========== Public API ==========

def record_block_event(client_id: str, block_event: dict) -> dict:
    """ブロック実行履歴を outputs/{client_id}/block_events.yaml に記録

    Args:
        client_id: クライアント ID
        block_event: {
            "media": "meta",
            "method": "custom_audience_exclusion",
            "target": "...",
            "blocked_count": int,
            "fraud_score_threshold": 0.78,
            ...
        }
    Returns:
        記録された block_event (event_id 付与済)
    """
    path = OUTPUT_DIR / client_id / "block_events.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"events": []}
    else:
        data = {"client_id": client_id, "events": []}

    events = data.setdefault("events", [])
    block_event = dict(block_event)
    block_event.setdefault("event_id", f"BE-{datetime.now().strftime('%Y%m%d')}-{len(events)+1:03d}")
    block_event.setdefault("recorded_at", _now_iso())

    events.append(block_event)
    data["last_updated"] = _now_iso()

    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)

    return block_event


def monitor_post_block_metrics(
    client_id: str, block_event_id: str, days: int = 14,
    pre_metrics: Optional[dict] = None, post_metrics: Optional[dict] = None,
) -> dict:
    """ブロック前後 N 日の CV / CPA / ROAS 変動を取得

    Args:
        client_id: クライアント ID
        block_event_id: 対象ブロックイベント ID
        days: 比較期間 (デフォルト 14 日)
        pre_metrics, post_metrics: テスト用注入 (本番は adapters 経由)

    Returns:
        {
            "block_event_id": ...,
            "pre": {"cv": 100, "cpa_jpy": 5000, "roas": 3.0},
            "post": {"cv": 85, "cpa_jpy": 6000, "roas": 2.7},
            "delta": {"cv_pct": -15, "cpa_pct": +20, "roas_pct": -10},
            "evaluation": "cv_drop_detected" | "stable" | "improved",
        }
    """
    if pre_metrics is None:
        pre_metrics = _fetch_metrics(client_id, days=days, before_event_id=block_event_id)
    if post_metrics is None:
        post_metrics = _fetch_metrics(client_id, days=days, after_event_id=block_event_id)

    delta = _calc_delta(pre_metrics, post_metrics)
    evaluation = _evaluate_post_block(delta)

    return {
        "block_event_id": block_event_id,
        "client_id": client_id,
        "comparison_days": days,
        "pre": pre_metrics,
        "post": post_metrics,
        "delta": delta,
        "evaluation": evaluation,
        "evaluated_at": _now_iso(),
    }


def detect_cv_drop(metrics: dict, threshold_pct: float = 15.0) -> bool:
    """想定超の CV 損失を検知

    Args:
        metrics: monitor_post_block_metrics の出力
        threshold_pct: CV 低下率の許容閾値 (default 15%)

    Returns:
        True なら CV 損失が閾値超過 (= 緩和を検討すべき)
    """
    delta = metrics.get("delta") or {}
    cv_pct = delta.get("cv_pct", 0)
    return cv_pct < -threshold_pct


def auto_relax_threshold(client_id: str, block_event_id: str) -> dict:
    """閾値自動緩和の推奨/実行

    detect_cv_drop で True が返ったブロックイベントについて、
    fraud_score 閾値を緩める提案を生成。

    Returns:
        {
            "block_event_id": ...,
            "current_threshold": 0.78,
            "suggested_threshold": 0.82,
            "rationale": "CV -18% で許容超過、閾値を 0.04 厳格化 (誤検知排除側へ)",
            "auto_applied": False,  # Phase A は提案のみ
        }
    """
    events = _load_block_events(client_id)
    target = next((e for e in events if e.get("event_id") == block_event_id), None)
    if not target:
        return {"error": f"block_event {block_event_id} not found"}

    current_threshold = target.get("fraud_score_threshold", 0.7)
    # CV 損失検出時は閾値を**厳しく** (= 高く) する (より強い不正シグナルだけブロック)
    suggested = min(0.95, current_threshold + 0.04)

    return {
        "block_event_id": block_event_id,
        "client_id": client_id,
        "current_threshold": current_threshold,
        "suggested_threshold": round(suggested, 2),
        "delta": round(suggested - current_threshold, 2),
        "rationale": "CV 損失が許容超過、閾値を厳格化して誤検知の影響を抑える",
        "auto_applied": False,  # Phase A は提案のみ、Phase B Week 2-3 で実行
        "suggested_at": _now_iso(),
    }


def generate_preservation_report(client_id: str, period: str = "monthly") -> dict:
    """月次 CV 保全レポートデータを生成 (monthly_report.md.j2 用)

    Args:
        client_id: クライアント ID
        period: monthly | quarterly

    Returns:
        {
            "period": "2026-05",
            "block_events_count": 12,
            "total_blocked_count": 1500,
            "cv_drop_detected_count": 1,
            "cv_preserved_pct": 91.7,        # 損失 / 想定超過しなかった率
            "auto_relax_suggestions": 1,
            "blocked_ad_cost_savings_jpy": 320000,
        }
    """
    events = _load_block_events(client_id)
    days = 30 if period == "monthly" else 90
    cutoff = datetime.now() - timedelta(days=days)
    period_events = [e for e in events if _parse_iso(e.get("recorded_at")) >= cutoff]

    cv_drops = 0
    auto_relax_count = 0
    total_blocked = 0
    estimated_savings = 0

    for e in period_events:
        total_blocked += e.get("blocked_count", 0) or 0
        estimated_savings += e.get("estimated_savings_jpy", 0) or 0
        # 各イベントの post 監視結果があれば集計
        post = e.get("post_monitor_result")
        if post and detect_cv_drop(post):
            cv_drops += 1
            auto_relax_count += 1

    cv_preserved_pct = round(
        (1 - cv_drops / max(len(period_events), 1)) * 100, 1
    )

    return {
        "period": _period_label(period),
        "client_id": client_id,
        "block_events_count": len(period_events),
        "total_blocked_count": total_blocked,
        "cv_drop_detected_count": cv_drops,
        "cv_preserved_pct": cv_preserved_pct,
        "auto_relax_suggestions": auto_relax_count,
        "blocked_ad_cost_savings_jpy": estimated_savings,
        "generated_at": _now_iso(),
    }


# ========== Private Helpers ==========

def _load_block_events(client_id: str) -> list[dict]:
    path = OUTPUT_DIR / client_id / "block_events.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("events", []) or []
    except Exception:
        return []


def _fetch_metrics(client_id: str, days: int, **kwargs) -> dict:
    """adapters 経由で KPI 取得 (Phase B Week 2-3 で接続)"""
    log.info(f"[cv_preservation_monitor] {client_id}: would fetch metrics (Phase A mock)")
    # Phase A: モック値、Phase B で実 API 呼出
    return {"cv": 0, "cpa_jpy": 0, "roas": 0}


def _calc_delta(pre: dict, post: dict) -> dict:
    """前後比較の delta を計算"""
    def pct(a, b):
        if not a:
            return 0
        return round((b - a) / a * 100, 1)
    return {
        "cv_pct": pct(pre.get("cv", 0), post.get("cv", 0)),
        "cpa_pct": pct(pre.get("cpa_jpy", 0), post.get("cpa_jpy", 0)),
        "roas_pct": pct(pre.get("roas", 0), post.get("roas", 0)),
    }


def _evaluate_post_block(delta: dict) -> str:
    """delta から評価ラベルを生成"""
    cv_pct = delta.get("cv_pct", 0)
    cpa_pct = delta.get("cpa_pct", 0)
    if cv_pct < -15:
        return "cv_drop_detected"
    if cv_pct > 5 or cpa_pct < -10:
        return "improved"
    return "stable"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(s: Optional[str]) -> datetime:
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else datetime.min
    except (ValueError, TypeError):
        return datetime.min


def _period_label(period: str) -> str:
    if period == "monthly":
        return datetime.now().strftime("%Y-%m")
    if period == "quarterly":
        m = datetime.now().month
        q = (m - 1) // 3 + 1
        return f"{datetime.now().year}-Q{q}"
    return period
