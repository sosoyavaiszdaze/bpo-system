from pathlib import Path

import yaml

from engine.operations_ui.queries import build_console_context
from engine.rules.decision_trace import build_selection_traces
from engine.rules.registry import load_rule_registry, summarize_rule_registry
from engine.stores.clients import upsert_client
from engine.stores.cases import upsert_case_from_indication
from engine.stores.db import connect
from engine.stores.decision_traces import list_traces, record_trace, trace_summary
from engine.stores.executions import list_case_executions, record_case_execution
from engine.stores.learning import (
    create_rule_improvement_drafts,
    get_learning_adjustments,
    list_rule_learning_stats,
    recompute_rule_learning_stats,
)
from engine.stores.jobs import record_job
from engine.stores.outcomes import (
    improvement_pct,
    list_outcomes,
    list_rule_outcome_rollups,
    outcome_summary,
    record_completion_outcome,
    record_outcome,
    refresh_rule_outcome_rollups,
    update_due_outcome_measurements,
)
from engine.stores.rule_drafts import create_rule_draft_from_text, list_rule_drafts, review_rule_draft
from engine.stores.rules import family_operations_matrix, meta_rule_operations_summary, registry_summary, sync_rule_registry
from scripts.operations_ui import create_app
from scripts.update_outcome_measurements import update_outcomes


def test_rule_registry_summarizes_axis_and_mapping_coverage(tmp_path):
    root = _rule_root(tmp_path)

    records = load_rule_registry(root)
    summary = summarize_rule_registry(records)

    assert summary["total_rules"] == 2
    assert summary["messaging_mapped"] == 1
    assert summary["expected_impact_rules"] == 1
    assert summary["root_cause_group_rules"] == 1
    assert summary["enabled_rules"] == 2
    assert summary["customer_visible_rules"] == 1
    assert summary["high_critical_unmapped_rules"] == 0
    assert summary["family_counts"]["foundation"]["total"] == 2
    assert summary["family_counts"]["foundation"]["customer_visible"] == 1
    missing = next(r for r in records if r.rule_id == "F-MF-02")
    assert "missing_root_cause_group" in missing.issues
    assert "messaging_unmapped" in missing.issues
    connected = next(r for r in records if r.rule_id == "F-MF-01")
    assert "incomplete_customer_message_schema" in connected.issues
    assert "unsafe_eval_trigger" in connected.issues


def test_rule_registry_accepts_non_financial_impact_for_review_rules(tmp_path):
    root = _rule_root(tmp_path)
    (root / "config" / "rule_messaging.yaml").write_text(
        yaml.safe_dump(
            {
                "rules": {
                    "F-MF-02": {
                        "customer_title": "審査リスク確認",
                        "priority": "B",
                        "goal_stage": "legal_review",
                        "today_action": "確認してください",
                        "yes_no_question": "確認できましたか?",
                        "action_options": {"A": "はい", "B": "いいえ"},
                        "answer_source_preference": ["validator", "chatwork_reply"],
                        "non_financial_impact": {
                            "primary_metric": "review_risk_reduction",
                            "source_basis": "審査落ちと配信停止リスクの予防。",
                            "measurement_window": {"signal": "1週", "verdict": "4週"},
                        },
                    }
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    records = load_rule_registry(root)
    record = next(r for r in records if r.rule_id == "F-MF-02")

    assert record.has_expected_impact
    assert record.expected_impact["primary_metric"] == "review_risk_reduction"
    assert "missing_expected_impact" not in record.issues
    assert "missing_impact_estimate" not in record.issues


def test_operations_console_context_is_read_only_shape(tmp_path):
    root = _rule_root(tmp_path)
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    upsert_client(
        conn,
        "pilotton",
        {
            "display_name": "株式会社パイロットン",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
            "chatwork_rooms": {"main": "12345"},
        },
    )
    upsert_case_from_indication(
        conn,
        {
            "indication_id": "case-1",
            "client_id": "pilotton",
            "rule_id": "F-MF-01",
            "severity": "high",
            "status": "open",
            "first_detected_at": "2026-05-09T00:00:00+00:00",
            "payload": {"title": "CVイベント確認"},
        },
    )
    record_job(conn, "daily_chatwork_check", "pilotton", "success", metrics={"sent": 1})
    record_outcome(
        conn,
        case_id="case-1",
        client_id="pilotton",
        metric="cpa_change_pct",
        baseline_value=10000,
        measured_value=8000,
        estimated_value_yen=200000,
        confidence="medium",
    )
    conn.commit()
    conn.close()

    ctx = build_console_context(db_path=db_path, root=root)

    assert ctx["clients"][0]["client_id"] == "pilotton"
    assert ctx["clients"][0]["open_cases_count"] == 1
    assert ctx["case_inbox"][0]["rule_id"] == "F-MF-01"
    assert ctx["recent_jobs"][0]["status"] == "success"
    assert ctx["response_summary"]["total"] == 0
    assert ctx["recent_responses"] == []
    assert ctx["outcomes"]["metrics"][0]["metric"] == "cpa_change_pct"
    assert ctx["outcomes"]["metrics"][0]["avg_change_pct"] == 20
    assert ctx["recent_outcomes"][0]["case_id"] == "case-1"
    assert "rule_learning_stats" in ctx
    assert ctx["rule_registry"]["total_rules"] == 2
    assert ctx["daily_ops"]["open_cases"] == 1
    assert ctx["daily_ops"]["rule_total"] == 2
    assert ctx["case_status_board"][0]["status"] == "open"
    assert ctx["case_status_board"][0]["count"] == 1
    assert ctx["connection_matrix"]["missing_required"] >= 0
    assert ctx["outcome_workbench"]["measured"] == 1
    assert ctx["outcome_workbench"]["improved"] == 1
    assert "rule_quality_board" in ctx
    assert "connections" in ctx
    assert "incidents" in ctx
    assert "rule_family_operations" in ctx
    assert "rule_change_drafts" in ctx


def test_operations_console_dashboard_renders_daily_ops_sections(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    upsert_client(
        conn,
        "pilotton",
        {
            "display_name": "株式会社パイロットン",
            "vertical": "ec_d2c",
            "ec_platform": "ecforce",
        },
    )
    conn.commit()
    conn.close()

    from fastapi.testclient import TestClient

    response = TestClient(create_app(db_path)).get("/")

    assert response.status_code == 200
    assert "Daily Operations" in response.text
    assert "Connection Readiness" in response.text
    assert "Case State Board" in response.text
    assert "Outcome Tracker" in response.text
    assert "Rule Quality Board" in response.text
    assert "Incidents" in response.text


def test_sync_rule_registry_persists_yaml_connectivity_audit(tmp_path):
    root = _rule_root(tmp_path)
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    records = load_rule_registry(root)

    result = sync_rule_registry(conn, records)
    conn.commit()
    summary = registry_summary(conn)
    issue = conn.execute(
        "SELECT issue_type FROM rule_registry_issues WHERE rule_id = ? ORDER BY issue_type",
        ("F-MF-02",),
    ).fetchall()

    assert result["rules_synced"] == 2
    assert summary["messaging_mapped"] == 1
    assert summary["expected_impact_rules"] == 1
    ops = conn.execute("SELECT lifecycle FROM rule_registry_operations WHERE rule_id = ?", ("F-MF-01",)).fetchone()
    assert ops["lifecycle"] == "active"
    assert [row["issue_type"] for row in issue] == [
        "messaging_unmapped",
        "missing_applies_to",
        "missing_expected_impact",
        "missing_lifecycle",
        "missing_root_cause_group",
        "unsafe_eval_trigger",
        "weak_or_missing_decision_axis",
    ]


def test_rule_registry_flags_high_severity_customer_visibility_gap(tmp_path):
    root = _rule_root(tmp_path)
    path = root / "config" / "rules" / "meta_rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "M01",
                        "name": "High priority Meta gap",
                        "severity": "critical",
                        "enabled": True,
                    },
                    {
                        "id": "M02",
                        "name": "Disabled Meta gap",
                        "severity": "critical",
                        "enabled": False,
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    records = load_rule_registry(root)
    summary = summarize_rule_registry(records)
    critical = next(r for r in records if r.rule_id == "M01")
    disabled = next(r for r in records if r.rule_id == "M02")

    assert "high_severity_unmapped" in critical.issues
    assert "high_severity_unmapped" not in disabled.issues
    assert summary["high_critical_unmapped_rules"] == 1


def test_rule_registry_does_not_count_draft_as_high_severity_unmapped(tmp_path):
    root = _rule_root(tmp_path)
    path = root / "config" / "rules" / "meta_rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "M90",
                        "name": "Phase Bで詳細記述する予定",
                        "severity": "critical",
                        "enabled": True,
                        "lifecycle": "draft",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    records = load_rule_registry(root)
    summary = summarize_rule_registry(records)
    record = next(r for r in records if r.rule_id == "M90")

    assert record.lifecycle == "draft"
    assert "draft_or_placeholder" in record.issues
    assert "high_severity_unmapped" not in record.issues
    assert summary["high_critical_unmapped_rules"] == 0


def test_meta_rule_operations_summary_counts_meta_readiness(tmp_path):
    root = _rule_root(tmp_path)
    (root / "config" / "rules" / "meta_rules.yaml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "M02",
                        "name": "CAPI接続",
                        "severity": "high",
                        "enabled": True,
                        "root_cause_group": "measurement_foundation",
                        "axis_position": "TO-02",
                        "data_source": [{"source": "meta_api", "fields": ["pixel_status"]}],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    conn = connect(tmp_path / "zynect.db")
    records = load_rule_registry(root)
    sync_rule_registry(conn, records)
    conn.commit()

    summary = meta_rule_operations_summary(conn)

    assert summary["meta_total"] >= 1
    assert summary["meta_high_critical"] >= 1


def test_claude_ads_meta_source_is_mapped_to_customer_visible_meta_rules():
    root = Path(__file__).resolve().parent.parent
    source_path = root / "config" / "external_rule_sources" / "claude_ads_meta.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    records = {record.rule_id: record for record in load_rule_registry(root)}

    assert source["runtime_dependency"] is False
    assert source["license"] == "MIT"
    assert set(source["customer_visible_promotions"]["promoted_rules"]) == {"M24", "M25", "M28", "M57", "M58", "M39"}

    for rule_id in ["M24", "M25", "M28", "M57", "M58", "M39"]:
        record = records[rule_id]
        assert record.messaging_mapped
        assert record.customer_visible
        assert record.has_expected_impact
        assert "source_refs" in record.messaging_payload
        assert any(str(ref).startswith("claude_ads_meta:") for ref in record.messaging_payload["source_refs"])


def test_rule_family_operations_matrix_includes_non_meta(tmp_path):
    root = _rule_root(tmp_path)
    (root / "config" / "rules" / "google_rules.yaml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "G01",
                        "name": "Google CV確認",
                        "severity": "high",
                        "enabled": True,
                        "root_cause_group": "measurement_foundation",
                        "axis_position": "TO-02",
                        "data_source": [{"source": "google_ads_api", "fields": ["conversions"]}],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    conn = connect(tmp_path / "zynect.db")
    sync_rule_registry(conn, load_rule_registry(root))
    conn.commit()

    matrix = {row["family"]: row for row in family_operations_matrix(conn)}

    assert matrix["google"]["total"] == 1
    assert matrix["google"]["high_critical"] == 1
    assert matrix["google"]["required_data_sources"] == 1


def test_rule_draft_from_natural_language_is_reviewable(tmp_path):
    conn = connect(tmp_path / "zynect.db")

    draft = create_rule_draft_from_text(
        conn,
        source_text="Metaで本人確認CVのCPAが悪化したら、登録CPAではなく本人確認CPAで判断したい",
        target_family="meta",
        created_by="ops",
    )
    review_rule_draft(conn, draft_id=draft["draft_id"], status="needs_revision", reviewer_user_id="reviewer-1")
    conn.commit()

    rows = list_rule_drafts(conn)
    reviewed = conn.execute("SELECT status FROM rule_change_drafts WHERE draft_id = ?", (draft["draft_id"],)).fetchone()

    assert rows[0]["proposed_rule_id"].startswith("M-DRAFT-")
    assert "本人確認CV" in rows[0]["proposed_yaml"]
    assert reviewed["status"] == "needs_revision"


def test_decision_trace_store_and_ui_context(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    record_trace(
        conn,
        client_id="pilotton",
        rule_id="F-MF-01",
        evaluation_date="2026-05-09",
        stage="environment_match",
        status="passed",
        evidence={"vertical": "ec_d2c"},
    )
    record_trace(
        conn,
        client_id="pilotton",
        rule_id="F-MF-02",
        evaluation_date="2026-05-09",
        stage="eligibility",
        status="skipped",
        reason="trigger_or_prerequisite_or_cooldown",
    )
    conn.commit()

    summary = trace_summary(conn, "pilotton")
    traces = list_traces(conn, "pilotton")
    ctx = build_console_context(db_path=db_path, root=_rule_root(tmp_path))

    assert summary["environment_match"]["passed"] == 1
    assert summary["eligibility"]["skipped"] == 1
    assert traces[0]["client_id"] == "pilotton"
    assert ctx["decision_trace_summary"]["environment_match"]["passed"] == 1
    assert len(ctx["recent_decision_traces"]) == 2


def test_outcome_tracker_computes_directional_improvement_and_value(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)

    cpa = record_outcome(
        conn,
        case_id="case-cpa",
        client_id="pilotton",
        metric="cpa_change_pct",
        baseline_value=10000,
        measured_value=8000,
        payload={"conversions": 50},
        measurement_end="2026-05-31",
    )
    roas = record_outcome(
        conn,
        case_id="case-roas",
        client_id="pilotton",
        metric="roas_change_pct",
        baseline_value=2.0,
        measured_value=2.4,
        payload={"monthly_spend_yen": 500000},
        measurement_end="2026-05-31",
    )
    ops = record_outcome(
        conn,
        case_id="case-ops",
        client_id="pilotton",
        metric="ops_hours_saved",
        baseline_value=6,
        measured_value=2,
        payload={"hourly_rate_yen": 6000},
        measurement_end="2026-05-31",
    )
    conn.commit()

    summary = outcome_summary(conn, "pilotton")
    rows = list_outcomes(conn, "pilotton")

    assert cpa["change_pct"] == 20
    assert cpa["estimated_value_yen"] == 100000
    assert round(roas["change_pct"], 1) == 20.0
    assert roas["estimated_value_yen"] == 200000
    assert round(ops["change_pct"], 1) == 66.7
    assert ops["estimated_value_yen"] == 24000
    assert summary["total_estimated_value_yen"] == 324000
    assert len(rows) == 3


def test_outcome_tracker_updates_due_baseline_measurements(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    baseline = record_outcome(
        conn,
        case_id="case-cpa",
        client_id="pilotton",
        metric="cpa",
        baseline_value=10000,
        measured_value=None,
        baseline_start="2026-05-01",
        measurement_start="2026-05-01",
        payload={"conversions": 40},
    )
    conn.commit()

    updated = update_due_outcome_measurements(
        conn,
        client_id="pilotton",
        current_kpis={"cpa": 8000, "cv_count": 45},
        today="2026-05-08",
    )
    conn.commit()
    row = conn.execute(
        "SELECT measured_value, measurement_end, change_pct, estimated_value_yen, payload_json "
        "FROM outcome_measurements WHERE case_id = ? AND measured_value IS NOT NULL",
        ("case-cpa",),
    ).fetchone()

    assert updated == 1
    assert row["measured_value"] == 8000
    assert row["measurement_end"] == "2026-05-08"
    assert row["change_pct"] == 20
    assert row["estimated_value_yen"] == 80000
    assert '"measurement_window_days": 7' in row["payload_json"]


def test_rule_outcome_rollups_accumulate_win_rate(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    upsert_case_from_indication(conn, {
        "indication_id": "case-1",
        "client_id": "pilotton",
        "rule_id": "M02",
        "status": "open",
        "severity": "high",
        "first_detected_at": "2026-05-01T00:00:00+00:00",
        "payload": {"title": "CAPI"},
    })
    record_outcome(
        conn,
        case_id="case-1",
        client_id="pilotton",
        metric="cpa",
        baseline_value=10000,
        measured_value=8000,
        measurement_end="2026-05-08",
        payload={"conversions": 10},
    )
    result = refresh_rule_outcome_rollups(conn)
    conn.commit()
    rows = list_rule_outcome_rollups(conn)

    assert result["rules_updated"] == 1
    assert rows[0]["rule_id"] == "M02"
    assert rows[0]["win_rate"] == 1.0


def test_client_done_response_records_execution_and_learning_stats(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    upsert_case_from_indication(conn, {
        "indication_id": "case-1",
        "client_id": "pilotton",
        "rule_id": "M02",
        "status": "open",
        "severity": "high",
        "first_detected_at": "2026-05-01T00:00:00+00:00",
        "payload": {"title": "CAPI"},
    })

    from engine.stores.responses import upsert_client_response
    upsert_client_response(
        conn,
        client_id="pilotton",
        case_id="case-1",
        record={
            "rule_id": "M02",
            "answer_code": "A",
            "answer_label": "対応済み",
            "status": "confirmed_done",
            "raw_message": "M02 A",
            "chatwork_message_id": "msg-1",
            "answered_at": "2026-05-02T00:00:00+00:00",
            "source": "chatwork_reply",
        },
    )
    recompute_rule_learning_stats(conn)
    conn.commit()

    executions = list_case_executions(conn, "case-1")
    learning = list_rule_learning_stats(conn)

    assert executions[0]["execution_status"] == "client_reported"
    assert executions[0]["rule_id"] == "M02"
    assert learning[0]["rule_id"] == "M02"
    assert learning[0]["execution_count"] == 1
    assert learning[0]["execution_rate"] == 1.0


def test_learning_stats_promote_rules_with_measured_wins(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    for i in range(3):
        case_id = f"case-{i}"
        upsert_case_from_indication(conn, {
            "indication_id": case_id,
            "client_id": "pilotton",
            "rule_id": "M02",
            "status": "open",
            "severity": "high",
            "first_detected_at": "2026-05-01T00:00:00+00:00",
            "payload": {"title": "CAPI"},
        })
        record_case_execution(
            conn,
            case_id=case_id,
            client_id="pilotton",
            rule_id="M02",
            execution_status="verified",
            evidence_source="meta_api",
            evidence_quality="high",
            verified_at="2026-05-02T00:00:00+00:00",
        )
        record_outcome(
            conn,
            case_id=case_id,
            client_id="pilotton",
            metric="cpa",
            baseline_value=10000,
            measured_value=8000,
            measurement_end="2026-05-08",
            payload={"conversions": 10},
        )

    rollup = refresh_rule_outcome_rollups(conn)
    stats = get_learning_adjustments(conn, ["M02"])["M02"]

    assert rollup["learning_rules_updated"] == 1
    assert stats["win_rate"] == 1.0
    assert stats["execution_rate"] == 1.0
    assert stats["priority_adjustment"] < 0
    assert stats["recommendation"]


def test_learning_stats_create_review_draft_for_bad_rule(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    for i in range(3):
        case_id = f"case-bad-{i}"
        upsert_case_from_indication(conn, {
            "indication_id": case_id,
            "client_id": "pilotton",
            "rule_id": "M99",
            "status": "open",
            "severity": "high",
            "first_detected_at": "2026-05-01T00:00:00+00:00",
            "payload": {"title": "Bad rule"},
        })
        record_outcome(
            conn,
            case_id=case_id,
            client_id="pilotton",
            metric="cpa",
            baseline_value=10000,
            measured_value=13000,
            measurement_end="2026-05-08",
            payload={"conversions": 10},
        )
    recompute_rule_learning_stats(conn)

    result = create_rule_improvement_drafts(conn, min_cases=3, min_confidence="medium")
    drafts = list_rule_drafts(conn, status="review_required")

    assert result["drafts_created"] == 1
    assert drafts[0]["target_family"] == "meta"
    assert "Rule M99 needs production review" in drafts[0]["source_text"]


def test_update_outcome_measurements_job_updates_due_rows(tmp_path):
    db_path = tmp_path / "zynect.db"
    conn = connect(db_path)
    upsert_client(conn, "pilotton", {"display_name": "株式会社パイロットン", "vertical": "ec_d2c"})
    upsert_case_from_indication(conn, {
        "indication_id": "case-1",
        "client_id": "pilotton",
        "rule_id": "M02",
        "status": "open",
        "severity": "high",
        "first_detected_at": "2026-05-01T00:00:00+00:00",
        "payload": {"title": "CAPI"},
    })
    record_outcome(
        conn,
        case_id="case-1",
        client_id="pilotton",
        metric="cpa",
        baseline_value=10000,
        measured_value=None,
        baseline_start="2026-05-01",
        measurement_start="2026-05-01",
        payload={"conversions": 10},
    )
    conn.commit()
    conn.close()

    result = update_outcomes(
        db_path=db_path,
        client_id="pilotton",
        today="2026-05-08",
        kpi_json='{"pilotton":{"cpa":8000,"cv_count":12,"roas":2.1}}',
    )

    conn = connect(db_path)
    try:
        outcome = conn.execute(
            "SELECT measured_value, change_pct FROM outcome_measurements "
            "WHERE case_id = 'case-1' AND measured_value IS NOT NULL"
        ).fetchone()
        job = conn.execute("SELECT status FROM job_runs WHERE job_name = 'update_outcome_measurements'").fetchone()
    finally:
        conn.close()

    assert result["updated_measurements"] == 1
    assert outcome["measured_value"] == 8000
    assert outcome["change_pct"] == 20
    assert job["status"] == "success"


def test_due_outcome_measurements_create_separate_7_14_28_rows(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    upsert_case_from_indication(conn, {
        "indication_id": "case-1",
        "client_id": "pilotton",
        "rule_id": "M02",
        "status": "open",
        "severity": "high",
        "first_detected_at": "2026-05-01T00:00:00+00:00",
        "payload": {"title": "CAPI"},
    })
    record_outcome(
        conn,
        case_id="case-1",
        client_id="pilotton",
        metric="cpa",
        baseline_value=10000,
        measured_value=None,
        baseline_start="2026-05-01",
        measurement_start="2026-05-01",
        payload={"conversions": 10},
    )

    updated = update_due_outcome_measurements(
        conn,
        client_id="pilotton",
        current_kpis={"cpa": 8500},
        today="2026-05-29",
    )
    rerun = update_due_outcome_measurements(
        conn,
        client_id="pilotton",
        current_kpis={"cpa": 8500},
        today="2026-05-29",
    )
    rows = list_outcomes(conn, client_id="pilotton", limit=10)
    measured_windows = sorted(
        row["measurement_end"]
        for row in rows
        if row["measured_value"] is not None
    )

    assert updated == 3
    assert rerun == 0
    assert measured_windows == ["2026-05-08", "2026-05-15", "2026-05-29"]


def test_improvement_pct_handles_metric_direction_and_zero_baseline():
    assert improvement_pct("cpa_change_pct", 10000, 8000) == 20
    assert improvement_pct("roas_change_pct", 2.0, 2.2) == 10.000000000000009
    assert improvement_pct("cpa_change_pct", 0, 8000) is None


def test_record_completion_outcome_parses_existing_achieved_effect(tmp_path):
    conn = connect(tmp_path / "zynect.db")
    row = record_completion_outcome(
        conn,
        {
            "indication_id": "pilotton:M02:meta:account:2026-05-04",
            "client_id": "pilotton",
            "rule_id": "M02",
            "resolved_date": "2026-05-20",
            "payload": {
                "achieved_effect": {
                    "minimum": "¥-58,000 / 月（保守）",
                    "realistic": "¥-115,500 / 月（現実）",
                }
            },
        },
    )
    conn.commit()

    assert row is not None
    assert row["metric"] == "estimated_monthly_value_yen"
    assert row["estimated_value_yen"] == 115500


def test_build_selection_traces_explains_dropoff_stages():
    loaded = [
        {"id": "R1", "layer": "foundation", "severity": "high"},
        {"id": "R2", "layer": "foundation", "severity": "high"},
        {"id": "R3", "layer": "foundation", "severity": "high"},
        {"id": "R4", "layer": "foundation", "severity": "high"},
    ]
    traces = build_selection_traces(
        client_id="pilotton",
        evaluation_date="2026-05-09",
        loaded_rules=loaded,
        matched_rules=[loaded[1], loaded[2], loaded[3]],
        eligible_rules=[loaded[2], loaded[3]],
        selected_rules=[loaded[3]],
    )

    by_rule_stage = {(t["rule_id"], t["stage"]): t for t in traces}
    assert by_rule_stage[("R1", "environment_match")]["reason"] == "environment_mismatch"
    assert by_rule_stage[("R2", "eligibility")]["reason"] == "trigger_or_prerequisite_or_cooldown"
    assert by_rule_stage[("R3", "daily_cap")]["status"] == "suppressed"
    assert by_rule_stage[("R4", "daily_cap")]["status"] == "selected"


def _rule_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "foundation").mkdir(parents=True)
    (root / "config" / "rules").mkdir(parents=True)
    (root / "config" / "verticals").mkdir(parents=True)
    (root / "config" / "ec_platforms").mkdir(parents=True)
    (root / "config" / "precision_categories").mkdir(parents=True)
    (root / "config" / "rule_messaging.yaml").write_text(
        yaml.safe_dump({"rules": {"F-MF-01": {"customer_title": "CVイベント確認"}}}, allow_unicode=True),
        encoding="utf-8",
    )
    (root / "config" / "foundation" / "measurement_foundation.yaml").write_text(
        yaml.safe_dump(
            {
                "layer": "foundation",
                "category": "measurement_foundation",
                "rules": [
                    {
                        "id": "F-MF-01",
                        "name": "CVイベント確認",
                        "severity": "high",
                        "root_cause_group": "measurement_foundation",
                        "axis_position": "TO-02",
                        "applies_to": {"ad_platforms": ["meta"]},
                        "trigger": {"condition": "client_state.cv_event_verified == False"},
                        "expected_impact": {"primary_metric": "cpa_change_pct", "primary_value": -10},
                    },
                    {
                        "id": "F-MF-02",
                        "name": "未接続ルール",
                        "severity": "medium",
                        "axis_position": "neutral",
                        "trigger": {"condition": "client_state.foo == False"},
                    },
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return root
