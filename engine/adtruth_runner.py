"""AdTruth 日次ランナー (Phase A、ADR-006/009/014 連携)

責務: 日次で fraud_score を算出 → 灰ゾーン (gray) / 黒ゾーン (black) を抽出 →
      検知ありなら ChatWork で判断要請を送る、検知ゼロなら日次ログのみ残す。

Phase A の方針 (ADR-014 厳守):
    - campaign 粒度のみ、閾値 0.60 / 5.0%
    - 検知数を増やすための override / 粒度拡張は一切しない
    - gray = 高 fraud × 高 CV (顧客判断必須)、black = 高 fraud × 低 CV (推奨ブロック)
    - ChatWork 通知は gray + black 検出時のみ。0 件なら ChatWork は静寂、ログだけ残す
    - block_events.yaml に Zynect 推奨候補として記録 (Phase B で実ブロック実行)

主要関数:
    - run_adtruth_check(client_id, dry_run=False, today=None) -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent


# ========== Public API ==========

def run_adtruth_check(
    client_id: str,
    dry_run: bool = False,
    today: Optional[str] = None,
) -> dict:
    """日次 AdTruth サイクル実行

    Returns:
        {
            "client_id": str,
            "samples_count": int,
            "gray_count": int,
            "black_count": int,
            "posted_count": int,    # ChatWork に送った件数 (gray+black の合計)
            "skipped_count": int,   # idempotency でスキップされた件数
            "log_only_message": str | None,  # 0 件時の日次ログ文字列
            "block_event_ids": list[str],
        }
    """
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    summary = {
        "client_id": client_id,
        "samples_count": 0,
        "gray_count": 0,
        "black_count": 0,
        "posted_count": 0,
        "skipped_count": 0,
        "log_only_message": None,
        "block_event_ids": [],
    }

    samples = _collect_fraud_samples(client_id)
    summary["samples_count"] = len(samples)

    flagged = [s for s in samples if s.get("quadrant") in ("gray", "black")]
    summary["gray_count"] = sum(1 for s in flagged if s["quadrant"] == "gray")
    summary["black_count"] = sum(1 for s in flagged if s["quadrant"] == "black")

    if not flagged:
        msg = (
            f"AdTruth 監視中: {client_id} — sample {len(samples)} 件、"
            f"gray 0件 / black 0件 (ヘルシー)"
        )
        summary["log_only_message"] = msg
        log.info(f"[adtruth] {msg}")
        return summary

    # gray + black 検出時: 各サンプルを ChatWork で報告
    log.info(
        f"[adtruth] {client_id}: gray {summary['gray_count']} 件 / "
        f"black {summary['black_count']} 件 検出、ChatWork 送信開始"
    )

    client_cfg = _load_client_cfg(client_id)
    for sample in flagged:
        result = _post_grey_zone_message(
            client_id, client_cfg, sample, today_str=today_str, dry_run=dry_run,
        )
        if result.get("posted"):
            summary["posted_count"] += 1
            event_id = _record_zynect_recommendation(client_id, sample, today_str)
            summary["block_event_ids"].append(event_id)
        elif result.get("skipped"):
            summary["skipped_count"] += 1

    return summary


# ========== Private Helpers ==========

def _collect_fraud_samples(client_id: str) -> list[dict]:
    """threshold_optimizer 経由で campaign 粒度サンプルを収集"""
    from engine.threshold_optimizer import _fetch_samples
    return _fetch_samples(client_id, "meta", period_days=30)


def _post_grey_zone_message(
    client_id: str, client_cfg: dict, sample: dict,
    today_str: str, dry_run: bool,
) -> dict:
    """灰ゾーン 1 件分の ChatWork 投稿 (テンプレ rendering + post_message)"""
    from notifiers.chatwork_notifier import ChatWorkClient
    from templates.chatwork import render
    from engine.recommendation_engine import generate_recommendation

    company = client_cfg.get("company") or {}
    chatwork_rooms = client_cfg.get("chatwork_rooms") or {}
    room_id = chatwork_rooms.get("main")

    quadrant = sample.get("quadrant")
    rec = generate_recommendation(
        rule={
            "client_id": client_id,
            "media": "meta",
            "fraud_score": sample.get("fraud_score", 0),
            "cv_rate_pct": sample.get("cv_rate_pct", 0),
            "cv_count": int(sample.get("cv_count", 0)),
            "ad_cost": sample.get("ad_cost", 0),
            "aov_jpy": (client_cfg.get("economics") or {}).get("aov_jpy", 15000),
        },
        client_data={"charter": client_cfg.get("operating_charter") or {}},
    )

    expected = rec.get("expected_outcomes") or {}
    decision_id = f"D-{today_str.replace('-', '')}-{sample.get('campaign_id', 'unk')[:6]}"

    context = {
        "client_name":   company.get("name") or client_id,
        "honorific":     company.get("honorific", "御中"),
        "today":         today_str,
        "entity_type":   "キャンペーン",
        "entity_name":   sample.get("campaign", "(unknown)"),
        "fraud_score":   round(sample.get("fraud_score", 0), 3),
        "media_avg":     "0.30 (業界推計)",
        "cv_rate":       round(sample.get("cv_rate_pct", 0), 2),
        "cv_count":      int(sample.get("cv_count", 0)),
        "ad_cost":       int(sample.get("ad_cost", 0) or 0),
        "placement_breakdown": "Phase A: 配信面別内訳は別途取得 (placement breakdown は Phase B Week 2-3 で実装)",
        "learning_status":      "学習フェーズ判定: Phase A 暫定 (週 50 CV 未満なら学習中)",
        "relearning_risk":      "ブロック実行時、過去 7 日 CV が 70% 以上残れば学習継続見込み",
        "option_a_net_yen":   int(expected.get("option_a_custom_audience_exclude", {}).get("net_yen", 0)),
        "option_b_net_yen":   int(expected.get("option_b_audience_network_block", {}).get("net_yen", 0)),
        "an_cost_savings":    int((sample.get("ad_cost", 0) or 0) * 0.15),
        "an_legitimate_cv":   max(0, int((sample.get("cv_count", 0) or 0) * 0.05)),
        "monitoring_cost_per_month": int((sample.get("ad_cost", 0) or 0) * 0.30),
        "recommended_action":          rec.get("recommended_action", "monitor_with_close_watch"),
        "recommended_action_label":    _action_label(rec.get("recommended_action")),
        "recommendation_rationale":    rec.get("rationale", "(rationale 算出中)"),
        "confidence_pct":              int(rec.get("confidence", 0.5) * 100),
        "confidence_band_basis":       f"sample_count={int(rec.get('confidence_band_pct', 50))}% band",
        "similar_past_decisions":      _format_similar(rec.get("similar_past_decisions", [])),
        "decision_id":                 decision_id,
        "charter_version":             (client_cfg.get("operating_charter") or {}).get("charter_version", "0.1-default"),
    }

    body = render("_grey_zone_decision_meta.md.j2", context)
    chat = ChatWorkClient(room_id=room_id, dry_run=dry_run)
    result = chat.post_message(body)

    posted = not result.get("skipped") and not result.get("dry_run")
    return {
        "posted":  posted,
        "skipped": result.get("skipped", False),
        "dry_run": result.get("dry_run", False),
        "decision_id": decision_id,
        "quadrant": quadrant,
    }


def _record_zynect_recommendation(client_id: str, sample: dict, today_str: str) -> str:
    """灰/黒検知の Zynect 推奨候補を block_events.yaml に「proposed」として記録

    実ブロックは Phase B Week 2-3。Phase A は「Zynect が推奨した履歴」を残すのが目的。
    """
    from engine.cv_preservation_monitor import record_block_event
    event = {
        "media": "meta",
        "method": "zynect_recommendation",
        "stage":  "proposed",
        "campaign":          sample.get("campaign", ""),
        "campaign_id":       sample.get("campaign_id", ""),
        "fraud_score":       sample.get("fraud_score", 0),
        "fraud_score_threshold": 0.60,
        "cv_rate_pct":       sample.get("cv_rate_pct", 0),
        "ad_cost":           sample.get("ad_cost", 0),
        "blocked_count":     0,
        "executed":          False,
        "quadrant":          sample.get("quadrant"),
        "today":             today_str,
    }
    rec = record_block_event(client_id, event)
    return rec.get("event_id", "")


def _action_label(action: Optional[str]) -> str:
    return {
        "custom_audience_exclude":     "Custom Audience 除外 (選択肢 A)",
        "audience_network_block":      "Audience Network 完全除外 (選択肢 B)",
        "monitor_with_close_watch":    "モニタリング継続 (選択肢 C)",
        "block_aggressive":            "Custom Audience 除外 (選択肢 A)",
        "block_with_cv_preservation":  "Audience Network 除外 (CV 保全側、選択肢 B)",
        "investigate":                 "詳細調査 (Phase B で再評価)",
    }.get(action or "", "モニタリング継続 (選択肢 C)")


def _format_similar(history: list[dict]) -> list[dict]:
    out = []
    for h in (history or [])[:5]:
        out.append({
            "date":        h.get("timestamp", "")[:10],
            "entity_name": (h.get("grey_zone_data") or {}).get("entity_name", "—"),
            "action":      h.get("customer_decision", "—"),
            "outcome":     h.get("outcome", "—"),
        })
    return out


def _load_client_cfg(client_id: str) -> dict:
    import yaml as _yaml
    path = ROOT / "config" / "clients.yaml"
    cfg = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return cfg.get("clients", {}).get(client_id, {}) or {}
