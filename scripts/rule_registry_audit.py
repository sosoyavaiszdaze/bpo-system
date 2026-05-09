#!/usr/bin/env python3
"""Print a read-only audit of YAML rule registry connectivity."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.rules.registry import load_rule_registry, summarize_rule_registry, top_rule_registry_issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit rule axis/messaging/impact connectivity.")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--limit", type=int, default=30, help="Issue row limit")
    args = parser.parse_args()

    records = load_rule_registry(Path(args.root))
    payload = {
        "summary": summarize_rule_registry(records),
        "issues": top_rule_registry_issues(records, limit=args.limit),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    summary = payload["summary"]
    print("Rule Registry Audit")
    print("===================")
    print(f"rules: {summary['total_rules']}")
    print(f"messaging coverage: {summary['messaging_coverage_pct']}% ({summary['messaging_mapped']})")
    print(f"expected impact coverage: {summary['expected_impact_coverage_pct']}% ({summary['expected_impact_rules']})")
    print(f"root cause coverage: {summary['root_cause_group_coverage_pct']}% ({summary['root_cause_group_rules']})")
    print(f"decision axis coverage: {summary['decision_axis_coverage_pct']}% ({summary['decision_axis_rules']})")
    print(f"layer counts: {summary['layer_counts']}")
    print(f"issue counts: {summary['issue_counts']}")
    print()
    print("Top issues")
    for row in payload["issues"]:
        print(f"- {row['rule_id']} [{row['layer']}] {', '.join(row['issues'])} ({row['source_path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
