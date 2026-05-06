"""ClientConfig データクラスのテスト"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestClientConfigFromYaml:
    """ClientConfig.from_yaml のテスト"""

    def test_basic_load(self):
        from engine.models import ClientConfig
        data = {
            "name": "テストクライアント",
            "active": True,
            "objective": "cpa_minimize",
            "ads": {
                "google": {"customer_id": "123-456-7890"},
                "meta": {"account_id": "act_123"},
                "tiktok": {"advertiser_id": "999"},
            },
            "seo": {"site_url": "https://example.com"},
            "adtruth": {"enabled": True},
            "notifications": {"slack": {"channel": "#test", "webhook_env": "SLACK_WH_TEST"}},
        }
        config = ClientConfig.from_yaml("test_client", data)
        assert config.client_id == "test_client"
        assert config.name == "テストクライアント"
        assert config.objective == "cpa_minimize"
        assert config.google_customer_id == "123-456-7890"
        assert config.meta_account_id == "act_123"
        assert config.features.adtruth is True
        assert config.features.seo_audit is True
        assert config.slack_channel == "#test"
        assert config.source == "yaml"
        assert config.get("ads")["google"]["customer_id"] == "123-456-7890"
        assert config.get("notifications")["slack"]["webhook_env"] == "SLACK_WH_TEST"

    def test_minimal_data(self):
        from engine.models import ClientConfig
        config = ClientConfig.from_yaml("minimal", {"name": "Minimal"})
        assert config.client_id == "minimal"
        assert config.name == "Minimal"
        assert config.active is True
        assert config.google_customer_id == ""
        assert config.features.adtruth is False

    def test_from_crm(self):
        from engine.models import ClientConfig
        crm_record = {
            "clientId": "crm_client",
            "name": "CRM Client",
            "active": True,
            "objective": "roas_target",
            "googleCustomerId": "111-222-3333",
            "featuresAdtruth": True,
            "featuresSeoAudit": False,
            "slackChannel": "#crm-test",
        }
        config = ClientConfig.from_crm(crm_record)
        assert config.client_id == "crm_client"
        assert config.objective == "roas_target"
        assert config.google_customer_id == "111-222-3333"
        assert config.features.adtruth is True
        assert config.features.seo_audit is False
        assert config.source == "crm"
        assert config.get("ads")["google"]["customer_id"] == "111-222-3333"
        assert config.get("notifications")["slack"]["channel"] == "#crm-test"
        assert config.get("crm")["twenty"]["enabled"] is True


class TestSecrets:
    """config/secrets.py のテスト"""

    def test_get_secret(self, monkeypatch):
        from config.secrets import get_secret
        monkeypatch.setenv("META_ACCESS_TOKEN_TEST_CLIENT", "secret123")
        assert get_secret("META", "ACCESS_TOKEN", "test_client") == "secret123"

    def test_get_secret_not_found(self):
        from config.secrets import get_secret
        assert get_secret("NONEXIST", "KEY", "nobody") is None

    def test_require_secret_raises(self):
        from config.secrets import require_secret
        with pytest.raises(EnvironmentError):
            require_secret("NONEXIST", "KEY", "nobody")

    def test_get_global_secret(self, monkeypatch):
        from config.secrets import get_global_secret
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
        assert get_global_secret("ANTHROPIC_API_KEY") == "test_key"


class TestPipelineClientLoading:
    """pipeline.py のクライアント読み込みテスト"""

    def test_load_from_yaml(self):
        from pipeline import load_client_config
        config = load_client_config("yamamoto_demo")
        assert config.client_id == "yamamoto_demo"
        assert config.source == "yaml"

    def test_load_unknown_client(self):
        from pipeline import load_client_config
        config = load_client_config("nonexistent_client_xyz")
        assert config.source == "default"


class TestTwentyCRMClientMethods:
    """TwentyCRM client管理メソッドのテスト（API未接続）"""

    def test_list_clients_no_api(self):
        from notifiers.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        assert crm.list_clients() == []

    def test_get_client_no_api(self):
        from notifiers.crm_twenty import TwentyCRM
        crm = TwentyCRM(api_url="", api_key="")
        assert crm.get_client("test") is None

    def test_upsert_client_no_api(self):
        from notifiers.crm_twenty import TwentyCRM
        from engine.models import ClientConfig
        crm = TwentyCRM(api_url="", api_key="")
        config = ClientConfig(client_id="test", name="Test")
        assert crm.upsert_client(config) is None
