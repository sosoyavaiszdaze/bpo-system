"""pipeline output step_status の回帰テスト"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _results():
    return {
        "client_id": "test_client",
        "client_name": "Test Client",
        "timestamp": "2026-05-04T00:00:00",
        "ads_audit": {},
        "step_status": {},
    }


def test_output_results_marks_slack_not_ready_as_skipped(monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.delenv("SLACK_MISSING", raising=False)

    results = _results()
    client_cfg = {"notifications": {"slack": {"webhook_env": "SLACK_MISSING"}}}
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["slack"] == "skipped"
    assert results["step_status"]["lark"] == "skipped"


def test_output_results_marks_slack_send_failure(monkeypatch, tmp_path):
    import pipeline
    from outputs import slack_notify

    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(slack_notify, "send_notification", lambda *_args: False)

    results = _results()
    client_cfg = {"notifications": {"slack": {"webhook_url": "https://example.test/webhook"}}}
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["slack"] == "error"
    assert results["step_status"]["lark"] == "skipped"


def test_output_results_marks_lark_not_ready_as_skipped(monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.delenv("LARK_MISSING", raising=False)

    results = _results()
    client_cfg = {
        "notifications": {
            "platform": "lark",
            "lark": {"webhook_env": "LARK_MISSING"},
        }
    }
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["lark"] == "skipped"
    assert results["step_status"]["slack"] == "skipped"


def test_output_results_marks_lark_send_failure(monkeypatch, tmp_path):
    import pipeline
    from outputs import lark_notify

    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(lark_notify, "send_lark_notification", lambda *_args: False)

    results = _results()
    client_cfg = {
        "notifications": {
            "platform": "lark",
            "lark": {"webhook_env": "LARK_READY"},
        }
    }
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["lark"] == "error"
    assert results["step_status"]["slack"] == "skipped"


def test_output_results_marks_crm_phase_b_as_skipped(monkeypatch, tmp_path):
    import pipeline
    from outputs import crm_twenty

    class DummyCRM:
        api_url = ""
        api_key = ""

        def save_health_snapshot(self, client_id, snapshot_data):
            raise AssertionError("CRM should not be called without credentials")

    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(crm_twenty, "TwentyCRM", DummyCRM)

    results = _results()
    client_cfg = {"crm": {"twenty": {"enabled": True}}}
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["crm"] == "skipped"


def test_output_results_marks_crm_save_failure(monkeypatch, tmp_path):
    import pipeline
    from outputs import crm_twenty

    class DummyCRM:
        api_url = "https://crm.example.test"
        api_key = "test_key"

        def save_health_snapshot(self, client_id, snapshot_data):
            return None

    monkeypatch.setattr(pipeline, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(crm_twenty, "TwentyCRM", DummyCRM)

    results = _results()
    client_cfg = {"crm": {"twenty": {"enabled": True}}}
    pipeline.output_results("test_client", client_cfg, results, report_version="none")

    assert results["step_status"]["crm"] == "error"
