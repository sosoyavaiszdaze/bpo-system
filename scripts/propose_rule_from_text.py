#!/usr/bin/env python3
"""Create/list reviewable rule drafts from natural-language input."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.stores.db import DEFAULT_DB_PATH, transaction
from engine.stores.rule_drafts import create_rule_draft_from_text, list_rule_drafts, review_rule_draft


def main() -> int:
    parser = argparse.ArgumentParser(description="Natural-language rule draft intake")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--text", help="Natural language check item to draft")
    parser.add_argument("--family", help="meta/google/tiktok/seo/legal/general")
    parser.add_argument("--layer", help="Optional target layer")
    parser.add_argument("--created-by")
    parser.add_argument("--list", action="store_true", help="List existing drafts")
    parser.add_argument("--status", help="Filter/list or set review status")
    parser.add_argument("--review-draft-id", help="Draft id to update")
    parser.add_argument("--reviewer")
    args = parser.parse_args()

    with transaction(args.db) as conn:
        if args.review_draft_id:
            if not args.status:
                raise SystemExit("--status is required with --review-draft-id")
            review_rule_draft(conn, draft_id=args.review_draft_id, status=args.status, reviewer_user_id=args.reviewer)
            payload = {"ok": True, "draft_id": args.review_draft_id, "status": args.status}
        elif args.list:
            payload = {"ok": True, "drafts": list_rule_drafts(conn, status=args.status)}
        else:
            if not args.text:
                raise SystemExit("--text is required unless --list or --review-draft-id is used")
            payload = {
                "ok": True,
                "draft": create_rule_draft_from_text(
                    conn,
                    source_text=args.text,
                    target_family=args.family,
                    target_layer=args.layer,
                    created_by=args.created_by,
                ),
            }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
