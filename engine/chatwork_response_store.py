"""ChatWork 顧客回答ストア (5/8 v3 ingestion)

責務: ChatWork の A/B/C 形式回答を永続化し、次回通知の選定 / 優先度 / 除外に
      反映できるようにする。

保存先: outputs/chatwork_responses/{client_id}.yaml

スキーマ:
    client_id: pilotton
    last_ingested_at: '2026-05-08T10:30:00+09:00'
    responses:
      F-AH-04:
        rule_id: F-AH-04
        answer_code: A
        answer_label: 認証済み
        raw_message: 'F-AH-04 A'
        chatwork_message_id: '2103768587398557696'
        answered_at: '2026-05-08T10:25:13+09:00'
        source: chatwork_reply
        status: confirmed_done       # confirmed_done / not_done / wants_help / not_applicable
        expires_at: '2026-08-06T10:25:13+09:00'  # status 別 default 期間後

status × expires_at 既定:
    confirmed_done   : 90 日 (3 ヶ月、操作変更や仕様変更で再確認すべき)
    not_applicable   : 365 日 (運用憲章レベル、長期除外)
    wants_help       : 14 日 (Zynect 担当が次回詳細を案内するまで保留)
    not_done         : 7 日 (1 週間 reminder cooldown)

主要 API:
    - load_responses(client_id) -> dict
    - save_response(client_id, response_record) -> str  (returns response_id / rule_id)
    - get_active_response(client_id, rule_id, today=None) -> dict | None
    - is_suppressed(client_id, rule_id, today=None) -> bool
    - list_active_responses(client_id, today=None) -> list[dict]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
RESPONSES_DIR = ROOT / "outputs" / "chatwork_responses"

JST = timezone(timedelta(hours=9))


# ========== status / expires 既定 ==========

VALID_STATUSES = {"confirmed_done", "not_done", "wants_help", "not_applicable"}

STATUS_DEFAULT_TTL_DAYS = {
    "confirmed_done":   90,    # 3 ヶ月、運用変化で再確認
    "not_applicable":   365,   # 1 年、運用憲章レベル
    "wants_help":       14,    # 2 週間、Zynect 担当が手動対応するまで
    "not_done":         7,     # 1 週間、reminder cooldown
}


# ========== Public API ==========

def load_responses(client_id: str) -> dict:
    """outputs/chatwork_responses/{client_id}.yaml を全文ロード"""
    path = RESPONSES_DIR / f"{client_id}.yaml"
    if not path.exists():
        return {"client_id": client_id, "responses": {}, "last_ingested_at": None}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        log.warning(f"chatwork_response_store: load failed for {client_id}: {e}")
        return {"client_id": client_id, "responses": {}, "last_ingested_at": None}
    data.setdefault("client_id", client_id)
    data.setdefault("responses", {})
    data.setdefault("last_ingested_at", None)
    return data


def save_response(client_id: str, response_record: dict) -> str:
    """1 件の response を上書き保存

    Args:
        response_record: {rule_id, answer_code, answer_label, raw_message,
                          chatwork_message_id, answered_at, source, status,
                          expires_at (optional, status から自動算出)}

    Returns:
        rule_id (上書き対象キー)
    """
    rule_id = response_record.get("rule_id")
    if not rule_id:
        raise ValueError("response_record.rule_id is required")

    status = response_record.get("status")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}, must be in {sorted(VALID_STATUSES)}")

    # expires_at 自動算出
    if not response_record.get("expires_at"):
        ttl_days = STATUS_DEFAULT_TTL_DAYS.get(status, 30)
        answered_at = _parse_iso(response_record.get("answered_at")) or _now()
        expires_at = answered_at + timedelta(days=ttl_days)
        response_record["expires_at"] = expires_at.isoformat(timespec="seconds")

    response_record.setdefault("source", "chatwork_reply")
    response_record.setdefault("answered_at", _now().isoformat(timespec="seconds"))

    data = load_responses(client_id)
    data["responses"][rule_id] = response_record
    data["last_ingested_at"] = _now().isoformat(timespec="seconds")
    _atomic_write(client_id, data)
    return rule_id


def get_active_response(
    client_id: str, rule_id: str, today: Optional[datetime] = None,
) -> Optional[dict]:
    """指定 rule_id の有効な response を返す。expires_at 超過なら None"""
    data = load_responses(client_id)
    rec = (data.get("responses") or {}).get(rule_id)
    if not rec:
        return None
    expires = _parse_iso(rec.get("expires_at"))
    today = today or _now()
    if expires and expires < today:
        return None  # 期限切れ
    return rec


def is_suppressed(
    client_id: str, rule_id: str, today: Optional[datetime] = None,
) -> bool:
    """この rule_id は次回通知から除外すべきか?

    True を返す条件:
        - confirmed_done で expires_at 内
        - not_applicable で expires_at 内
    False を返す条件:
        - 回答なし
        - not_done (reminder 対象、ただし cooldown は別途)
        - wants_help (次回は詳細通知に進める、除外ではない)
        - 期限切れ
    """
    rec = get_active_response(client_id, rule_id, today=today)
    if not rec:
        return False
    return rec.get("status") in ("confirmed_done", "not_applicable")


def list_active_responses(
    client_id: str, today: Optional[datetime] = None,
) -> list[dict]:
    """期限内の全 response を返す"""
    data = load_responses(client_id)
    today = today or _now()
    out = []
    for rec in (data.get("responses") or {}).values():
        expires = _parse_iso(rec.get("expires_at"))
        if expires and expires < today:
            continue
        out.append(rec)
    return out


def get_status_map(
    client_id: str, today: Optional[datetime] = None,
) -> dict:
    """rule_id → status の dict を返す (期限切れは除外)

    daily_todo_builder が参照: confirmed_done / not_applicable は除外、
    wants_help / not_done は通知側で扱い分岐。
    """
    today = today or _now()
    out = {}
    for rec in list_active_responses(client_id, today=today):
        rid = rec.get("rule_id")
        if rid:
            out[rid] = rec.get("status")
    return out


# ========== Private ==========

def _now() -> datetime:
    return datetime.now(JST)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except (ValueError, TypeError):
        return None


def _atomic_write(client_id: str, data: dict) -> None:
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    path = RESPONSES_DIR / f"{client_id}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
