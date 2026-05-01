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
python3 pipeline.py run all          # 全クライアント監査（v2 のみ、後方互換デフォルト）
python3 pipeline.py run yamamoto_demo # 特定クライアント
python3 pipeline.py test             # 設定チェック
python3 -m pytest tests/ -v          # テスト実行

# v3 レポート（Day 4-5 で追加）
python3 pipeline.py run yamamoto_demo --report-version v3    # v3 のみ生成
python3 pipeline.py run yamamoto_demo --report-version both  # v2 + v3 並行生成（移行期間）
```

## v3 レポートと v2 の違い

| 項目 | v2 | v3 |
|------|----|----|
| ページ数 | 1〜3 | 8（表紙/サマリ/Top5/媒体×3/Insights/付録） |
| テンプレート | `templates/report.html` | `templates/v3/*.html` |
| 出力ファイル | `{client}_report.pdf` | `{client}_report_v3.pdf` |
| 業界比較 | なし | 業界平均/Zynect推奨/現状の3軸 |
| 想定効果 | なし | 月次削減見込み額+信頼度+発現週数 |
| Claude API | 補助分析 | 顧客語翻訳・ナラティブ・Zynect Insights（フォールバックあり） |
| 重み付け | 既存 scorer | `config/priority_weights.yaml`（パターンC=quick_win優先） |
| 業界別ベンチマーク | なし | `config/benchmarks.yaml`（5業界×3媒体） |

詳細は `docs/release_notes/v3.0.md`、設計は `docs/report_design/v3_*.md` を参照。

## v3 トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| Claude 文章が機械的 | `ANTHROPIC_API_KEY` 未設定 | `.env` に設定（v3 はフォールバックで動作するが品質低下） |
| 「業界平均データ未収集」表示 | `benchmarks.yaml` の null セル | Day 4 提示の要調査リスト参照 |
| CPA「—」表示 | conversions=0 で算出不能 | 仕様（v3 は意味不明な「¥0」を避けるため None で「—」表示） |
| 媒体ページ「分析対象外」表示 | 当該媒体のデータ未取得 | データソース（CSV / API）を確認 |
| company.industry 未対応 | `mobile_app` 等が benchmarks.yaml に未登録 | v3.1 で対応予定。暫定で `ec_retail` にマップ |
| `--report-version` 引数エラー | 値が `v2|v3|both` 以外 | 3 値のいずれかを指定 |

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
