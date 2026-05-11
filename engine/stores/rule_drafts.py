"""Reviewable rule-change drafts created from natural-language requests."""
from __future__ import annotations

import hashlib
import re
from typing import Any

import yaml

from engine.stores.db import json_dumps, json_loads, utc_now


def draft_id_for(source_text: str, target_family: str | None = None) -> str:
    raw = f"{target_family or ''}|{source_text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def create_rule_draft_from_text(
    conn,
    *,
    source_text: str,
    target_family: str | None = None,
    target_layer: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a conservative YAML skeleton for human review.

    This intentionally does not activate the rule. Claude or another prompt
    layer can later replace the heuristic skeleton, but the DB review workflow
    stays the same.
    """
    text = source_text.strip()
    if not text:
        raise ValueError("source_text is required")
    family = target_family or _infer_family(text)
    layer = target_layer or _infer_layer(family)
    rule_id = _proposed_rule_id(family, text)
    proposed = {
        "id": rule_id,
        "name": _title_from_text(text),
        "severity": "medium",
        "enabled": False,
        "lifecycle": "draft",
        "root_cause_group": _root_cause_group(family, text),
        "decision_axis": "review_required",
        "applies_to": _applies_to(family),
        "trigger": {
            "type": "manual_review_required",
            "source_text": text,
        },
        "expected_impact": {
            "primary_metric": "review_required",
            "source_basis": "operator_natural_language_draft",
        },
        "customer_message": {
            "status": "draft",
            "needs_review": True,
        },
    }
    proposed_yaml = yaml.safe_dump({"rules": [proposed]}, allow_unicode=True, sort_keys=False)
    draft_id = draft_id_for(text, family)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO rule_change_drafts (
          draft_id, source_text, proposed_rule_id, proposed_yaml,
          target_family, target_layer, status, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'review_required', ?, ?, ?)
        ON CONFLICT(draft_id) DO UPDATE SET
          source_text=excluded.source_text,
          proposed_rule_id=excluded.proposed_rule_id,
          proposed_yaml=excluded.proposed_yaml,
          target_family=excluded.target_family,
          target_layer=excluded.target_layer,
          status='review_required',
          payload_json=excluded.payload_json,
          updated_at=excluded.updated_at
        """,
        (
            draft_id,
            text,
            rule_id,
            proposed_yaml,
            family,
            layer,
            json_dumps({"created_by": created_by, "heuristic_version": 1}),
            now,
            now,
        ),
    )
    return get_rule_draft(conn, draft_id) or {"draft_id": draft_id}


def get_rule_draft(conn, draft_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM rule_change_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["payload"] = json_loads(data.pop("payload_json"), {})
    return data


def list_rule_drafts(conn, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM rule_change_drafts
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json"), {})
        out.append(data)
    return out


def review_rule_draft(
    conn,
    *,
    draft_id: str,
    status: str,
    reviewer_user_id: str | None = None,
) -> None:
    if status not in {"approved", "rejected", "needs_revision", "review_required"}:
        raise ValueError(f"invalid draft status: {status}")
    conn.execute(
        """
        UPDATE rule_change_drafts
        SET status = ?, reviewer_user_id = ?, reviewed_at = ?, updated_at = ?
        WHERE draft_id = ?
        """,
        (status, reviewer_user_id, utc_now(), utc_now(), draft_id),
    )


def _infer_family(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("meta", "facebook", "instagram", "pixel", "capi")):
        return "meta"
    if "google" in low:
        return "google"
    if "tiktok" in low:
        return "tiktok"
    if "seo" in low:
        return "seo"
    if any(x in low for x in ("法律", "景表", "特商", "privacy", "個人情報")):
        return "legal"
    return "general"


def _infer_layer(family: str) -> str:
    return "layer_a" if family in {"meta", "google", "tiktok", "seo"} else "foundation"


def _proposed_rule_id(family: str, text: str) -> str:
    prefix = {
        "meta": "M-DRAFT",
        "google": "G-DRAFT",
        "tiktok": "T-DRAFT",
        "seo": "S-DRAFT",
        "legal": "F-LC-DRAFT",
    }.get(family, "R-DRAFT")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:6].upper()
    return f"{prefix}-{digest}"


def _title_from_text(text: str) -> str:
    title = re.sub(r"\s+", " ", text).strip()
    return title[:80]


def _root_cause_group(family: str, text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("cv", "conversion", "pixel", "capi", "計測")):
        return "measurement_foundation"
    if any(x in low for x in ("cpa", "roas", "ltv", "課金")):
        return "performance_diagnosis"
    if family == "legal":
        return "legal_review"
    return family


def _applies_to(family: str) -> dict[str, list[str]]:
    if family in {"meta", "google", "tiktok"}:
        return {"ad_platforms": [family]}
    return {}
