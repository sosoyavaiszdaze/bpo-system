#!/usr/bin/env python3
"""Meta API 接続テストスクリプト"""
import os
import sys
import json
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.environ.get("META_ACCESS_TOKEN_BANDAL", "")
ACCOUNT_ID = "act_916507360736121"
API_BASE = "https://graph.facebook.com/v22.0"

ERROR_CODES = {
    190: "トークン無効/期限切れ",
    100: "パラメータ不正",
    200: "権限不足",
    273: "アカウントアクセス不可",
}


def api_get(endpoint, params=None):
    """Meta Graph API GET リクエスト"""
    url = f"{API_BASE}/{endpoint}"
    query_params = {"access_token": TOKEN}
    if params:
        query_params.update(params)
    query = urllib.parse.urlencode(query_params)
    url = f"{url}?{query}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            error = json.loads(body).get("error", {})
            code = error.get("code", 0)
            msg = error.get("message", str(e))
            desc = ERROR_CODES.get(code, "不明なエラー")
            return None, f"HTTP {e.code} | エラーコード {code}: {desc}\n  {msg}"
        except Exception:
            return None, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return None, str(e)


def main():
    print("=== Meta API 接続テスト (BANDAL GAMING) ===\n")

    if not TOKEN:
        print("ERROR: META_ACCESS_TOKEN_BANDAL が .env に設定されていません")
        sys.exit(1)

    print(f"Account ID: {ACCOUNT_ID}")
    print(f"Token: {TOKEN[:20]}...{TOKEN[-10:]}")
    print()

    # Test 1: トークン有効性
    print("[Test 1] GET /me — トークン有効性確認")
    data, err = api_get("me")
    if data:
        print(f"  OK: id={data.get('id')}, name={data.get('name', 'N/A')}")
    else:
        print(f"  NG: {err}")

    # Test 2: アカウントアクセス
    print(f"\n[Test 2] GET /{ACCOUNT_ID} — アカウントアクセス確認")
    data, err = api_get(ACCOUNT_ID, {"fields": "name,account_status"})
    if data:
        status_map = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW"}
        status = status_map.get(data.get("account_status"), data.get("account_status"))
        print(f"  OK: name={data.get('name')}, status={status}")
    else:
        print(f"  NG: {err}")

    # Test 3: キャンペーン取得
    print(f"\n[Test 3] GET /{ACCOUNT_ID}/campaigns — キャンペーン取得確認")
    data, err = api_get(f"{ACCOUNT_ID}/campaigns", {"fields": "name,status", "limit": "5"})
    if data:
        campaigns = data.get("data", [])
        print(f"  OK: {len(campaigns)}件取得")
        for c in campaigns[:5]:
            print(f"    - {c.get('name')} ({c.get('status')})")
    else:
        print(f"  NG: {err}")

    print("\n=== テスト完了 ===")


if __name__ == "__main__":
    import urllib.parse
    main()
