"""Record an Outcome Tracker measurement into the operational DB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.stores.db import connect
from engine.stores.outcomes import record_outcome


def _load_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a measured outcome")
    parser.add_argument("--db", default=str(ROOT / "state" / "zynect.db"), help="SQLite DB path")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--client", required=True, dest="client_id")
    parser.add_argument("--metric", required=True, help="e.g. cpa_change_pct, roas_change_pct, ops_hours_saved")
    parser.add_argument("--baseline", required=True, type=float)
    parser.add_argument("--measured", required=True, type=float)
    parser.add_argument("--baseline-start")
    parser.add_argument("--baseline-end")
    parser.add_argument("--measurement-start")
    parser.add_argument("--measurement-end")
    parser.add_argument("--estimated-value-yen", type=float)
    parser.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    parser.add_argument("--notes")
    parser.add_argument("--payload-json", help='JSON basis, e.g. {"conversions":120}')
    args = parser.parse_args()

    conn = connect(Path(args.db))
    try:
        row = record_outcome(
            conn,
            case_id=args.case_id,
            client_id=args.client_id,
            metric=args.metric,
            baseline_value=args.baseline,
            measured_value=args.measured,
            baseline_start=args.baseline_start,
            baseline_end=args.baseline_end,
            measurement_start=args.measurement_start,
            measurement_end=args.measurement_end,
            estimated_value_yen=args.estimated_value_yen,
            confidence=args.confidence,
            notes=args.notes,
            payload=_load_payload(args.payload_json),
        )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
