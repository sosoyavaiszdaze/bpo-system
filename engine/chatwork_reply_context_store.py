"""ChatWork 返信文脈ストア

直近の統合 TODO 通知で「上から何番目にどの rule_id を表示したか」を保存する。
これにより、顧客が通知文どおりに `A` / `C、C` のような 1 文字返信をした場合でも、
直近通知の表示順に沿って rule_id へ割り当てられる。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = ROOT / "outputs" / "chatwork_reply_context"
JST = timezone(timedelta(hours=9))


def save_latest_context(client_id: str, context: dict) -> None:
    """直近通知の返信文脈を保存する。"""
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = CONTEXT_DIR / f"{client_id}.yaml"
    existing = load_latest_context(client_id) or {}
    history = existing.get("history") or []

    latest = {
        "client_id": client_id,
        "message_id": str(context.get("message_id") or ""),
        "sent_at": context.get("sent_at") or _now_iso(),
        "today": context.get("today"),
        "displayed_rule_ids": list(context.get("displayed_rule_ids") or []),
    }
    history.append(latest)
    data = {
        "client_id": client_id,
        "latest": latest,
        # 直近 10 件だけ保持。古い通知への返信は message_id 比較で基本拾わない。
        "history": history[-10:],
    }
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def load_latest_context(client_id: str) -> Optional[dict]:
    """直近通知の返信文脈を返す。無ければ None。"""
    path = CONTEXT_DIR / f"{client_id}.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    latest = data.get("latest") or {}
    if not latest.get("displayed_rule_ids"):
        return None
    return latest


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")
