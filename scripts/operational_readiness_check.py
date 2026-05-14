#!/usr/bin/env python3
"""Run production operational readiness checks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.rules.registry import load_rule_registry
from engine.stores.db import transaction
from engine.stores.readiness import evaluate_operational_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Zynect operational readiness.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--max-job-freshness-hours", type=float, default=26.0)
    parser.add_argument("--max-rule-draft-backlog", type=int, default=30)
    parser.add_argument("--fail-on", choices=("blocker", "none"), default="blocker")
    args = parser.parse_args()

    records = load_rule_registry(Path(args.root))
    with transaction(args.db) as conn:
        result = evaluate_operational_readiness(
            conn,
            records=records,
            max_job_freshness_hours=args.max_job_freshness_hours,
            max_rule_draft_backlog=args.max_rule_draft_backlog,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on == "blocker" and result["blocker_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
