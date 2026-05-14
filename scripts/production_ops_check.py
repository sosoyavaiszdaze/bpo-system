#!/usr/bin/env python3
"""Run the non-ChatWork production operations loop.

This is the daily backbone for the production OS excluding AdTruth and
customer notification:

1. sync Rule Registry into DB
2. run Rule Quality Gate
3. update due Outcome Tracker measurements and rule rollups
4. evaluate Operational Readiness

The command is intentionally review-safe: it writes operational DB state, but
does not send ChatWork messages or mutate YAML rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.rules.quality_gate import audit_rule_quality
from engine.rules.registry import load_rule_registry
from engine.stores.db import DEFAULT_DB_PATH, transaction
from engine.stores.jobs import finish_job, start_job
from engine.stores.readiness import evaluate_operational_readiness
from engine.stores.rules import registry_summary, sync_rule_registry
from scripts.update_outcome_measurements import update_outcomes


def run_production_ops_check(
    *,
    db_path: Path | str | None = None,
    root: Path | str | None = None,
    today: str | None = None,
    client_id: str | None = None,
    kpi_json: str | None = None,
    fail_on: str = "blocker",
) -> dict:
    root_path = Path(root) if root else ROOT
    today = today or datetime.now().strftime("%Y-%m-%d")
    db = db_path or DEFAULT_DB_PATH
    records = load_rule_registry(root_path)

    with transaction(db) as conn:
        job_run_id = start_job(conn, "production_ops_check", client_id)

    result: dict = {
        "ok": True,
        "today": today,
        "client_id": client_id,
        "rule_registry": {},
        "quality_gate": {},
        "outcomes": {},
        "readiness": {},
        "errors": [],
    }
    try:
        with transaction(db) as conn:
            sync = sync_rule_registry(conn, records)
            result["rule_registry"] = {**sync, "summary": registry_summary(conn)}

        quality = audit_rule_quality(records, customer_visible_only=True)
        result["quality_gate"] = quality

        result["outcomes"] = update_outcomes(
            db_path=db,
            client_id=client_id,
            today=today,
            kpi_json=kpi_json,
        )

        with transaction(db) as conn:
            readiness = evaluate_operational_readiness(conn, records=records)
            result["readiness"] = readiness
            finish_job(
                conn,
                job_run_id,
                "success" if _passes(result, fail_on) else "partial_failure",
                metrics=_metrics(result),
                errors=_blocking_errors(result, fail_on),
            )
        result["ok"] = _passes(result, fail_on)
        return result
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"{exc.__class__.__name__}: {exc}")
        with transaction(db) as conn:
            finish_job(conn, job_run_id, "failed", errors=result["errors"])
        return result


def _passes(result: dict, fail_on: str) -> bool:
    if fail_on == "none":
        return not result.get("errors")
    if result.get("errors"):
        return False
    if fail_on == "warning":
        return not (
            result.get("quality_gate", {}).get("blocker_count")
            or result.get("quality_gate", {}).get("warning_count")
            or result.get("readiness", {}).get("blocker_count")
            or result.get("readiness", {}).get("warning_count")
        )
    return not (
        result.get("quality_gate", {}).get("blocker_count")
        or result.get("readiness", {}).get("blocker_count")
    )


def _blocking_errors(result: dict, fail_on: str) -> list[str]:
    errors = list(result.get("errors") or [])
    if fail_on in {"blocker", "warning"}:
        q = result.get("quality_gate", {})
        r = result.get("readiness", {})
        if q.get("blocker_count"):
            errors.append(f"quality_gate_blockers={q.get('blocker_count')}")
        if r.get("blocker_count"):
            errors.append(f"readiness_blockers={r.get('blocker_count')}")
    if fail_on == "warning":
        q = result.get("quality_gate", {})
        r = result.get("readiness", {})
        if q.get("warning_count"):
            errors.append(f"quality_gate_warnings={q.get('warning_count')}")
        if r.get("warning_count"):
            errors.append(f"readiness_warnings={r.get('warning_count')}")
    return errors


def _metrics(result: dict) -> dict:
    quality = result.get("quality_gate") or {}
    readiness = result.get("readiness") or {}
    outcomes = result.get("outcomes") or {}
    registry = (result.get("rule_registry") or {}).get("summary") or {}
    return {
        "quality_blockers": quality.get("blocker_count", 0),
        "quality_warnings": quality.get("warning_count", 0),
        "readiness_blockers": readiness.get("blocker_count", 0),
        "readiness_warnings": readiness.get("warning_count", 0),
        "outcome_measurements_updated": outcomes.get("updated_measurements", 0),
        "rule_rollups_updated": outcomes.get("rollups_updated", 0),
        "rule_messaging_coverage_pct": registry.get("messaging_coverage_pct", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zynect production operations checks without ChatWork.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--root", default=str(ROOT), help="Repository root")
    parser.add_argument("--client", dest="client_id", help="Optional single client")
    parser.add_argument("--today", help="YYYY-MM-DD")
    parser.add_argument("--kpi-json", help='Optional JSON: {"client_id":{"cpa":8000,"cv_count":40}}')
    parser.add_argument("--fail-on", choices=("blocker", "warning", "none"), default="blocker")
    args = parser.parse_args()

    payload = run_production_ops_check(
        db_path=args.db,
        root=args.root,
        today=args.today,
        client_id=args.client_id,
        kpi_json=args.kpi_json,
        fail_on=args.fail_on,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
