"""データモデル定義 — Pydantic による config/data バリデーション"""
import logging

log = logging.getLogger("bpo")

try:
    from pydantic import BaseModel
    from typing import Optional, Literal
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    log.debug("pydantic 未インストール: バリデーション機能は無効")


if PYDANTIC_AVAILABLE:
    class CampaignModel(BaseModel):
        """unified format のキャンペーンデータモデル"""
        campaign: str = ""
        platform: Literal["google", "meta", "tiktok", "unknown"] = "unknown"
        campaign_type: str = "unknown"
        status: str = "ENABLED"
        bidding_strategy: str = "unknown"
        daily_budget: float = 0.0
        impressions: float = 0.0
        clicks: float = 0.0
        cost: float = 0.0
        conversions: float = 0.0
        cpa: float = 0.0
        ctr: float = 0.0
        cpm: float = 0.0
        frequency: float = 0.0
        roas: float = 0.0
        revenue: float = 0.0
        conversion_value: float = 0.0
        ad_count: int = 0
        keyword_count: int = 0
        learning_phase: bool = False
        enhanced_conversions: bool = False

        model_config = {"extra": "allow"}

    class CheckResultModel(BaseModel):
        """チェック結果の標準モデル"""
        id: str
        passed: bool
        campaign: str = ""
        platform: str = ""
        message: str = ""
        severity: str = "medium"
        conflict_group: Optional[str] = None

    class ClientConfigModel(BaseModel):
        """クライアント設定のバリデーション"""
        name: str = ""
        active: bool = True
        objective: str = "balanced"

        model_config = {"extra": "allow"}


def validate_campaign(data: dict) -> dict:
    """キャンペーンデータをPydanticで検証（利用可能な場合）"""
    if not PYDANTIC_AVAILABLE:
        return data
    try:
        model = CampaignModel(**data)
        return model.model_dump()
    except Exception as e:
        log.debug(f"Campaign validation warning: {e}")
        return data


def validate_client_config(config: dict) -> dict:
    """クライアント設定をPydanticで検証"""
    if not PYDANTIC_AVAILABLE:
        return config
    try:
        model = ClientConfigModel(**config)
        return model.model_dump()
    except Exception as e:
        log.warning(f"Client config validation error: {e}")
        return config


# ============================================================
# ClientConfig データクラス（CRM/YAML共通）
# ============================================================
from dataclasses import dataclass, field as dataclass_field


@dataclass
class ClientFeatures:
    """クライアント機能フラグ"""
    adtruth: bool = False
    seo_audit: bool = True
    claude_analysis: bool = True


@dataclass
class ClientConfig:
    """Twenty CRM / YAML 共通のクライアント設定型"""
    client_id: str = ""
    name: str = ""
    active: bool = True
    objective: str = "balanced"
    target_cpa: float = 0
    target_roas: float = 0

    # プラットフォームID
    google_customer_id: str = ""
    google_login_customer_id: str = ""
    meta_account_id: str = ""
    tiktok_advertiser_id: str = ""

    # 機能フラグ
    features: ClientFeatures = dataclass_field(default_factory=ClientFeatures)

    # 通知設定（環境変数名のみ）
    slack_channel: str = ""
    slack_webhook_env: str = ""

    # 高度な設定
    intent_overrides: dict = dataclass_field(default_factory=dict)
    threshold_overrides: dict = dataclass_field(default_factory=dict)
    schedule_cron: str = "0 9 * * *"
    timezone: str = "Asia/Tokyo"

    # メタデータ
    source: str = "yaml"  # "crm" or "yaml"

    @classmethod
    def from_yaml(cls, client_id, data):
        """clients.yaml の dict から生成"""
        ads = data.get("ads", {})
        seo_cfg = data.get("seo", {})
        adtruth_cfg = data.get("adtruth", {})
        notif = data.get("notifications", {}).get("slack", {})

        features = ClientFeatures(
            adtruth=adtruth_cfg.get("enabled", False),
            seo_audit=bool(seo_cfg.get("site_url")),
            claude_analysis=True,
        )

        return cls(
            client_id=client_id,
            name=data.get("name", ""),
            active=data.get("active", True),
            objective=data.get("objective", "balanced"),
            google_customer_id=ads.get("google", {}).get("customer_id", ""),
            meta_account_id=ads.get("meta", {}).get("account_id", ""),
            tiktok_advertiser_id=ads.get("tiktok", {}).get("advertiser_id", ""),
            features=features,
            slack_channel=notif.get("channel", ""),
            slack_webhook_env=notif.get("webhook_env", ""),
            schedule_cron=data.get("schedule", "0 9 * * *"),
            timezone=data.get("timezone", "Asia/Tokyo"),
            source="yaml",
        )

    @classmethod
    def from_crm(cls, crm_record):
        """Twenty CRM レコードから生成"""
        features = ClientFeatures(
            adtruth=crm_record.get("featuresAdtruth", False),
            seo_audit=crm_record.get("featuresSeoAudit", True),
            claude_analysis=crm_record.get("featuresClaudeAnalysis", True),
        )

        return cls(
            client_id=crm_record.get("clientId", ""),
            name=crm_record.get("name", ""),
            active=crm_record.get("active", True),
            objective=crm_record.get("objective", "balanced"),
            target_cpa=crm_record.get("targetCpa", 0),
            target_roas=crm_record.get("targetRoas", 0),
            google_customer_id=crm_record.get("googleCustomerId", ""),
            google_login_customer_id=crm_record.get("googleLoginCustomerId", ""),
            meta_account_id=crm_record.get("metaAccountId", ""),
            tiktok_advertiser_id=crm_record.get("tiktokAdvertiserId", ""),
            features=features,
            slack_channel=crm_record.get("slackChannel", ""),
            slack_webhook_env=crm_record.get("slackWebhookEnv", ""),
            schedule_cron=crm_record.get("scheduleCron", "0 9 * * *"),
            timezone=crm_record.get("timezone", "Asia/Tokyo"),
            source="crm",
        )
