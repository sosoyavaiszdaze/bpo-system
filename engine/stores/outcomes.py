"""Outcome Tracker store and KPI calculation helpers.

This is intentionally SQLite-first. The public functions use plain dicts and
SQL that can later move to PostgreSQL with minimal shape changes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
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


def update_due_outcome_measurements(
    conn,
    *,
    client_id: str,
    current_kpis: dict[str, float | int | None],
    today: str,
    windows: tuple[int, ...] = (7, 14, 28),
) -> int:
    """Fill measured_value for baseline rows whose measurement window is due."""
    today_date = date.fromisoformat(today)
    rows = conn.execute(
        """
        SELECT outcome_id, case_id, metric, baseline_start, baseline_value, payload_json
        FROM outcome_measurements
        WHERE client_id = ?
          AND measured_value IS NULL
          AND baseline_value IS NOT NULL
          AND baseline_start IS NOT NULL
        """,
        (client_id,),
    ).fetchall()
    updated = 0
    for row in rows:
        metric = row["metric"]
        measured = current_kpis.get(metric)
        if measured is None:
            continue
        baseline_start = date.fromisoformat(row["baseline_start"])
        age_days = (today_date - baseline_start).days
        due_window = next((w for w in windows if age_days >= w), None)
        if due_window is None:
            continue
        payload = json_loads(row["payload_json"], {})
        payload["measurement_window_days"] = due_window
        payload["measurement_source"] = "daily_chatwork_check"
        change_pct = improvement_pct(metric, row["baseline_value"], float(measured))
        value_yen = estimate_value_yen(metric, row["baseline_value"], float(measured), payload)
        conn.execute(
            """
            UPDATE outcome_measurements
            SET measured_value = ?,
                measurement_end = ?,
                change_pct = ?,
                estimated_value_yen = COALESCE(?, estimated_value_yen),
                notes = ?,
                payload_json = ?
            WHERE outcome_id = ?
            """,
            (
                float(measured),
                today,
                change_pct,
                value_yen,
                f"{due_window}d measured outcome",
                json_dumps(payload),
                row["outcome_id"],
            ),
        )
        updated += 1
    return updated


def refresh_rule_outcome_rollups(conn) -> dict[str, Any]:
    """Aggregate measured outcomes into per-rule win rates."""
    rows = conn.execute(
        """
        SELECT c.rule_id,
               COUNT(DISTINCT o.case_id) AS cases_count,
               SUM(CASE WHEN o.measured_value IS NOT NULL THEN 1 ELSE 0 END) AS measured_count,
               SUM(CASE WHEN o.change_pct > 0 THEN 1 ELSE 0 END) AS improved_count,
               SUM(CASE WHEN o.change_pct < 0 THEN 1 ELSE 0 END) AS worsened_count,
               SUM(CASE WHEN o.measured_value IS NULL OR o.change_pct IS NULL THEN 1 ELSE 0 END) AS unknown_count,
               AVG(o.change_pct) AS avg_change_pct,
               SUM(COALESCE(o.estimated_value_yen, 0)) AS estimated_value_yen,
               MAX(COALESCE(o.measurement_end, o.created_at)) AS last_measured_at
        FROM outcome_measurements o
        LEFT JOIN operational_cases c ON c.case_id = o.case_id
        WHERE c.rule_id IS NOT NULL
        GROUP BY c.rule_id
        """
    ).fetchall()
    updated = 0
    for row in rows:
        measured_count = int(row["measured_count"] or 0)
        improved_count = int(row["improved_count"] or 0)
        win_rate = round(improved_count / measured_count, 4) if measured_count else None
        conn.execute(
            """
            INSERT INTO rule_outcome_rollups (
              rule_id, cases_count, measured_count, improved_count, worsened_count,
              unknown_count, avg_change_pct, estimated_value_yen, win_rate,
              last_measured_at, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(rule_id) DO UPDATE SET
              cases_count=excluded.cases_count,
              measured_count=excluded.measured_count,
              improved_count=excluded.improved_count,
              worsened_count=excluded.worsened_count,
              unknown_count=excluded.unknown_count,
              avg_change_pct=excluded.avg_change_pct,
              estimated_value_yen=excluded.estimated_value_yen,
              win_rate=excluded.win_rate,
              last_measured_at=excluded.last_measured_at,
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
            """,
            (
                row["rule_id"],
                row["cases_count"] or 0,
                measured_count,
                improved_count,
                row["worsened_count"] or 0,
                row["unknown_count"] or 0,
                row["avg_change_pct"],
                row["estimated_value_yen"] or 0,
                win_rate,
                row["last_measured_at"],
                json_dumps({"source": "outcome_measurements"}),
            ),
        )
        updated += 1
    return {"rules_updated": updated}


def list_rule_outcome_rollups(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM rule_outcome_rollups
        ORDER BY measured_count DESC, win_rate DESC, estimated_value_yen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json"), {})
        out.append(data)
    return out


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
