# BPO System 運用マニュアル

## 1. 日次運用

```bash
# 全クライアント監査
python3 pipeline.py run all

# 特定クライアント
python3 pipeline.py run yamamoto_demo
```

出力:
- `reports/YYYY-MM-DD/<client>_results.json` — JSON結果
- `reports/YYYY-MM-DD/<client>_report.pdf` — PDFレポート
- Slack 通知 (webhook設定時)
- Twenty CRM 保存 (API設定時)

## 2. 新規クライアント追加

`config/clients.yaml` に追加:

```yaml
clients:
  new_client:
    name: "新規クライアント"
    active: true
    objective: balanced   # balanced / cpa_minimize / cv_maximize / roas_target
    ads:
      google:
        customer_id: "XXX-XXX-XXXX"
        developer_token_env: "GOOGLE_ADS_DEVELOPER_TOKEN"
      meta:
        account_id: "act_XXXXXXXXX"
        access_token_env: "META_ACCESS_TOKEN_NEW_CLIENT"
      tiktok:
        advertiser_id: "XXXXXXXXX"
        access_token_env: "TIKTOK_ACCESS_TOKEN_NEW_CLIENT"
    notifications:
      slack:
        webhook_env: "SLACK_WEBHOOK_NEW_CLIENT"
```

## 3. 閾値カスタマイズ

`config/thresholds.yaml` を編集:

```yaml
common:
  ctr_min: 1.0          # CTR最低基準 (%)
  frequency_max: 4.0    # フリークエンシー上限
  cv_zero_cost_min: 5000 # ゼロCV警告閾値 (円)
  cpa_spike_pct: 20     # CPA スパイク判定 (%)
```

## 4. スケジューラ

```bash
# 手動起動
python3 integrations/scheduler.py
```

| スケジュール | 時刻(JST) | 内容 |
|---|---|---|
| 週次フル監査 | 日曜 02:00 | 全クライアント |
| 日次Fraud | 毎日 06:00 | 不正検知のみ |
| 月次ベンチマーク | 1日 00:00 | 業界指標更新 |

## 5. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `TWENTY_API_KEY 未設定` | `.env` にAPIキーを設定 |
| `google-ads 未インストール` | `pip install google-ads` |
| `Playwright chromium 未発見` | `playwright install chromium` |
| `Score が異常に低い` | `config/thresholds.yaml` の閾値を確認 |
| `Claude分析スキップ` | `.env` に `ANTHROPIC_API_KEY` を設定 |

## 6. テスト

```bash
# 全テスト
python3 -m pytest tests/ -v

# 設定チェックのみ
python3 pipeline.py test
```
