#!/usr/bin/env python3
"""Update due Outcome Tracker measurements without sending ChatWork."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.stores.clients import list_client_ids
from engine.stores.db import DEFAULT_DB_PATH, connect, transaction
from engine.stores.jobs import finish_job, start_job
from engine.stores.outcomes import refresh_rule_outcome_rollups, update_due_outcome_measurements


def update_outcomes(
    *,
    db_path: Path | str | None = None,
    client_id: str | None = None,
    today: str | None = None,
    kpi_json: str | None = None,
) -> dict:
    today = today or datetime.now().strftime("%Y-%m-%d")
    if kpi_json:
        kpis_by_client = json.loads(kpi_json)
    else:
        kpis_by_client = {}

    with connect(db_path) as read_conn:
        clients = [client_id] if client_id else list_client_ids(read_conn)

    summary = {"clients": 0, "updated_measurements": 0, "rollups_updated": 0, "errors": []}
    for cid in clients:
        job_run_id = None
        with transaction(db_path) as conn:
            job_run_id = start_job(conn, "update_outcome_measurements", cid)
        try:
            current_kpis = kpis_by_client.get(cid) if isinstance(kpis_by_client, dict) else None
            if current_kpis is None:
                current_kpis = _fetch_current_kpis(cid)
            with transaction(db_path) as conn:
                updated = update_due_outcome_measurements(conn, client_id=cid, current_kpis=current_kpis, today=today)
                rollups = refresh_rule_outcome_rollups(conn)
                finish_job(
                    conn,
                    job_run_id,
                    "success",
                    metrics={
                        "updated_measurements": updated,
                        "rollups_updated": rollups.get("rules_updated", 0),
                    },
                )
            summary["clients"] += 1
            summary["updated_measurements"] += updated
            summary["rollups_updated"] = rollups.get("rules_updated", 0)
        except Exception as e:
            summary["errors"].append(f"{cid}: {e.__class__.__name__}: {e}")
            with transaction(db_path) as conn:
                finish_job(conn, job_run_id, "failed", errors=[str(e)])
    return summary


def _fetch_current_kpis(client_id: str) -> dict:
    from scripts.daily_chatwork_check import fetch_audit_results

    audit = fetch_audit_results(client_id)
    ads = (audit or {}).get("ads_audit") or {}
    summary = (ads.get("platform_summary") or {}).get("meta") or {}
    return {
        "cpa": summary.get("avg_cpa"),
        "cv_count": summary.get("conversions"),
        "roas": summary.get("avg_roas"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update due Outcome Tracker measurements")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--client", dest="client_id", help="Client id; default all DB clients")
    parser.add_argument("--today", help="YYYY-MM-DD")
    parser.add_argument("--kpi-json", help='Optional JSON: {"client_id":{"cpa":8000,"cv_count":40,"roas":2.1}}')
    args = parser.parse_args()

    summary = update_outcomes(db_path=args.db, client_id=args.client_id, today=args.today, kpi_json=args.kpi_json)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not summary["errors"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
