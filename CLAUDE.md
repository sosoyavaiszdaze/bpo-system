# CLAUDE.md — BPO System プロジェクト指示書

## プロジェクト概要
BPO System は広告運用の自動監査プラットフォーム。Google Ads / Meta / TikTok の3媒体に対応。

## 技術スタック
- Python 3.12+
- 依存: pyyaml, jinja2, playwright, python-dotenv, google-ads
- テスト: pytest + ruff
- CI: GitHub Actions

## 主要コマンド
```bash
python3 pipeline.py run all          # 全クライアント監査
python3 pipeline.py run yamamoto_demo # 特定クライアント
python3 pipeline.py test             # 設定チェック
python3 -m pytest tests/ -v          # テスト実行
```

## コーディング規約
- docstring: 全関数に日本語で記述
- ログ: `log = logging.getLogger("bpo")` を使用
- 環境変数: `.env` に格納、コード内にシークレットを絶対にハードコードしない
- 設定: `config/clients.yaml` と `config/thresholds.yaml` で管理
- チェックID: `G01`, `M01`, `T01`, `S01`, `X01`, `F01` 形式

## アーキテクチャ
```
pipeline.py → adapters/ → analyzers/ → engine/ → outputs/
                                      ↓
                                 config/rules/ (YAML)
```

## 重要な注意事項
- `outputs/crm_save.py`: 環境変数 `TWENTY_API_URL`, `TWENTY_API_KEY` が必要
- `config/clients.yaml`: webhook は `webhook_env` で環境変数名を指定
- unified format: 全アダプタが統一形式で `{"campaigns": [...], "totals": {...}}` を返す
