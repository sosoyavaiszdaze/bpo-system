# クライアントオンボーディング手順

## 1. 事前準備

### 必要情報
| 項目 | 記入例 |
|---|---|
| クライアント名 | ABC株式会社 |
| クライアントID | abc_corp |
| 目標 | balanced / cpa_minimize / cv_maximize / roas_target |
| Google Ads Customer ID | 123-456-7890 |
| Meta Ad Account ID | act_1234567890 |
| TikTok Advertiser ID | 1234567890 |
| LP URL | https://example.com/lp |
| Slack チャンネル | #abc-ads-alert |

### 権限確認
- [ ] Google Ads: MCC 管理者アクセス
- [ ] Meta: Business Manager の広告アカウント分析者権限
- [ ] TikTok: Advertiser の読取り権限

## 2. `config/clients.yaml` にクライアント追加

```yaml
clients:
  abc_corp:
    name: "ABC株式会社"
    active: true
    objective: balanced

    ads:
      google:
        customer_id: "123-456-7890"
        developer_token_env: "GOOGLE_ADS_DEVELOPER_TOKEN"
        login_customer_id: "000-000-0000"  # MCC ID
      meta:
        account_id: "act_1234567890"
        access_token_env: "META_ACCESS_TOKEN_ABC"
      tiktok:
        advertiser_id: "1234567890"
        access_token_env: "TIKTOK_ACCESS_TOKEN_ABC"

    seo:
      enabled: true
      urls:
        - https://example.com/lp

    notifications:
      slack:
        webhook_env: "SLACK_WEBHOOK_ABC"

    crm:
      twenty:
        enabled: true
        company_id: "xxxx-xxxx"

    anthropic_model: "claude-sonnet-4-6"
```

## 3. `.env` に API キーを追加

```bash
# .env に追記
META_ACCESS_TOKEN_ABC=ea_xxxxx
TIKTOK_ACCESS_TOKEN_ABC=xxxxx
SLACK_WEBHOOK_ABC=https://hooks.slack.com/services/xxx/xxx/xxx
```

## 4. テスト実行

```bash
# 設定チェック
python3 pipeline.py test

# テスト監査
python3 pipeline.py run abc_corp
```

### 確認項目
- [ ] データ取得が成功するか（CSV → API の順で確認）
- [ ] スコアが妥当な範囲か（テストデータなら 30-70 が正常）
- [ ] PDF が `reports/YYYY-MM-DD/` に生成されたか
- [ ] Slack 通知が届くか（webhook 設定時）

## 5. Slack チャンネル設定

1. Slack で `#abc-ads-alert` チャンネルを作成
2. Incoming Webhook を作成
3. Webhook URL を `.env` の `SLACK_WEBHOOK_ABC` に設定
4. テスト通知: `python3 pipeline.py run abc_corp`

## 6. 本番切り替え

- `clients.yaml` で `active: true` を確認
- スケジューラが動作中か確認: `python3 integrations/scheduler.py`
- 初回フル監査を手動実行して結果を確認
