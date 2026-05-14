#!/usr/bin/env python3
"""Run the production Rule Quality Gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.rules.quality_gate import audit_rule_quality
from engine.rules.registry import load_rule_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit customer-visible rules for production readiness.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--family", default=None, help="Optional family filter, e.g. meta/adtruth/google")
    parser.add_argument(
        "--include-internal",
        action="store_true",
        help="Also audit non-customer-visible rules.",
    )
    parser.add_argument(
        "--fail-on",
        choices=("blocker", "warning", "none"),
        default="blocker",
    )
    args = parser.parse_args()

    result = audit_rule_quality(
        load_rule_registry(Path(args.root)),
        family=args.family,
        customer_visible_only=not args.include_internal,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on == "none":
        return 0
    if args.fail_on == "warning" and (result["blocker_count"] or result["warning_count"]):
        return 2
    if args.fail_on == "blocker" and result["blocker_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
