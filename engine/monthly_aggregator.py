"""月次集計ロジック (ADR-005 / Day 3 E1)

IndicationState のアクティブ DB と月次アーカイブから、
ChatWork monthly_report.md.j2 のレンダリング context を組み立てる。

入力:
- IndicationState (アクティブ: open / resolved_pending / resolved_confirmed)
- archive/{YYYY-MM}.json （過去の archived レコード）

出力 context スキーマ:
{
    "client_display_name": str,
    "period_label": "YYYY-MM",
    "period_start": "YYYY-MM-DD",
    "period_end": "YYYY-MM-DD",
    "generated_at": "YYYY-MM-DD HH:MM JST",
    "summary": {
        "indications_new": int,    # 期中に first_detected_date が含まれる件数
        "completions": int,         # 期中に resolved_date が含まれる件数 + archive
        "in_progress": int,         # 月末時点で open / resolved_pending な件数
        "coverage_label": str | None,
    },
    "effect": {
        "minimum": "¥-NNN,NNN",
        "realistic": "¥-NNN,NNN",
        "optimistic": "¥-NNN,NNN" | None,
        "formula_note": str | None,
    },
    "completions_breakdown": [{
        "rule_id": str, "title": str,
        "severity_label": str, "effect_label": str,
    }],
    "open_indications": [{
        "rule_id": str, "title": str,
        "days_open": int, "severity_label": str,
    }],
    "next_focus": [str],
    "attached_pdf": str | None,
}
"""
from __future__ import annotations

import calendar
import json
import logging
import os
from datetime import datetime
from typing import Optional

log = logging.getLogger("bpo")

SEVERITY_LABEL = {
    "critical": "緊急", "high": "高", "medium": "中", "low": "低",
}


def _yen(n: int) -> str:
    """整数を ¥ 表記に。負値は ¥-N,NNN 形式"""
    return f"¥{n:,}"


def _calc_period(period: str) -> tuple[str, str]:
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-{月末}')"""
    year, month = (int(x) for x in period.split("-"))
    last = calendar.monthrange(year, month)[1]
    return f"{period}-01", f"{period}-{last:02d}"


def _in_period(date_str: Optional[str], start: str, end: str) -> bool:
    if not date_str:
        return False
    return start <= date_str <= end


def _load_archive(state, period: str) -> list[dict]:
    """{client}_indications.archive/{YYYY-MM}.json を読み込み（無ければ空）"""
    path = os.path.join(state.archive_dir, f"{period}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"アーカイブ読み込み失敗 {path}: {e}")
        return []


def _record_yen(record: dict, layer: str) -> int:
    """payload.achieved_effect.{layer} を整数化（無ければ 0）

    achieved_effect は {"minimum_yen": int, "realistic_yen": int, "optimistic_yen": int}
    の数値型 or {"minimum": "¥-58,000 / 月", ...} の文字列型に対応。
    """
    payload = record.get("payload") or {}
    eff = payload.get("achieved_effect") or {}
    val = eff.get(f"{layer}_yen")
    if isinstance(val, (int, float)):
        return int(val)
    text = eff.get(layer)
    if isinstance(text, str):
        # "¥-58,000 / 月" → -58000
        import re
        m = re.search(r"-?[\d,]+", text)
        if m:
            try:
                return int(m.group(0).replace(",", ""))
            except ValueError:
                return 0
    return 0


def _record_effect_label(record: dict) -> str:
    """完了内訳に表示する効果ラベル（realistic 優先 → minimum）"""
    payload = record.get("payload") or {}
    eff = payload.get("achieved_effect") or {}
    for key in ("realistic", "minimum", "optimistic"):
        val = eff.get(key)
        if isinstance(val, str) and val:
            return val
    realistic_yen = _record_yen(record, "realistic")
    if realistic_yen:
        return f"{_yen(realistic_yen)} / 月"
    return "効果集計中"


def _days_between(start_date: str, end_date: str) -> int:
    try:
        s = datetime.fromisoformat(start_date).date()
        e = datetime.fromisoformat(end_date).date()
        return (e - s).days
    except (ValueError, TypeError):
        return 0


def aggregate_month(
    state,  # IndicationState
    period: str,
    client_display_name: str,
    today: Optional[str] = None,
    next_focus: Optional[list[str]] = None,
    attached_pdf: Optional[str] = None,
    formula_note: Optional[str] = None,
) -> dict:
    """月次レポートのレンダリング context を組み立てる

    Args:
        state: IndicationState
        period: 'YYYY-MM' 集計対象月
        client_display_name: 表示名（[テスト] プレフィクス可）
        today: 集計実行日。省略時は今日
        next_focus: 次月重点項目リスト。None なら自動生成
        attached_pdf: 添付 PDF のファイル名
        formula_note: 効果算出の補足注記
    """
    period_start, period_end = _calc_period(period)
    today_str = today or datetime.now().strftime("%Y-%m-%d")

    # アクティブ DB（in_progress 計算用）
    active = list(state.all_indications().values())
    # アーカイブ（期中の完了済み）
    archived = _load_archive(state, period)

    # 完了集計: 期中に resolved_confirmed に到達した件数
    # アクティブ側 (resolved_confirmed) + archived 両方を見る
    completions_in_period: list[dict] = []
    for r in active + archived:
        if r.get("status") in ("resolved_confirmed", "archived") and \
                _in_period(r.get("resolved_date"), period_start, period_end):
            completions_in_period.append(r)

    # 新規指摘: first_detected_date が期中
    new_in_period = [
        r for r in active + archived
        if _in_period(r.get("first_detected_date"), period_start, period_end)
    ]

    # 月末時点で進行中 (active 側で open / resolved_pending)
    in_progress = [r for r in active if r.get("status") in ("open", "resolved_pending")]

    # 効果合計
    minimum_total = sum(_record_yen(r, "minimum") for r in completions_in_period)
    realistic_total = sum(_record_yen(r, "realistic") for r in completions_in_period)
    optimistic_total = sum(_record_yen(r, "optimistic") for r in completions_in_period)

    # 完了内訳 (severity 順、最大 5 件)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    completions_sorted = sorted(
        completions_in_period,
        key=lambda r: (severity_order.get(r.get("severity"), 9),
                       -abs(_record_yen(r, "realistic")))
    )
    completions_breakdown = [
        {
            "rule_id": r.get("rule_id", ""),
            "title": (r.get("payload") or {}).get("title") or r.get("rule_id", ""),
            "severity_label": SEVERITY_LABEL.get(r.get("severity"), r.get("severity", "")),
            "effect_label": _record_effect_label(r),
        }
        for r in completions_sorted[:5]
    ]

    # 進行中 Top（経過日数長い順、最大 5 件）
    open_indications = []
    for r in sorted(
        in_progress,
        key=lambda r: _days_between(r.get("first_detected_date", today_str), today_str),
        reverse=True,
    )[:5]:
        days = _days_between(r.get("first_detected_date", today_str), today_str)
        open_indications.append({
            "rule_id": r.get("rule_id", ""),
            "title": (r.get("payload") or {}).get("title") or r.get("rule_id", ""),
            "days_open": days,
            "severity_label": SEVERITY_LABEL.get(r.get("severity"), r.get("severity", "")),
        })

    # next_focus 自動生成（指定なき場合）
    if next_focus is None:
        next_focus = []
        for o in open_indications[:3]:
            next_focus.append(f"{o['title']}（{o['rule_id']}）の完了確定")

    # coverage_label
    total_in_period = len(new_in_period)
    if total_in_period > 0:
        rate = len(completions_in_period) / total_in_period * 100
        coverage_label = f"期中新規 {total_in_period} 件中 完了 {len(completions_in_period)} 件 ({rate:.0f}%)"
    else:
        coverage_label = None

    context = {
        "client_display_name": client_display_name,
        "period_label": period,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M JST"),
        "summary": {
            "indications_new": len(new_in_period),
            "completions": len(completions_in_period),
            "in_progress": len(in_progress),
            "coverage_label": coverage_label,
        },
        "effect": {
            "minimum": _yen(minimum_total),
            "realistic": _yen(realistic_total),
            "optimistic": _yen(optimistic_total) if optimistic_total else None,
            "formula_note": formula_note,
        },
        "completions_breakdown": completions_breakdown,
        "open_indications": open_indications,
        "next_focus": next_focus,
        "attached_pdf": attached_pdf,
    }
    return context
