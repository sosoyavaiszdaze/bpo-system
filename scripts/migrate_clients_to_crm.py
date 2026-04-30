#!/usr/bin/env python3
"""clients.yaml → Twenty CRM 移行スクリプト

使用方法:
    python scripts/migrate_clients_to_crm.py --dry-run    # 実行プラン表示
    python scripts/migrate_clients_to_crm.py              # 実行
    python scripts/migrate_clients_to_crm.py --force      # 重複上書き
"""
import os
import sys
import yaml
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="clients.yaml → Twenty CRM 移行")
    parser.add_argument("--dry-run", action="store_true", help="実行プラン表示のみ")
    parser.add_argument("--force", action="store_true", help="既存client_idと重複してもupsert")
    args = parser.parse_args()

    # clients.yaml 読み込み
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "config", "clients.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    clients = config.get("clients", {})
    defaults = config.get("defaults", {})

    print("=== clients.yaml → Twenty CRM 移行 ===")
    print(f"クライアント数: {len(clients)}")
    print(f"モード: {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print()

    from engine.models import ClientConfig

    for client_id, data in clients.items():
        # デフォルト値をマージ
        merged = {**defaults, **data}
        client_config = ClientConfig.from_yaml(client_id, merged)

        print(f"--- {client_id} ---")
        print(f"  Name: {client_config.name}")
        print(f"  Active: {client_config.active}")
        print(f"  Objective: {client_config.objective}")
        print(f"  Google: {client_config.google_customer_id or '(未設定)'}")
        print(f"  Meta: {client_config.meta_account_id or '(未設定)'}")
        print(f"  TikTok: {client_config.tiktok_advertiser_id or '(未設定)'}")
        print(f"  AdTruth: {client_config.features.adtruth}")
        print(f"  SEO: {client_config.features.seo_audit}")
        print(f"  Slack: {client_config.slack_channel or '(未設定)'}")

        if args.dry_run:
            print("  → DRY-RUN: スキップ")
        else:
            api_url = os.environ.get("TWENTY_API_URL", "")
            api_key = os.environ.get("TWENTY_API_KEY", "")
            if not api_url or not api_key:
                print("  → ERROR: TWENTY_API_URL / TWENTY_API_KEY 未設定")
                continue

            from outputs.crm_twenty import TwentyCRM
            crm = TwentyCRM()

            existing = crm.get_client(client_id)
            if existing and not args.force:
                print("  → SKIP: 既存 (--force で上書き)")
                continue

            result = crm.upsert_client(client_config)
            if result:
                print(f"  → OK: CRM保存完了 (ID: {result})")
            else:
                print("  → WARNING: CRM保存失敗 (API未接続の可能性)")
        print()

    print("完了。")


if __name__ == "__main__":
    main()
