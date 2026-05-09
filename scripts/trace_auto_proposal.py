#!/usr/bin/env python3
"""Persist coarse decision traces for auto proposal rule selection."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.auto_proposal_engine import collect_eligible_rules
from engine.rules.decision_trace import build_selection_traces
from engine.stores.db import DEFAULT_DB_PATH, transaction
from engine.stores.decision_traces import record_trace, trace_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace auto proposal selection into decision_traces.")
    parser.add_argument("--client", required=True)
    parser.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = collect_eligible_rules(args.client, today=args.today)
    traces = build_selection_traces(
        client_id=args.client,
        evaluation_date=args.today,
        loaded_rules=result.get("loaded_rules") or [],
        matched_rules=result.get("matched_rules") or [],
        eligible_rules=result.get("eligible_rules") or [],
        selected_rules=result.get("selected") or [],
    )
    with transaction(args.db) as conn:
        for trace in traces:
            record_trace(conn, **trace)
        summary = trace_summary(conn, client_id=args.client)
    payload = {"ok": True, "client_id": args.client, "traces": len(traces), "summary": summary}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"recorded traces={len(traces)} client={args.client} db={args.db}")
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
