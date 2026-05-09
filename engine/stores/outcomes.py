"""Outcome Tracker store and KPI calculation helpers.

This is intentionally SQLite-first. The public functions use plain dicts and
SQL that can later move to PostgreSQL with minimal shape changes.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from engine.stores.db import json_dumps, json_loads, row_to_dict

LOWER_IS_BETTER = {
    "cpa",
    "cpa_change_pct",
    "cpc",
    "cpc_change_pct",
    "frequency",
    "frequency_reduction_pct",
    "ops_hours",
    "ops_hours_saved",
}

HIGHER_IS_BETTER = {
    "roas",
    "roas_change_pct",
    "cv",
    "cv_count",
    "cv_count_change_pct",
    "cvr",
    "cvr_change_pct",
    "ctr",
    "ctr_change_pct",
    "impressions",
    "impression_share_recovery_pct",
    "match_rate_improvement_pct",
    "learning_signal_quality_pct",
}


def outcome_id_for(
    case_id: str,
    metric: str,
    measurement_start: str | None = None,
    measurement_end: str | None = None,
) -> str:
    """Build a stable id for one case/metric/window measurement."""
    raw = f"{case_id}|{metric}|{measurement_start or ''}|{measurement_end or ''}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outcome:{digest}"


def improvement_pct(metric: str, baseline_value: float | None, measured_value: float | None) -> float | None:
    """Return positive percentage when the measured value improved.

    CPA/CPC/frequency/ops_hours are lower-is-better; ROAS/CV/CTR/CVR are
    higher-is-better. Unknown metrics default to normal percentage change.
    """
    if baseline_value is None or measured_value is None:
        return None
    baseline = float(baseline_value)
    measured = float(measured_value)
    if baseline == 0:
        return None
    metric_key = metric.lower()
    if metric_key in LOWER_IS_BETTER:
        return ((baseline - measured) / baseline) * 100.0
    return ((measured - baseline) / baseline) * 100.0


def estimate_value_yen(
    metric: str,
    baseline_value: float | None,
    measured_value: float | None,
    payload: dict[str, Any] | None = None,
) -> float | None:
    """Estimate economic value from measured improvement when possible.

    Supported lightweight formulas:
    - CPA: (baseline_cpa - measured_cpa) * conversions
    - ROAS: (measured_roas - baseline_roas) * spend
    - ops_hours_saved: saved_hours * hourly_rate_yen

    Returns None when the required basis is missing; callers may pass an
    explicit estimated_value_yen to avoid guessing.
    """
    if baseline_value is None or measured_value is None:
        return None
    payload = payload or {}
    metric_key = metric.lower()
    baseline = float(baseline_value)
    measured = float(measured_value)

    if metric_key in {"cpa", "cpa_change_pct"}:
        conversions = _number(payload.get("conversions") or payload.get("cv_count"))
        if conversions is None:
            return None
        return float(round(max(0.0, (baseline - measured) * conversions)))

    if metric_key in {"roas", "roas_change_pct"}:
        spend = _number(payload.get("spend_yen") or payload.get("monthly_spend_yen"))
        if spend is None:
            return None
        return float(round(max(0.0, (measured - baseline) * spend)))

    if metric_key == "ops_hours_saved":
        hourly_rate = _number(payload.get("hourly_rate_yen")) or 5000.0
        return float(round(max(0.0, baseline - measured) * hourly_rate))

    return None


def record_outcome(
    conn,
    *,
    case_id: str,
    client_id: str,
    metric: str,
    baseline_value: float | None,
    measured_value: float | None,
    baseline_start: str | None = None,
    baseline_end: str | None = None,
    measurement_start: str | None = None,
    measurement_end: str | None = None,
    change_pct: float | None = None,
    estimated_value_yen: float | None = None,
    confidence: str = "medium",
    notes: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert one outcome measurement and return the stored row."""
    payload = payload or {}
    if change_pct is None:
        change_pct = improvement_pct(metric, baseline_value, measured_value)
    if estimated_value_yen is None:
        estimated_value_yen = estimate_value_yen(metric, baseline_value, measured_value, payload)

    outcome_id = outcome_id_for(case_id, metric, measurement_start, measurement_end)
    conn.execute(
        """
        INSERT INTO outcome_measurements (
          outcome_id, case_id, client_id, metric,
          baseline_start, baseline_end, measurement_start, measurement_end,
          baseline_value, measured_value, change_pct, estimated_value_yen,
          confidence, notes, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(outcome_id) DO UPDATE SET
          baseline_start=excluded.baseline_start,
          baseline_end=excluded.baseline_end,
          measurement_start=excluded.measurement_start,
          measurement_end=excluded.measurement_end,
          baseline_value=excluded.baseline_value,
          measured_value=excluded.measured_value,
          change_pct=excluded.change_pct,
          estimated_value_yen=excluded.estimated_value_yen,
          confidence=excluded.confidence,
          notes=excluded.notes,
          payload_json=excluded.payload_json
        """,
        (
            outcome_id,
            case_id,
            client_id,
            metric,
            baseline_start,
            baseline_end,
            measurement_start,
            measurement_end,
            baseline_value,
            measured_value,
            change_pct,
            estimated_value_yen,
            confidence,
            notes,
            json_dumps(payload),
        ),
    )
    return get_outcome(conn, outcome_id) or {"outcome_id": outcome_id}


def record_completion_outcome(conn, record: dict[str, Any]) -> dict[str, Any] | None:
    """Record a resolved indication's achieved effect when available.

    Existing completion payloads may carry either numeric keys
    (`realistic_yen`) or display strings (`realistic: "¥-115,500 / 月"`).
    When no numeric value can be parsed, no outcome row is written.
    """
    payload = record.get("payload") or {}
    effect = payload.get("achieved_effect") or {}
    value = (
        _number(effect.get("realistic_yen"))
        or _number(effect.get("minimum_yen"))
        or _yen_from_text(effect.get("realistic"))
        or _yen_from_text(effect.get("minimum"))
    )
    if value is None:
        return None
    metric = payload.get("outcome_metric") or "estimated_monthly_value_yen"
    return record_outcome(
        conn,
        case_id=record.get("indication_id") or record.get("case_id") or "",
        client_id=record.get("client_id") or "",
        metric=metric,
        baseline_value=None,
        measured_value=None,
        measurement_end=record.get("resolved_date"),
        change_pct=None,
        estimated_value_yen=abs(float(value)),
        confidence=payload.get("outcome_confidence") or "medium",
        notes="completion_notice achieved_effect",
        payload={
            "rule_id": record.get("rule_id"),
            "source": "completion_notice",
            "achieved_effect": effect,
        },
    )


def get_outcome(conn, outcome_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM outcome_measurements WHERE outcome_id = ?",
        (outcome_id,),
    ).fetchone()
    data = row_to_dict(row)
    if data:
        data["payload"] = json_loads(data.pop("payload_json"), {})
    return data


def list_outcomes(conn, client_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if client_id:
        where = "WHERE client_id = ?"
        params.append(client_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT outcome_id, case_id, client_id, metric, baseline_value, measured_value,
               change_pct, estimated_value_yen, confidence, measurement_end, notes, created_at
        FROM outcome_measurements
        {where}
        ORDER BY COALESCE(measurement_end, created_at) DESC, created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def outcome_summary(conn, client_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if client_id:
        where = "WHERE client_id = ?"
        params.append(client_id)
    rows = conn.execute(
        f"""
        SELECT metric, COUNT(*) AS n, AVG(change_pct) AS avg_change_pct,
               SUM(COALESCE(estimated_value_yen, 0)) AS estimated_value_yen
        FROM outcome_measurements
        {where}
        GROUP BY metric
        ORDER BY metric
        """,
        params,
    ).fetchall()
    metrics = [dict(row) for row in rows]
    return {
        "metrics": metrics,
        "total_estimated_value_yen": sum(float(row["estimated_value_yen"] or 0) for row in metrics),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yen_from_text(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"-?[\d,]+", value)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None
