# クライアント管理ガイド

## 三層分離

| 層 | 保存場所 | 内容 |
|---|---|---|
| マスターデータ | Twenty CRM (Client オブジェクト) | クライアントID、名前、目標、プラットフォームID、機能フラグ |
| 秘密情報 | `.env` (環境変数) | APIトークン、Webhook URL |
| フォールバック | `config/clients.yaml` | CRM障害時の代替データ |

## 環境変数命名規約

```
<SERVICE>_<KEY>_<CLIENT_ID_UPPERCASE>
```

| 環境変数名 | 用途 |
|---|---|
| `META_ACCESS_TOKEN_YAMAMOTO_DEMO` | Meta Ads API トークン |
| `TIKTOK_ACCESS_TOKEN_YAMAMOTO_DEMO` | TikTok Ads API トークン |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads 開発者トークン (全クライアント共通) |
| `SLACK_WEBHOOK_YAMAMOTO_DEMO` | Slack Webhook URL |
| `ADTRUTH_API_KEY_YAMAMOTO_DEMO` | AdTruth API キー |
| `TWENTY_API_URL` | Twenty CRM API URL (共通) |
| `TWENTY_API_KEY` | Twenty CRM API キー (共通) |
| `ANTHROPIC_API_KEY` | Claude API キー (共通) |

## 新規顧客オンボーディング

1. **Twenty CRM で Client オブジェクトを作成**
   - client_id (一意), name, objective を入力
   - プラットフォームIDは顧客のMCC承認後に入力

2. **`.env` に秘密情報を追記**
   ```
   META_ACCESS_TOKEN_NEW_CLIENT=xxx
   TIKTOK_ACCESS_TOKEN_NEW_CLIENT=xxx
   SLACK_WEBHOOK_NEW_CLIENT=https://hooks.slack.com/xxx
   ```

3. **疎通確認**
   ```bash
   python pipeline.py test
   python pipeline.py run new_client
   ```

## CRM障害時の対応

1. `config/clients.yaml` にクライアント情報を追記
2. `TWENTY_API_URL` を空にすると自動的にYAMLフォールバック
3. CRM復旧後は `python scripts/migrate_clients_to_crm.py` で再同期

## 移行スクリプト

```bash
# 実行プラン確認
python scripts/migrate_clients_to_crm.py --dry-run

# 実行
python scripts/migrate_clients_to_crm.py

# 既存データ上書き
python scripts/migrate_clients_to_crm.py --force
```
