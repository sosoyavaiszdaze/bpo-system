#!/usr/bin/env python3
"""Sync YAML rule metadata into the operational DB rule registry."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.rules.registry import load_rule_registry
from engine.stores.db import DEFAULT_DB_PATH, transaction
from engine.stores.rules import registry_summary, sync_rule_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync YAML rules into DB-backed Rule Registry.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    records = load_rule_registry(Path(args.root))
    with transaction(args.db) as conn:
        result = sync_rule_registry(conn, records)
        summary = registry_summary(conn)
    payload = {"ok": True, **result, "summary": summary}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"synced rules={result['rules_synced']} issues={result['issues_synced']} db={args.db}")
        print(f"messaging={summary['messaging_coverage_pct']}% impact={summary['expected_impact_coverage_pct']}%")
        print(f"root_cause={summary['root_cause_group_coverage_pct']}% axis={summary['decision_axis_coverage_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
