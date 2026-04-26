"""HTTP リトライヘルパー — エクスポネンシャルバックオフ付きリトライ"""
import time
import logging
import urllib.request
import urllib.error

log = logging.getLogger("bpo")


def fetch_with_retry(url, headers=None, data=None, method="GET",
                     max_retries=3, backoff_base=2, timeout=30):
    """エクスポネンシャルバックオフ付きHTTPリクエスト

    Args:
        url: リクエスト先URL
        headers: HTTPヘッダーdict
        data: POSTデータ (bytes)
        method: HTTPメソッド
        max_retries: 最大リトライ回数
        backoff_base: バックオフ基数（秒）
        timeout: リクエストタイムアウト（秒）
    Returns:
        tuple: (response_body_str, status_code, response_headers_dict)
    Raises:
        urllib.error.URLError: 全リトライ失敗時
    """
    headers = headers or {}
    last_error = None

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                return body, resp.status, dict(resp.headers)
        except urllib.error.HTTPError as e:
            last_error = e
            # 4xx クライアントエラーはリトライしない（429 Rate Limit除く）
            if 400 <= e.code < 500 and e.code != 429:
                raise
            wait = backoff_base ** attempt
            log.warning(f"HTTP {e.code} リトライ {attempt + 1}/{max_retries}: {url} ({wait}秒待機)")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_error = e
            wait = backoff_base ** attempt
            log.warning(f"接続エラー リトライ {attempt + 1}/{max_retries}: {url} ({wait}秒待機)")
            time.sleep(wait)

    if last_error:
        raise last_error
    return None, 0, {}
