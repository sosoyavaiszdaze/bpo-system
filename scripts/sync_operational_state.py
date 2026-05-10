"""Sync legacy YAML/JSON runtime state into the operational DB.

This is the repeatable, day-to-day version of migrate_state_to_db.py. It is
additive and idempotent; legacy files remain the source of truth until the
write path is fully moved to DB-backed stores.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.migrate_state_to_db import migrate


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync runtime state into state/zynect.db")
    parser.add_argument("--db", default=str(ROOT / "state" / "zynect.db"), help="SQLite DB path")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument("--dry-run", action="store_true", help="rollback after sync")
    args = parser.parse_args()

    summary = migrate(
        root=Path(args.root),
        db_path=Path(args.db),
        dry_run=args.dry_run,
        job_name="sync_operational_state",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not summary["errors"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
