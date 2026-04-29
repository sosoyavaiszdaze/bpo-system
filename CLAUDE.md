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

## ID 体系 (Phase 2 完了)

Python check_id と YAML rule_id は統一済み。`id_mapping.yaml` は廃止。
`engine/id_mapper.py` はパススルー実装として残存（後方互換用）。

ルール数: Google 108, Meta 65, TikTok 46, SEO 45, AdTruth 15 (合計 279, enabled 277)

## スコアリング設計

- 全ルールの `weight` は 1.0 に統一
- 重み付けは `severity_weight × category_weight × polarity_multiplier` の3層
- 詳細: `docs/scoring_design.md`

## ワークフロー
業務フロー・役割分担の詳細は docs/workflow.md を参照。
コード変更時は「誰が使う機能か（AI自動/社内運用/社外クライアント）」を意識すること。
- AI自動処理 → エラー時はログ+フォールバック、人間介入不要にする
- 社内向け機能 → Slack通知は日本語、技術詳細OK
- 社外向け機能 → ビジネス指標のみ、技術用語は使わない

## 重要な注意事項
- `outputs/crm_save.py`: 環境変数 `TWENTY_API_URL`, `TWENTY_API_KEY` が必要
- `config/clients.yaml`: webhook は `webhook_env` で環境変数名を指定
- unified format: 全アダプタが統一形式で `{"campaigns": [...], "totals": {...}}` を返す
