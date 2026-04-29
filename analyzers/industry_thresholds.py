"""業界別動的閾値 — クライアントの業界に基づき不正検知閾値を調整"""
import logging

log = logging.getLogger("bpo")

INDUSTRY_THRESHOLDS = {
    "gaming": {
        "industry": "gaming",
        "ip_block_threshold": 0.80,
        "publisher_block_threshold": 0.80,
        "fraud_rate_block": 0.15,
        "cv_safe_threshold": 30,
        "note": "ゲーム業界は不正率が高いため閾値を厳しめに設定",
    },
    "finance": {
        "industry": "finance",
        "ip_block_threshold": 0.90,
        "publisher_block_threshold": 0.90,
        "fraud_rate_block": 0.25,
        "cv_safe_threshold": 80,
        "note": "金融業界はCV単価が高いため誤ブロックを避ける",
    },
    "ecommerce": {
        "industry": "ecommerce",
        "ip_block_threshold": 0.85,
        "publisher_block_threshold": 0.85,
        "fraud_rate_block": 0.20,
        "cv_safe_threshold": 50,
        "note": "EC標準設定",
    },
    "healthcare": {
        "industry": "healthcare",
        "ip_block_threshold": 0.90,
        "publisher_block_threshold": 0.90,
        "fraud_rate_block": 0.25,
        "cv_safe_threshold": 100,
        "note": "医療業界はリード品質が最重要。誤ブロック防止優先",
    },
    "education": {
        "industry": "education",
        "ip_block_threshold": 0.85,
        "publisher_block_threshold": 0.85,
        "fraud_rate_block": 0.20,
        "cv_safe_threshold": 40,
        "note": "教育業界は季節変動が大きいため標準寄り",
    },
    "default": {
        "industry": "default",
        "ip_block_threshold": 0.85,
        "publisher_block_threshold": 0.85,
        "fraud_rate_block": 0.20,
        "cv_safe_threshold": 50,
        "note": "デフォルト設定",
    },
}


def apply_dynamic_thresholds(client_config):
    """クライアント設定から業界を判定し、動的閾値を返す

    Args:
        client_config: クライアント設定dict (industry フィールドを参照)
    Returns:
        dict: 業界別閾値
    """
    industry = client_config.get("industry", "default").lower()
    thresholds = INDUSTRY_THRESHOLDS.get(industry, INDUSTRY_THRESHOLDS["default"])
    log.debug(f"業界閾値適用: {industry} → {thresholds['note']}")
    return thresholds


def get_available_industries():
    """利用可能な業界一覧を返す"""
    return list(INDUSTRY_THRESHOLDS.keys())
