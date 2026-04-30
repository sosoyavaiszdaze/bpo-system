"""Intent Override 抑止フィルターのテスト"""
import os
import sys
import yaml
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mock_clients_yaml(tmp_path, monkeypatch):
    """テスト用clients.yamlを生成"""
    yaml_data = {
        "clients": {
            "test_client": {
                "name": "テスト",
                "intent_overrides": [
                    {
                        "rule_ids": ["C05", "C06"],
                        "reason": "テスト期間中",
                        "suppress_action": "skip_notification",
                        "expires_at": (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"),
                    },
                    {
                        "rule_ids": ["G25"],
                        "reason": "命名規則は社内独自",
                        "suppress_action": "add_context_note",
                        "expires_at": None,
                    },
                    {
                        "rule_ids": ["C01"],
                        "reason": "期限切れoverride",
                        "suppress_action": "skip_notification",
                        "expires_at": "2020-01-01",
                    },
                    {
                        "rule_ids": ["C02"],
                        "reason": "severity下げ",
                        "suppress_action": "downgrade_severity",
                        "expires_at": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
                    },
                    {
                        "rule_ids": ["C10"],
                        "reason": "もうすぐ期限切れ",
                        "suppress_action": "skip_notification",
                        "expires_at": (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d"),
                    },
                ],
            }
        }
    }
    yaml_path = tmp_path / "clients.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_data, f, allow_unicode=True)

    import engine.intent_filter as intent_mod
    monkeypatch.setattr(intent_mod, "CONFIG_DIR", str(tmp_path))
    return yaml_path


class TestIntentFilter:

    def test_active_override_suppresses_check(self, mock_clients_yaml):
        """有効期限内のoverrideがsuppressed=Trueを設定すること"""
        from engine.intent_filter import filter_checks
        checks = [
            {"id": "C05", "passed": False, "platform": "google", "severity": "high"},
            {"id": "C07", "passed": False, "platform": "google", "severity": "medium"},
        ]
        result = filter_checks("test_client", checks)
        c05 = next(c for c in result if c["id"] == "C05")
        c07 = next(c for c in result if c["id"] == "C07")
        assert c05.get("suppressed") is True
        assert c07.get("suppressed") is None

    def test_expired_override_does_not_suppress(self, mock_clients_yaml):
        """期限切れのoverrideが無視されること"""
        from engine.intent_filter import filter_checks
        checks = [{"id": "C01", "passed": False, "platform": "google", "severity": "medium"}]
        result = filter_checks("test_client", checks)
        assert result[0].get("suppressed") is None

    def test_null_expires_is_permanent(self, mock_clients_yaml):
        """expires_at=null が無期限として機能すること"""
        from engine.intent_filter import filter_checks
        checks = [{"id": "G25", "passed": False, "platform": "google", "severity": "low"}]
        result = filter_checks("test_client", checks)
        assert result[0].get("context_note") is not None
        assert "意図的設定" in result[0]["context_note"]

    def test_downgrade_severity(self, mock_clients_yaml):
        """severity低下: critical→high→medium→low→info"""
        from engine.intent_filter import _downgrade_severity
        assert _downgrade_severity("critical") == "high"
        assert _downgrade_severity("high") == "medium"
        assert _downgrade_severity("medium") == "low"
        assert _downgrade_severity("low") == "info"
        assert _downgrade_severity("info") == "info"

    def test_downgrade_severity_applied(self, mock_clients_yaml):
        """downgrade_severityがチェックに正しく適用"""
        from engine.intent_filter import filter_checks
        checks = [{"id": "C02", "passed": False, "platform": "google", "severity": "critical"}]
        result = filter_checks("test_client", checks)
        assert result[0]["severity"] == "high"

    def test_add_context_note(self, mock_clients_yaml):
        """context_note が正しく付加されること"""
        from engine.intent_filter import filter_checks
        checks = [{"id": "G25", "passed": False, "platform": "google", "severity": "low"}]
        result = filter_checks("test_client", checks)
        assert "命名規則は社内独自" in result[0]["context_note"]

    def test_get_expiring_overrides_within_30_days(self, mock_clients_yaml):
        """30日以内のもののみ返すこと"""
        from engine.intent_filter import get_expiring_overrides
        expiring = get_expiring_overrides("test_client", days=30)
        rule_ids = [rid for o in expiring for rid in o.get("rule_ids", [])]
        assert "C10" in rule_ids  # 15日後に期限切れ

    def test_get_expiring_overrides_excludes_null(self, mock_clients_yaml):
        """expires_at=null は返さないこと"""
        from engine.intent_filter import get_expiring_overrides
        expiring = get_expiring_overrides("test_client", days=365)
        rule_ids = [rid for o in expiring for rid in o.get("rule_ids", [])]
        assert "G25" not in rule_ids  # null = 無期限 → 除外
