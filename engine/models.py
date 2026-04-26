"""データモデル定義 — Pydantic による config/data バリデーション"""
import logging

log = logging.getLogger("bpo")

try:
    from pydantic import BaseModel, Field
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
