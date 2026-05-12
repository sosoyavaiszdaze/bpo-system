"""Rule learning statistics and priority feedback.

This is deliberately deterministic. Claude/LLM can propose new rules or copy
changes later, but production prioritization should first be grounded in
execution and outcome measurements that are reproducible.
"""
from __future__ import annotations

import hashlib
from typing import Any

from engine.stores.db import json_dumps, json_loads


def recompute_rule_learning_stats(conn, rule_id: str | None = None) -> dict[str, Any]:
    """Recompute Track/Learn rollups.

    Inputs:
      - operational_cases: opportunities shown to operators/clients
      - case_executions: whether the action was actually executed
      - outcome_measurements: whether the post-action KPI improved
      - client_responses.not_applicable: lightweight false-positive proxy

    Output:
      - rule_learning_stats.priority_adjustment, negative = promote, positive = demote
    """
    params: list[Any] = []
    where = ""
    if rule_id:
        where = "WHERE c.rule_id = ?"
        params.append(rule_id)

    rows = conn.execute(
        f"""
        SELECT c.rule_id,
               COUNT(DISTINCT c.case_id) AS cases_count,
               COUNT(DISTINCT CASE
                 WHEN e.execution_status IN ('client_reported', 'verified', 'implemented')
                 THEN e.case_id END
               ) AS execution_count,
               COUNT(DISTINCT CASE WHEN o.measured_value IS NOT NULL THEN o.outcome_id END) AS measured_count,
               SUM(CASE WHEN o.change_pct > 0 THEN 1 ELSE 0 END) AS improved_count,
               SUM(CASE WHEN o.change_pct < 0 THEN 1 ELSE 0 END) AS worsened_count,
               SUM(CASE WHEN o.measured_value IS NULL OR o.change_pct IS NULL THEN 1 ELSE 0 END) AS unknown_count,
               AVG(o.change_pct) AS avg_change_pct,
               SUM(COALESCE(o.estimated_value_yen, 0)) AS estimated_value_yen,
               COUNT(DISTINCT CASE WHEN r.status = 'not_applicable' THEN r.response_id END) AS false_positive_count
        FROM operational_cases c
        LEFT JOIN case_executions e ON e.case_id = c.case_id
        LEFT JOIN outcome_measurements o ON o.case_id = c.case_id
        LEFT JOIN client_responses r ON r.case_id = c.case_id
        {where}
        GROUP BY c.rule_id
        """,
        params,
    ).fetchall()

    updated = 0
    for row in rows:
        rid = row["rule_id"]
        if not rid:
            continue
        cases_count = int(row["cases_count"] or 0)
        execution_count = int(row["execution_count"] or 0)
        measured_count = int(row["measured_count"] or 0)
        improved_count = int(row["improved_count"] or 0)
        false_positive_count = int(row["false_positive_count"] or 0)
        execution_rate = round(execution_count / cases_count, 4) if cases_count else None
        win_rate = round(improved_count / measured_count, 4) if measured_count else None
        false_positive_rate = round(false_positive_count / cases_count, 4) if cases_count else None
        priority_adjustment, confidence, recommendation = _learning_recommendation(
            cases_count=cases_count,
            execution_rate=execution_rate,
            measured_count=measured_count,
            win_rate=win_rate,
            avg_change_pct=row["avg_change_pct"],
            false_positive_rate=false_positive_rate,
        )
        payload = {
            "source": "case_executions+outcome_measurements+client_responses",
            "interpretation": "negative priority_adjustment promotes; positive demotes",
        }
        conn.execute(
            """
            INSERT INTO rule_learning_stats (
              rule_id, cases_count, execution_count, execution_rate,
              measured_count, improved_count, worsened_count, unknown_count,
              false_positive_count, false_positive_rate, win_rate, avg_change_pct,
              estimated_value_yen, priority_adjustment, confidence, recommendation,
              payload_json, last_learned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(rule_id) DO UPDATE SET
              cases_count=excluded.cases_count,
              execution_count=excluded.execution_count,
              execution_rate=excluded.execution_rate,
              measured_count=excluded.measured_count,
              improved_count=excluded.improved_count,
              worsened_count=excluded.worsened_count,
              unknown_count=excluded.unknown_count,
              false_positive_count=excluded.false_positive_count,
              false_positive_rate=excluded.false_positive_rate,
              win_rate=excluded.win_rate,
              avg_change_pct=excluded.avg_change_pct,
              estimated_value_yen=excluded.estimated_value_yen,
              priority_adjustment=excluded.priority_adjustment,
              confidence=excluded.confidence,
              recommendation=excluded.recommendation,
              payload_json=excluded.payload_json,
              last_learned_at=excluded.last_learned_at
            """,
            (
                rid,
                cases_count,
                execution_count,
                execution_rate,
                measured_count,
                improved_count,
                row["worsened_count"] or 0,
                row["unknown_count"] or 0,
                false_positive_count,
                false_positive_rate,
                win_rate,
                row["avg_change_pct"],
                row["estimated_value_yen"] or 0,
                priority_adjustment,
                confidence,
                recommendation,
                json_dumps(payload),
            ),
        )
        _record_learning_event(
            conn,
            rid,
            "stats_refreshed",
            {
                "priority_adjustment": priority_adjustment,
                "confidence": confidence,
                "recommendation": recommendation,
            },
        )
        updated += 1
    return {"rules_updated": updated}


def get_learning_adjustments(conn, rule_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return priority feedback used by the Prioritize layer."""
    params: list[Any] = []
    where = ""
    if rule_ids:
        placeholders = ",".join("?" for _ in rule_ids)
        where = f"WHERE rule_id IN ({placeholders})"
        params.extend(rule_ids)
    rows = conn.execute(
        f"""
        SELECT rule_id, priority_adjustment, confidence, recommendation, execution_rate,
               win_rate, avg_change_pct, false_positive_rate, estimated_value_yen,
               cases_count, measured_count
        FROM rule_learning_stats
        {where}
        """,
        params,
    ).fetchall()
    return {row["rule_id"]: dict(row) for row in rows}


def list_rule_learning_stats(conn, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM rule_learning_stats
        ORDER BY confidence DESC, measured_count DESC, priority_adjustment ASC, estimated_value_yen DESC
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


def _learning_recommendation(
    *,
    cases_count: int,
    execution_rate: float | None,
    measured_count: int,
    win_rate: float | None,
    avg_change_pct: float | None,
    false_positive_rate: float | None,
) -> tuple[float, str, str]:
    adjustment = 0.0
    reasons = []

    if measured_count >= 3 and win_rate is not None:
        if win_rate >= 0.7:
            adjustment -= 15
            reasons.append("outcome_win_rate_high")
        elif win_rate <= 0.3:
            adjustment += 25
            reasons.append("outcome_win_rate_low")
    if avg_change_pct is not None and measured_count >= 3:
        if avg_change_pct >= 10:
            adjustment -= 5
            reasons.append("avg_impact_high")
        elif avg_change_pct < 0:
            adjustment += 10
            reasons.append("avg_impact_negative")
    if cases_count >= 3 and execution_rate is not None and execution_rate < 0.2:
        adjustment += 15
        reasons.append("execution_rate_low")
    if cases_count >= 3 and false_positive_rate is not None and false_positive_rate >= 0.3:
        adjustment += 25
        reasons.append("false_positive_rate_high")

    if measured_count >= 5:
        confidence = "high"
    elif measured_count >= 2 or cases_count >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    if adjustment < 0:
        recommendation = "promote"
    elif adjustment > 0:
        recommendation = "review_or_demote"
    else:
        recommendation = "keep_observing"

    return adjustment, confidence, ",".join(reasons) or recommendation


def _record_learning_event(conn, rule_id: str, event_type: str, payload: dict[str, Any]) -> None:
    raw = f"{rule_id}|{event_type}|{json_dumps(payload)}"
    event_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    conn.execute(
        """
        INSERT OR IGNORE INTO rule_learning_events (
          learning_event_id, rule_id, event_type, source, payload_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, rule_id, event_type, "deterministic_rollup", json_dumps(payload)),
    )
