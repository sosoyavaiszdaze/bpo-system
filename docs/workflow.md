# BPO System ワークフロー

## 全体フロー

```
[データ取得] → [監査・分析] → [判断] → [実行] → [レポート] → [学習]
```

### Phase 1: データ取得 (Extract)
- pipeline.py `_phase_extract()`
- Google Ads / Meta / TikTok API → 3媒体統合
- CSV フォールバック（API未接続時）
- validator.py でunified format に正規化

### Phase 2: 監査・分析 (Analyze)
- pipeline.py `_phase_analyze()`
- ads_audit.py → 全チェックモジュール実行 → YAML評価 → スコアリング
- anomaly.py → 異常検知（前日比較）
- segment_waste.py → 無駄コスト検出
- fraud_audit.py → F01-F15 不正検知
- fraud_ingest.py → AdTruth/CSV/ヒューリスティック
- fraud_action.py → 複合判定 + 自動ブロック/フラグ
- conflict_detector.py → トレードオフ検出 + 軸矛盾検出
- claude_analyzer.py → Claude API 定性分析
- seo_audit.py → SEO監査

### Phase 3: レポート (Report)
- pipeline.py `_phase_report()`
- JSON保存 → Slack通知 → CRM保存 → PDF/HTML生成
- Twenty CRM: ヘルススナップショット + ActionLog

---

## 役割分担

### AI自動処理（人間介入不要）
| 処理 | 担当モジュール | エラー時 |
|---|---|---|
| 日次監査 | pipeline.py + scheduler.py | ログ + フォールバックスコア |
| 異常検知 | anomaly.py | 空アラート返却 |
| 不正検知 (93%) | fraud_action.py | flag_and_monitor にフォールバック |
| スコアリング | yaml_evaluator.py + scorer.py | フォールバック簡易スコア |
| SEO監査 | seo_audit.py | PageSpeed失敗時はHTMLのみ |
| Claude分析 | claude_analyzer.py | キャッシュ or スキップ |
| ルール自動更新 | adaptive_rule_engine.py | 変更せず現状維持 |
| 効果測定 | fraud_action.py 自動アンブロック | アンブロックせず継続 |

### 社内運用（Slack日本語通知）
| 処理 | 担当モジュール | 通知先 |
|---|---|---|
| Slack判断依頼 (7%) | slack_judgment.py | #fraud-judgment |
| エスカレーション | slack_judgment.py | L1→L2→L3 |
| 週次学習レビュー | scheduler.py | #fraud-judgment |
| 異常アラート | anomaly.py | クライアント別Slackチャンネル |
| Fraudアラート | fraud_action.py | #fraud-alerts |

### 社外クライアント向け（ビジネス指標のみ）
| 出力 | 担当モジュール | 内容 |
|---|---|---|
| PDFレポート | pdf_report.py | スコア・グレード・改善提案 |
| HTMLレポート | pdf_report.py | PDF読めない環境向け |
| CRMノート | crm_save.py / crm_twenty.py | 日次サマリー |
| 月次レポート | crm_twenty.py | ActionLog + HealthSnapshot集計 |

---

## 判断フロー（不正検知）

```
fraud_audit (F01-F15)
  ↓
cv_quality_scorer (CV品質判定)
  ↓
_composite_decision()
  ├── score < 0.85 → Monitor (自動)
  ├── fraud ≥ 20% & true_cv = 0 → Block (自動)
  ├── fraud ≥ 20% & fake ≥ 80% → Block (自動)
  ├── fraud ≥ 20% & true_cv ≥ 50 → Flag & Monitor (自動)
  ├── 学習DB 80%+ パターン → 自動適用
  └── 上記以外 (7%) → Slack判断依頼
       ├── 回答あり → アクション実行
       ├── リマインダー (1h/4h/12h)
       ├── エスカレーション (L1→L2→L3)
       └── タイムアウト → デフォルト適用
            └── judgment_db 記録 → 学習反映
```

---

## スケジューラ

| ジョブ | 頻度 | 担当 |
|---|---|---|
| 週次フル監査 | 日曜 02:00 | scheduler.py |
| 日次Fraudスキャン | 毎日 06:00 | scheduler.py |
| 月次ベンチマーク更新 | 毎月1日 00:00 | scheduler.py |
| 月次CRMレポート | 毎月1日 03:00 | scheduler.py |
| 判断エスカレーション | 15分毎 | scheduler.py |
| 週次学習レビュー | 月曜 09:00 | scheduler.py |

---

## データフロー

```
[API/CSV] → adapters/ → validator.py → unified format
                                      ↓
                                 ads_audit.py
                                   ├→ checks/common.py (C01-C15)
                                   ├→ checks/google.py (59 IDs)
                                   ├→ checks/meta.py (33 IDs)
                                   ├→ checks/tiktok.py (23 IDs)
                                   └→ checks/cross.py (3 IDs)
                                      ↓
                              yaml_evaluator.py (YAML評価 + polarity + prerequisite)
                                      ↓
                              scorer.py (プラットフォーム別 → クロスプラットフォーム)
                                      ↓
                              結果dict → JSON / Slack / CRM / PDF
```

---

## コーディングガイドライン

### AI自動処理
- エラー時はログ + フォールバック。人間介入不要にする
- `try/except` で囲み、1モジュールの失敗で全体が止まらないようにする
- step_status に ok/error/skipped を記録

### 社内向け機能
- Slack通知は日本語
- 技術詳細OK（check ID、スコア内訳等）
- エスカレーション付き

### 社外向け機能
- ビジネス指標のみ（スコア、グレード、改善提案）
- 技術用語は使わない
- ¥表記、日本語
