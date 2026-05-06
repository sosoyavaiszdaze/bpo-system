"""ChatWork 顧客回答 ACK ストア

ChatWork 返信を取り込んだ後の「ご回答ありがとうございます」返信を、
同じ顧客メッセージに対して二重送信しないための軽量ストア。

保存先: outputs/chatwork_response_acks/{client_id}.yaml
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACK_DIR = ROOT / "outputs" / "chatwork_response_acks"
JST = timezone(timedelta(hours=9))


def load_acked_message_ids(client_id: str) -> set[str]:
    """ACK 済み ChatWork message_id の set を返す。"""
    data = _load(client_id)
    return {str(v) for v in data.get("acked_message_ids", []) if v is not None}


def mark_acked_message_ids(client_id: str, message_ids: list[str]) -> None:
    """指定 message_id を ACK 済みとして保存する。"""
    ids = {str(v) for v in message_ids if v is not None and str(v)}
    if not ids:
        return
    data = _load(client_id)
    existing = {str(v) for v in data.get("acked_message_ids", []) if v is not None}
    data["client_id"] = client_id
    data["last_acked_at"] = datetime.now(JST).isoformat(timespec="seconds")
    data["acked_message_ids"] = sorted(existing | ids, key=_message_sort_key)
    _write(client_id, data)


def _load(client_id: str) -> dict:
    path = ACK_DIR / f"{client_id}.yaml"
    if not path.exists():
        return {"client_id": client_id, "last_acked_at": None, "acked_message_ids": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"client_id": client_id, "last_acked_at": None, "acked_message_ids": []}
    data.setdefault("client_id", client_id)
    data.setdefault("last_acked_at", None)
    data.setdefault("acked_message_ids", [])
    return data


def _write(client_id: str, data: dict) -> None:
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    path = ACK_DIR / f"{client_id}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _message_sort_key(v: str) -> tuple[int, str]:
    try:
        return (int(v), v)
    except (TypeError, ValueError):
        return (0, str(v))
