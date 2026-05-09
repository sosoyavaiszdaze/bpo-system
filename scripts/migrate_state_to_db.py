"""Import existing JSON/YAML runtime state into ADR-018 SQLite tables.

This migration is additive: it does not delete or modify existing state files.
It is safe to run repeatedly because primary keys / unique indexes de-dupe rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.stores.cases import add_case_event, upsert_case_from_indication
from engine.stores.clients import list_client_ids, upsert_client
from engine.stores.db import connect, json_dumps, utc_now
from engine.stores.jobs import record_job


def migrate(root: Path = ROOT, db_path: Path | None = None, dry_run: bool = False) -> dict:
    conn = connect(db_path)
    summary = {
        "clients": 0,
        "indications": 0,
        "case_events": 0,
        "responses": 0,
        "chatwork_sent": 0,
        "auto_proposal_history": 0,
        "job_runs": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    try:
        _import_clients(conn, root, summary)
        _import_indications(conn, root, summary)
        _import_responses(conn, root, summary)
        _import_chatwork_sent(conn, root, summary)
        _import_auto_proposal_history(conn, root, summary)
        status = "success" if not summary["errors"] else "partial_failure"
        metrics = {k: v for k, v in summary.items() if isinstance(v, int)}
        record_job(conn, "migrate_state_to_db", None, status, errors=summary["errors"], metrics=metrics)
        summary["job_runs"] += 1
        for client_id in list_client_ids(conn):
            record_job(conn, "migrate_state_to_db", client_id, status, errors=summary["errors"], metrics=metrics)
            summary["job_runs"] += 1
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception as e:
        conn.rollback()
        summary["errors"].append(f"{e.__class__.__name__}: {e}")
        raise
    finally:
        conn.close()
    return summary


def _import_clients(conn, root: Path, summary: dict) -> None:
    path = root / "config" / "clients.yaml"
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for client_id, cfg in (data.get("clients") or {}).items():
        upsert_client(conn, client_id, cfg or {})
        summary["clients"] += 1


def _import_indications(conn, root: Path, summary: dict) -> None:
    for path in sorted((root / "outputs" / "chatwork_state").glob("*_indications.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError) as e:
            summary["errors"].append(f"indication load failed {path}: {e}")
            continue
        for record in (data.get("indications") or {}).values():
            case_id = upsert_case_from_indication(conn, record)
            summary["indications"] += 1
            summary["case_events"] += len(record.get("history") or [])
            if record.get("notified_at"):
                _insert_notification_from_indication(conn, case_id, record)


def _insert_notification_from_indication(conn, case_id: str, record: dict) -> None:
    payload = record.get("payload") or {}
    notified_at = record.get("notified_at") or utc_now()
    notification_id = _hash("notification", case_id, notified_at)
    conn.execute(
        """
        INSERT OR IGNORE INTO notification_messages (
          notification_id, client_id, case_id, channel, status, sent_at, payload_json
        ) VALUES (?, ?, ?, 'chatwork', 'sent', ?, ?)
        """,
        (notification_id, record.get("client_id", ""), case_id, notified_at, json_dumps(payload)),
    )


def _import_responses(conn, root: Path, summary: dict) -> None:
    for path in sorted((root / "outputs" / "chatwork_responses").glob("*.yaml")):
        if ".bak." in path.name:
            continue
        client_id = path.stem
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            summary["errors"].append(f"response load failed {path}: {e}")
            continue
        for rule_id, rec in (data.get("responses") or {}).items():
            response_id = _hash("response", client_id, rule_id, rec.get("chatwork_message_id") or rec.get("answered_at") or "")
            case_id = _latest_case_id_for_rule(conn, client_id, rule_id)
            conn.execute(
                """
                INSERT OR REPLACE INTO client_responses (
                  response_id, client_id, rule_id, case_id, answer_code, answer_label,
                  status, raw_message, chatwork_message_id, answered_at, source, expires_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response_id,
                    client_id,
                    rule_id,
                    case_id,
                    rec.get("answer_code"),
                    rec.get("answer_label"),
                    rec.get("status") or "unknown",
                    rec.get("raw_message"),
                    str(rec.get("chatwork_message_id")) if rec.get("chatwork_message_id") else None,
                    rec.get("answered_at"),
                    rec.get("source") or "chatwork_reply",
                    rec.get("expires_at"),
                    json_dumps(rec),
                ),
            )
            if case_id:
                add_case_event(
                    conn,
                    case_id=case_id,
                    client_id=client_id,
                    event_type="client_response",
                    actor_type="client",
                    actor_id=str(rec.get("chatwork_message_id") or ""),
                    event_at=rec.get("answered_at") or utc_now(),
                    message=rec.get("raw_message"),
                    payload=rec,
                )
            summary["responses"] += 1


def _import_chatwork_sent(conn, root: Path, summary: dict) -> None:
    path = root / "state" / "chatwork_sent.json"
    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError) as e:
        summary["errors"].append(f"chatwork_sent load failed {path}: {e}")
        return
    entries = data.items() if isinstance(data, dict) else []
    for key, rec in entries:
        rec = rec if isinstance(rec, dict) else {"value": rec}
        conn.execute(
            """
            INSERT OR REPLACE INTO chatwork_sent (
              idempotency_key, client_id, room_id, message_id, sent_at, body_hash, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(key),
                rec.get("client_id"),
                str(rec.get("room_id")) if rec.get("room_id") else None,
                str(rec.get("message_id")) if rec.get("message_id") else None,
                rec.get("sent_at") or rec.get("ts"),
                rec.get("body_hash"),
                json_dumps(rec),
            ),
        )
        summary["chatwork_sent"] += 1


def _import_auto_proposal_history(conn, root: Path, summary: dict) -> None:
    for path in sorted((root / "outputs" / "auto_proposal_history").glob("*.yaml")):
        client_id = path.stem
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            summary["errors"].append(f"auto_proposal_history load failed {path}: {e}")
            continue
        for rule_id, rec in data.items():
            if not isinstance(rec, dict):
                continue
            event_at = rec.get("last_sent_at") or rec.get("sent_at") or utc_now()
            case_id = _latest_case_id_for_rule(conn, client_id, rule_id)
            if case_id:
                add_case_event(
                    conn,
                    case_id=case_id,
                    client_id=client_id,
                    event_type="auto_proposal_history",
                    actor_type="system",
                    event_at=event_at,
                    payload={"rule_id": rule_id, **rec},
                )
                summary["case_events"] += 1
            summary["auto_proposal_history"] += 1


def _latest_case_id_for_rule(conn, client_id: str, rule_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT case_id FROM operational_cases
        WHERE client_id = ? AND rule_id = ?
        ORDER BY first_detected_at DESC
        LIMIT 1
        """,
        (client_id, rule_id),
    ).fetchone()
    return row["case_id"] if row else None


def _hash(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import existing runtime state into state/zynect.db")
    parser.add_argument("--db", default=str(ROOT / "state" / "zynect.db"), help="SQLite DB path")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument("--dry-run", action="store_true", help="rollback after import")
    args = parser.parse_args()

    summary = migrate(root=Path(args.root), db_path=Path(args.db), dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not summary["errors"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
