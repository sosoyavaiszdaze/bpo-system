"""指摘フィルタ (ADR-005 / Day 2 C2)

検知された候補 indication 群を、ChatWork に通知すべき件に絞り込む。

3 軸:
1. severity 上限: critical / high のみ通知（medium / low は state には残すが notify しない）
2. 日次3件抑制: 1日あたり通知する新規 indication は最大 N 件
3. cooldown 7日: 同 (rule_id, platform, target_id) で前回 resolved_confirmed から N 日未満は再通知しない

呼び出し順:
    detector → upsert_detected → indication_filter → chatwork_notifier
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("bpo")

DEFAULT_ALLOWED_SEVERITIES = {"critical", "high"}
DEFAULT_DAILY_CAP = 3
DEFAULT_COOLDOWN_DAYS = 7

# 通知優先度: critical > high > medium > low
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def filter_indications(
    indications: list[dict],
    state,  # IndicationState インスタンス（循環 import を避けるため duck-typed）
    today: Optional[str] = None,
    allowed_severities: Optional[set[str]] = None,
    daily_cap: int = DEFAULT_DAILY_CAP,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
) -> list[dict]:
    """通知すべき indication を絞り込む

    Args:
        indications: detector が upsert した record リスト（各々 indication_id を持つ）
        state: IndicationState（cooldown 判定で latest_resolved_for を参照）
        today: 'YYYY-MM-DD'。省略時は今日
        allowed_severities: 許可する severity 集合
        daily_cap: 1 日に通知する最大件数（既に通知済みの件はカウントしない）
        cooldown_days: 直近 resolved_confirmed から再通知までの待機日数

    Returns:
        通知対象の record リスト（severity 高い順、最大 daily_cap 件）

    抑制理由はログに出力して可観測にする。
    """
    allowed = allowed_severities or DEFAULT_ALLOWED_SEVERITIES
    today_str = today or datetime.now().strftime("%Y-%m-%d")

    eligible: list[dict] = []
    for ind in indications:
        # 1. severity フィルタ
        sev = ind.get("severity", "medium")
        if sev not in allowed:
            log.debug(f"filter: severity={sev} 除外 {ind['indication_id']}")
            continue

        # 2. 既に通知済みはスキップ（重複通知防止）
        if ind.get("notified_at"):
            log.debug(f"filter: 既に通知済み {ind['indication_id']}")
            continue

        # 3. cooldown 判定
        if _is_in_cooldown(state, ind, today_str, cooldown_days):
            log.info(
                f"filter: cooldown中 ({cooldown_days}日) でスキップ {ind['indication_id']} "
                f"rule={ind['rule_id']} target={ind['target_id']}"
            )
            continue

        eligible.append(ind)

    # severity 高い順 → first_detected_at 古い順（先に検知された方を優先）
    eligible.sort(
        key=lambda r: (
            SEVERITY_ORDER.get(r.get("severity", "medium"), 9),
            r.get("first_detected_at", ""),
        )
    )

    # 4. daily cap
    today_notified = _count_notified_today(state, today_str)
    remaining_cap = max(0, daily_cap - today_notified)
    if remaining_cap == 0:
        log.info(f"filter: 日次cap {daily_cap} 件に到達済み、通知 0 件")
        return []

    selected = eligible[:remaining_cap]
    if len(eligible) > remaining_cap:
        log.info(
            f"filter: 日次cap {daily_cap} 件を超過、{len(eligible) - remaining_cap} 件を翌日へ繰越"
        )
    return selected


def _is_in_cooldown(state, ind: dict, today_str: str, cooldown_days: int) -> bool:
    """同 (rule_id, platform, target_id) で直近 resolved_confirmed から cooldown_days 未満なら True

    判定対象は別 indication_id（first_detected_date が違うため）。同 ID 自身は
    対象外。日付ベース判定（resolved_date）でテスト/シミュレーション可能にする。
    """
    latest_resolved = state.latest_resolved_for(
        ind["rule_id"], ind["platform"], ind["target_id"]
    )
    if latest_resolved is None:
        return False
    if latest_resolved["indication_id"] == ind["indication_id"]:
        return False
    resolved_date = latest_resolved.get("resolved_date") or latest_resolved.get("last_clean_date")
    if not resolved_date:
        return False
    try:
        resolved_dt = datetime.fromisoformat(resolved_date).date()
        today_dt = datetime.fromisoformat(today_str).date()
    except ValueError:
        return False
    return (today_dt - resolved_dt) < timedelta(days=cooldown_days)


def _count_notified_today(state, today_str: str) -> int:
    """state 上で今日通知済みの indication 数（同日多重通知防止のため）

    日付ベース判定（notified_date）でシミュレーション可能。
    """
    count = 0
    for r in state.all_indications().values():
        if r.get("notified_date") == today_str:
            count += 1
    return count
