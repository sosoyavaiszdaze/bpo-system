from engine.stores.cases import apply_api_evidence_to_cases, get_case
from engine.stores.db import connect


def test_api_resolved_evidence_records_execution_and_moves_case_to_measuring(tmp_path):
    from engine.stores.executions import list_case_executions

    db = tmp_path / "ops.sqlite"
    conn = connect(db)
    try:
        conn.execute(
            """
            INSERT INTO operational_cases (
              case_id, client_id, rule_id, title, status, severity, owner_type,
              first_detected_at, payload_json, created_at, updated_at
            ) VALUES (
              'case-1', 'pilotton', 'M02', 'CAPI check', 'waiting_client',
              'critical', 'client', '2026-05-12T09:00:00+09:00', '{}',
              datetime('now'), datetime('now')
            )
            """
        )

        result = apply_api_evidence_to_cases(
            conn,
            client_id="pilotton",
            evidence_map={
                "M02": {
                    "status": "resolved",
                    "source": "meta_api.pixel",
                    "reason": "Meta APIでCAPIを確認",
                    "value": {"capi_enabled": True},
                    "rule_group": "meta_capi_and_dedup",
                }
            },
            verified_at="2026-05-12T10:00:00+09:00",
        )

        case = get_case(conn, "case-1")
        executions = list_case_executions(conn, case_id="case-1")
    finally:
        conn.close()

    assert result["executions_recorded"] == 1
    assert result["transitions"] == 1
    assert case["status"] == "measuring"
    assert executions[0]["execution_status"] == "verified"
    assert executions[0]["evidence_quality"] == "high"
    assert executions[0]["payload"]["rule_group"] == "meta_capi_and_dedup"


def test_api_resolved_evidence_canonicalizes_legacy_monitoring_status(tmp_path):
    from engine.stores.executions import list_case_executions

    db = tmp_path / "ops.sqlite"
    conn = connect(db)
    try:
        conn.execute(
            """
            INSERT INTO operational_cases (
              case_id, client_id, rule_id, title, status, severity, owner_type,
              first_detected_at, payload_json, created_at, updated_at
            ) VALUES (
              'case-1', 'pilotton', 'M04', 'Domain verification', 'monitoring',
              'high', 'client', '2026-05-17T09:00:00+09:00', '{}',
              datetime('now'), datetime('now')
            )
            """
        )

        result = apply_api_evidence_to_cases(
            conn,
            client_id="pilotton",
            evidence_map={
                "M04": {
                    "status": "resolved",
                    "source": "meta_api.domain_verification",
                    "reason": "Meta APIでドメイン検証済みを確認",
                    "value": {"verified": True},
                    "rule_group": "meta_measurement_foundation",
                }
            },
            verified_at="2026-05-17T09:15:00+09:00",
        )

        case = get_case(conn, "case-1")
        executions = list_case_executions(conn, case_id="case-1")
    finally:
        conn.close()

    assert result["executions_recorded"] == 1
    assert result["transitions"] == 1
    assert case["status"] == "measuring"
    assert executions[0]["execution_status"] == "verified"
    assert executions[0]["payload"]["rule_group"] == "meta_measurement_foundation"


def test_api_manual_required_does_not_advance_case(tmp_path):
    db = tmp_path / "ops.sqlite"
    conn = connect(db)
    try:
        conn.execute(
            """
            INSERT INTO operational_cases (
              case_id, client_id, rule_id, title, status, severity, owner_type,
              first_detected_at, payload_json, created_at, updated_at
            ) VALUES (
              'case-1', 'pilotton', 'M02', 'CAPI check', 'waiting_client',
              'critical', 'client', '2026-05-12T09:00:00+09:00', '{}',
              datetime('now'), datetime('now')
            )
            """
        )

        result = apply_api_evidence_to_cases(
            conn,
            client_id="pilotton",
            evidence_map={"M02": {"status": "manual_required"}},
        )
        case = get_case(conn, "case-1")
    finally:
        conn.close()

    assert result["executions_recorded"] == 0
    assert case["status"] == "waiting_client"
