"""Print client health from ADR-018 operational DB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.stores.clients import list_client_ids
from engine.stores.db import connect
from engine.stores.jobs import client_health


def build_health_report(db_path: Path, client_id: str | None = None) -> list[dict]:
    conn = connect(db_path)
    try:
        client_ids = [client_id] if client_id else list_client_ids(conn)
        return [client_health(conn, cid) for cid in client_ids]
    finally:
        conn.close()


def _print_table(rows: list[dict]) -> None:
    headers = ["client", "last_success", "open", "waiting_client", "waiting_zynect", "stale", "latest_status"]
    print("\t".join(headers))
    for r in rows:
        latest = r.get("latest_job") or {}
        print("\t".join([
            str(r.get("client_id", "")),
            str(r.get("last_successful_run_at") or "-"),
            str(r.get("open_cases_count", 0)),
            str(r.get("waiting_client_count", 0)),
            str(r.get("waiting_zynect_count", 0)),
            str(r.get("stale_cases_count", 0)),
            str(latest.get("status") or "-"),
        ]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Show ADR-018 client health")
    parser.add_argument("--db", default=str(ROOT / "state" / "zynect.db"), help="SQLite DB path")
    parser.add_argument("--client", default=None, help="client_id")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    rows = build_health_report(Path(args.db), client_id=args.client)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
