import json
from pathlib import Path

import yaml

from engine.stores.cases import get_case, upsert_case_from_indication
from engine.stores.db import connect
from engine.stores.jobs import client_health, record_job
from scripts.client_health import build_health_report
from scripts.migrate_state_to_db import migrate


def test_schema_initializes_and_case_upsert(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    record = {
        "indication_id": "pilotton:F-MF-01:meta:acct:2026-05-09",
        "client_id": "pilotton",
        "rule_id": "F-MF-01",
        "severity": "high",
        "status": "open",
        "first_detected_at": "2026-05-09T00:00:00+00:00",
        "first_detected_date": "2026-05-09",
        "last_detected_at": "2026-05-09T00:00:00+00:00",
        "last_detected_date": "2026-05-09",
        "payload": {"title": "CVイベント確認"},
        "history": [{"at": "2026-05-09T00:00:00+00:00", "event": "detected"}],
    }

    case_id = upsert_case_from_indication(conn, record)
    conn.commit()

    case = get_case(conn, case_id)
    assert case["client_id"] == "pilotton"
    assert case["rule_id"] == "F-MF-01"
    assert case["status"] == "open"
    assert case["payload"]["title"] == "CVイベント確認"
    events = conn.execute("SELECT event_type FROM case_events WHERE case_id = ?", (case_id,)).fetchall()
    assert [e["event_type"] for e in events] == ["detected"]


def test_migrate_state_to_db_imports_clients_cases_and_responses(tmp_path):
    root = _sample_root(tmp_path)
    db_path = tmp_path / "state" / "zynect.db"

    summary = migrate(root=root, db_path=db_path)

    assert summary["clients"] == 1
    assert summary["indications"] == 1
    assert summary["responses"] == 1
    assert summary["chatwork_sent"] == 1
    assert summary["job_runs"] == 2
    assert summary["errors"] == []

    conn = connect(db_path)
    try:
        case = conn.execute("SELECT * FROM operational_cases WHERE client_id = 'pilotton'").fetchone()
        assert case["rule_id"] == "F-MF-01"
        response = conn.execute("SELECT * FROM client_responses WHERE client_id = 'pilotton'").fetchone()
        assert response["case_id"] == case["case_id"]
        assert response["status"] == "wants_help"
        health = client_health(conn, "pilotton")
        assert health["open_cases_count"] == 1
        assert health["latest_job"]["job_name"] == "migrate_state_to_db"
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path):
    root = _sample_root(tmp_path)
    db_path = tmp_path / "state" / "zynect.db"

    migrate(root=root, db_path=db_path)
    migrate(root=root, db_path=db_path)

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM operational_cases").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM client_responses").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM chatwork_sent").fetchone()["n"] == 1
    finally:
        conn.close()


def test_sync_imports_archived_indications_and_uses_sync_job_name(tmp_path):
    root = _sample_root(tmp_path)
    archive_dir = root / "outputs" / "chatwork_state" / "pilotton_indications.archive"
    archive_dir.mkdir(parents=True)
    archived = {
        "indication_id": "pilotton:F-MF-02:meta:acct:2026-05-01",
        "client_id": "pilotton",
        "rule_id": "F-MF-02",
        "platform": "meta",
        "target_id": "acct",
        "severity": "medium",
        "status": "archived",
        "first_detected_at": "2026-05-01T00:00:00+00:00",
        "first_detected_date": "2026-05-01",
        "resolved_at": "2026-05-04T00:00:00+00:00",
        "resolved_date": "2026-05-04",
        "payload": {"title": "Archived"},
        "history": [{"at": "2026-05-04T00:00:00+00:00", "event": "archived"}],
    }
    (archive_dir / "2026-05.json").write_text(json.dumps([archived], ensure_ascii=False), encoding="utf-8")
    db_path = tmp_path / "state" / "zynect.db"

    summary = migrate(root=root, db_path=db_path, job_name="sync_operational_state")

    conn = connect(db_path)
    try:
        assert summary["indications"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM operational_cases").fetchone()["n"] == 2
        latest = conn.execute(
            "SELECT job_name FROM job_runs WHERE client_id = 'pilotton' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert latest["job_name"] == "sync_operational_state"
    finally:
        conn.close()


def test_client_health_report_json_shape(tmp_path):
    root = _sample_root(tmp_path)
    db_path = tmp_path / "state" / "zynect.db"
    migrate(root=root, db_path=db_path)

    rows = build_health_report(db_path)

    assert len(rows) == 1
    assert rows[0]["client_id"] == "pilotton"
    assert "open_cases_count" in rows[0]
    assert "latest_job" in rows[0]


def test_job_store_records_success_and_failure(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    record_job(conn, "daily_chatwork_check", "pilotton", "success", metrics={"sent": 1})
    record_job(conn, "daily_chatwork_check", "yamamoto_demo", "failed", errors=["token missing"])
    conn.commit()

    h1 = client_health(conn, "pilotton")
    h2 = client_health(conn, "yamamoto_demo")

    assert h1["latest_job"]["status"] == "success"
    assert h1["last_successful_run_at"]
    assert h2["latest_job"]["status"] == "failed"
    assert h2["latest_job"]["errors"] == ["token missing"]


def _sample_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "outputs" / "chatwork_state").mkdir(parents=True)
    (root / "outputs" / "chatwork_responses").mkdir(parents=True)
    (root / "outputs" / "auto_proposal_history").mkdir(parents=True)
    (root / "state").mkdir(parents=True)

    (root / "config" / "clients.yaml").write_text(
        yaml.safe_dump({
            "clients": {
                "pilotton": {
                    "display_name": "株式会社パイロットン",
                    "vertical": "ec_d2c",
                    "ec_platform": "ecforce",
                    "chatwork_rooms": {"main": "12345"},
                }
            }
        }, allow_unicode=True),
        encoding="utf-8",
    )
    indication = {
        "client_id": "pilotton",
        "version": 1,
        "indications": {
            "pilotton:F-MF-01:meta:acct:2026-05-09": {
                "indication_id": "pilotton:F-MF-01:meta:acct:2026-05-09",
                "client_id": "pilotton",
                "rule_id": "F-MF-01",
                "platform": "meta",
                "target_id": "acct",
                "severity": "high",
                "status": "open",
                "first_detected_at": "2026-05-09T00:00:00+00:00",
                "first_detected_date": "2026-05-09",
                "last_detected_at": "2026-05-09T00:00:00+00:00",
                "last_detected_date": "2026-05-09",
                "notified_at": "2026-05-09T09:00:00+09:00",
                "payload": {"title": "CVイベント確認"},
                "history": [{"at": "2026-05-09T00:00:00+00:00", "event": "detected"}],
            }
        },
    }
    (root / "outputs" / "chatwork_state" / "pilotton_indications.json").write_text(
        json.dumps(indication, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "outputs" / "chatwork_responses" / "pilotton.yaml").write_text(
        yaml.safe_dump({
            "client_id": "pilotton",
            "responses": {
                "F-MF-01": {
                    "rule_id": "F-MF-01",
                    "answer_code": "C",
                    "answer_label": "確認したい",
                    "status": "wants_help",
                    "raw_message": "C",
                    "chatwork_message_id": "2100000000000000001",
                    "answered_at": "2026-05-09T10:00:00+09:00",
                    "source": "chatwork_reply",
                    "expires_at": "2026-05-23T10:00:00+09:00",
                }
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (root / "state" / "chatwork_sent.json").write_text(
        json.dumps({
            "idem-1": {
                "client_id": "pilotton",
                "room_id": "12345",
                "message_id": "999",
                "sent_at": "2026-05-09T09:00:00+09:00",
            }
        }),
        encoding="utf-8",
    )
    return root
