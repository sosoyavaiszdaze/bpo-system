"""秘密情報取得モジュール

命名規約: <SERVICE>_<KEY>_<CLIENT_ID_UPPERCASE>
例: META_ACCESS_TOKEN_YAMAMOTO_DEMO
    GOOGLE_ADS_DEVELOPER_TOKEN
    SLACK_WEBHOOK_YAMAMOTO_DEMO
"""
import os
import logging

log = logging.getLogger("bpo")


def get_secret(service, key, client_id):
    """命名規約ベースで環境変数を取得

    Args:
        service: サービス名 (META, GOOGLE_ADS, SLACK, ADTRUTH等)
        key: キー名 (ACCESS_TOKEN, DEVELOPER_TOKEN, WEBHOOK等)
        client_id: クライアントID (yamamoto_demo等)
    Returns:
        str or None: 環境変数の値
    """
    env_name = f"{service.upper()}_{key.upper()}_{client_id.upper()}"
    value = os.getenv(env_name)
    if value is None:
        log.debug(f"Secret not found: {env_name}")
    return value


def get_global_secret(name):
    """BPO共通の秘密情報（client_idに依存しない）"""
    return os.getenv(name)


def require_secret(service, key, client_id):
    """秘密情報必須版（無ければ例外）"""
    value = get_secret(service, key, client_id)
    if value is None:
        env_name = f"{service.upper()}_{key.upper()}_{client_id.upper()}"
        raise EnvironmentError(f"Required secret not found: {env_name}")
    return value
