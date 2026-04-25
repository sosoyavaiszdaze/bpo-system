# BPO System

広告運用の自動監査・最適化プラットフォーム。Google Ads / Meta Ads / TikTok Ads の3媒体に対応し、175項目の広告チェック、45項目のSEO監査、不正検知を統合実行する。

## セットアップ

```bash
# 1. 仮想環境
python3 -m venv venv
source venv/bin/activate

# 2. 依存関係インストール
pip install -r requirements.txt

# 3. 環境変数
cp .env.example .env
# .env を編集して各種APIキーを設定

# 4. Playwright (SEO監査・PDF生成用)
playwright install chromium
```

## コマンド

```bash
# 全クライアント監査実行
python3 pipeline.py run all

# 特定クライアント
python3 pipeline.py run yamamoto_demo

# 設定テスト
python3 pipeline.py test
```

## ディレクトリ構造

```
bpo-system/
├── pipeline.py              # メインオーケストレータ
├── config/
│   ├── clients.yaml         # クライアント設定
│   ├── thresholds.yaml      # 閾値設定
│   ├── model.yaml           # Claude API設定
│   ├── context-overrides.yaml # トレードオフ解決記録
│   ├── rules/               # YAML監査ルール定義
│   ├── references/          # ベンチマーク等参照データ
│   └── prompts/             # Claude APIプロンプト
├── adapters/
│   ├── csv_adapter.py       # CSVデータ読込
│   ├── meta_adapter.py      # Meta Marketing API
│   ├── tiktok_adapter.py    # TikTok Business API
│   ├── google_adapter.py    # Google Ads API
│   └── validator.py         # データバリデーション
├── analyzers/
│   ├── ads_audit.py         # 広告監査オーケストレータ
│   ├── checks/              # プラットフォーム別チェック
│   ├── anomaly.py           # 異常検知
│   ├── segment_waste.py     # 無駄コスト検出
│   ├── fraud_audit.py       # 不正検知
│   ├── fraud_ingest.py      # AdTruthデータ取得
│   └── fraud_action.py      # 不正対応アクション
├── engine/
│   ├── yaml_evaluator.py    # YAMLルール評価エンジン
│   ├── scorer.py            # スコアリング
│   ├── claude_analyzer.py   # Claude API定性分析
│   ├── conflict_detector.py # トレードオフ検出
│   └── report_generator.py  # レポート生成統合
├── seo/
│   ├── seo_audit.py         # SEO監査
│   └── playwright_audit.py  # Playwright LP実測
├── outputs/
│   ├── slack_notify.py      # Slack Block Kit通知
│   ├── pdf_report.py        # PDF生成
│   └── crm_save.py          # Twenty CRM保存
├── integrations/
│   ├── slack_bot.py         # Slack Bot
│   └── scheduler.py         # APSchedulerスケジューラ
├── templates/
│   └── report.html          # PDFテンプレート
├── tests/                   # pytest テスト
├── scripts/                 # ユーティリティ
├── docs/                    # 運用マニュアル
└── data/                    # テストCSV
```

## 設計文書

- 設計文書 v1.3 に準拠
- 監査チェック: 175項目 (Google 84 + Meta 56 + TikTok 35)
- SEO: 45項目 (Layer1 YAML 20 + Layer3 Playwright 25)
- スコアリング: `S = Σ(C_pass × W_sev × W_cat) / Σ(C_total × W_sev × W_cat) × 100`
