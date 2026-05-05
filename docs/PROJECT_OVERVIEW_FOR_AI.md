# BPO System (Zynect Media Agent) — プロジェクト構造解説

> **対象読者**: 本コードベースを引き継ぐ別 AI / 開発者
> **目的**: コードを読む前に「何を解いているか」「どこに何があるか」「主要概念」を 5 分で把握する
> **生成日**: 2026-05-03
> **対象ブランチ**: main / コミット 5a5c6bc 以降の Day 5.1 v2 ローカル変更含む

---

## 1. このシステムは何か

**Zynect Media Agent** = 広告運用代理店 Zynect Media の自社プロダクト。クライアント企業の広告アカウント（Google Ads / Meta Ads / TikTok Ads / SEO / Fraud 検知）を **米満氏理論ベース** で自動監査し、**営業に使える PDF レポート**を生成するパイプライン。

### 解いている問題
1. 既存の広告監査ツール（他社製）は**専門用語の羅列**で顧客が理解できない
2. **改善提案が抽象的**で「明日から何をやるか」が読めない
3. **想定効果の数値**が出ない、または出ても根拠が雑（過大評価）
4. **米満氏理論**（広告運用の独自哲学）を反映した監査が他社にない

### 提供価値
- 米満氏理論 9 原則（Google）+ 10 原則（Meta）に基づく 220+ ルールでの自動監査
- 業界別ベンチマーク（5 業界 × 3 媒体 × 6 メトリクス）と現状値の **3 軸比較**
- **現実的な改善見込み額**（重複排除 + pixel_health 連動、3 層提示で過大評価回避）
- 実装手順 + 効果発現週数 + 改善確認方法 を併記したアクションプラン

### ユーザー（社内のオペレーター）の使い方
```bash
python pipeline.py run pilotton --report-version v3
# → reports/YYYY-MM-DD/pilotton_report_v3.pdf を生成（顧客提示用）
```

---

## 2. 技術スタック

| カテゴリ | 採用技術 |
|---------|---------|
| 言語 | Python 3.9（venv 使用、`requirements.txt`） |
| YAML | ruamel.yaml（既存形式保持で書き戻し可）+ PyYAML |
| Web API | urllib（標準）/ requests（一部） |
| AI / LLM | Anthropic Claude API（claude-sonnet-4-6 デフォルト、Opus 4.7 プレミアム） |
| テンプレート | Jinja2 |
| PDF 生成 | Playwright (Chromium) — HTML → PDF |
| テスト | pytest |
| 設定 | YAML（`config/` 配下）+ `.env`（dotenv） |

`requirements.txt` の主要依存: `pyyaml / jinja2 / python-dotenv / pydantic / google-ads / playwright / anthropic / apscheduler / pytest / ruff`

---

## 3. ディレクトリ構造（必ず把握すべき場所）

```
bpo-system/
├── pipeline.py                  # 🎯 エントリポイント（CLI: python pipeline.py run <client>）
├── config/                      # 設定ファイル（YAML 中心）
│   ├── clients.yaml             # 各クライアントの広告アカウント・通知・業界
│   ├── thresholds.yaml          # 監査の閾値（CTR下限、ROAS下限、フリークエンシー上限等）
│   ├── benchmarks.yaml          # ⭐ 業界別ベンチマーク（5業界×3媒体×6メトリクス、JPY 直書き）
│   ├── priority_weights.yaml    # ⭐ 優先順位スコア + 6グループ重複排除係数
│   ├── rules/
│   │   ├── google_rules.yaml    # 108 ルール（Google Ads 監査）
│   │   ├── meta_rules.yaml      # 70 ルール（Meta Ads 監査、root_cause_group 付与済）
│   │   ├── tiktok_rules.yaml    # 46 ルール
│   │   ├── common_rules.yaml    # 15 ルール（C01-C15、媒体共通）
│   │   ├── adtruth_rules.yaml   # Fraud 検知ルール
│   │   ├── seo_rules.yaml       # SEO 監査ルール
│   │   └── cross_rules.yaml     # クロス媒体ルール
│   ├── prompts/                 # Claude プロンプトテンプレート
│   └── references/              # 参考データ（ベンチマーク歴史等）
├── adapters/                    # 外部 API ↔ 内部データ変換層
│   ├── google_adapter.py
│   ├── meta_adapter.py          # ⭐ Meta Graph API → 統一 campaigns 配列
│   ├── tiktok_adapter.py
│   ├── csv_adapter.py           # CSV フォールバック
│   └── validator.py             # 取得データの妥当性検証
├── analyzers/                   # 監査ロジック（評価・検出）
│   ├── ads_audit.py             # ⭐ メイン広告監査（platform_summary 構築）
│   ├── anomaly.py               # 異常検知（CPA急騰等）
│   ├── segment_waste.py         # 低効率セグメント検出
│   ├── fraud_audit.py           # AdTruth 不正検知
│   ├── fraud_action.py          # 不正対策アクション
│   ├── fraud_ingest.py          # Fraud データ取り込み
│   └── checks/                  # 媒体別個別チェック関数
├── engine/                      # コアロジック（v3 で大幅拡張）
│   ├── models.py                # ClientConfig 等の Pydantic モデル
│   ├── scorer.py                # ヘルススコア算出
│   ├── conflict_detector.py     # トレードオフ検出（P2 vs P5 等）
│   ├── intent_filter.py         # 意図的設定の警告抑制
│   ├── id_mapper.py             # ルール ID 統一（後方互換）
│   ├── claude_analyzer.py       # v2 用 Claude 分析（旧）
│   ├── rule_coverage.py         # 監査カバレッジ算出
│   ├── yaml_evaluator.py        # YAML ルール評価エンジン
│   ├── report_generator.py      # v2 レポート生成
│   ├── report_generator_v3.py   # ⭐ v3 レポート生成オーケストレータ
│   ├── benchmark_compare.py     # ⭐ v3 業界平均/Zynect推奨/現状の3軸比較
│   ├── impact_estimator.py      # ⭐ v3 想定効果試算（3層: minimum/realistic/independent）
│   ├── priority_ranker.py       # ⭐ v3 Top5 + Critical Alerts ランキング（パターンC）
│   └── claude_insights.py       # ⭐ v3 Claude API 統合（フォールバック付）
├── outputs/                     # 出力層
│   ├── pdf_report.py            # v2 PDF（Playwright）
│   ├── pdf_report_v3.py         # ⭐ v3 PDF
│   ├── slack_notify.py
│   ├── lark_notify.py
│   ├── crm_twenty.py            # Twenty CRM 連携
│   └── json_save.py
├── templates/                   # Jinja2 テンプレート
│   ├── report.html              # v2（単一ファイル）
│   └── v3/                      # ⭐ v3（8ファイル分割）
│       ├── report_v3.html       # マスター（include 集約）
│       ├── _styles.html         # 共通CSS
│       ├── cover.html           # 表紙
│       ├── summary.html         # エグゼクティブサマリ + 3層インパクト
│       ├── actions.html         # Top5 + Critical Alerts + ロードマップ
│       ├── platform.html        # 媒体別詳細
│       ├── timeline.html        # 効果発現タイムライン + 累積折れ線
│       ├── insights.html        # Zynect Insights（独自視点）
│       └── appendix.html        # 用語集 + 原則解説
├── docs/
│   ├── PROJECT_OVERVIEW_FOR_AI.md   # ⭐ 本ファイル
│   ├── decisions/                    # ⭐ ADR（重要設計判断）
│   │   ├── README.md
│   │   ├── ADR-001-three-layer-impact-display.md
│   │   ├── ADR-002-six-root-cause-groups.md
│   │   ├── ADR-003-pixel-health-coupling.md
│   │   └── meta_rules_classification.md  # Meta 70ルール自動分類
│   ├── principles/                   # 米満氏理論ドキュメント
│   │   ├── google_principles.md      # Google 9原則 (P1-P9)
│   │   ├── meta_principles.md        # Meta 10原則 (M-α 〜 M-λ)
│   │   └── meta_rule_mapping.md
│   ├── report_design/                # v3 設計文書 6 本
│   │   ├── v3_problem_analysis.md
│   │   ├── v3_structure.md
│   │   ├── v3_content_strategy.md
│   │   ├── v3_terminology_dict.md    # 60語の翻訳辞書
│   │   ├── v3_client_config_spec.md
│   │   └── v3_priority_score_weights.md
│   ├── release_notes/v3.0.md
│   ├── workflow.md
│   ├── scoring_design.md
│   ├── client_management.md
│   └── onboarding*.md
├── scripts/                     # 運用スクリプト
│   ├── migrate_clients_v3.py    # clients.yaml v2→v3 移行
│   ├── update_benchmarks.py
│   ├── test_meta_connection.py  # Meta API 疎通確認
│   └── ...
├── tests/                       # pytest
│   ├── test_v3_engines.py       # v3 エンジン単体テスト 21 ケース
│   ├── test_intent_filter.py
│   └── ...
├── data/                        # CSV フォールバック / Fraud スナップショット
├── reports/                     # 生成レポート（gitignore）
│   └── YYYY-MM-DD/
│       ├── {client}_report.pdf       # v2
│       ├── {client}_report_v3.pdf    # v3
│       ├── {client}_results.json     # 監査 JSON
│       └── pilotton_brand_breakdown.md
├── logs/                        # ログ（gitignore）
│   ├── YYYY-MM-DD.log
│   ├── llm_audit/{client}/      # Claude API 全リクエスト保存
│   └── llm_cost/                # API コスト記録
├── seo/                         # SEO 監査モジュール
├── integrations/                # 外部連携（Twenty CRM 等）
├── venv/                        # Python virtual env（gitignore）
├── .env                         # シークレット（gitignore、chmod 600）
├── requirements.txt
└── CLAUDE.md                    # AI エージェント向けプロジェクト指示書
```

⭐ = v3 で新設または大幅拡張された主要ファイル

---

## 4. パイプライン実行フロー（pipeline.py）

```
$ python pipeline.py run pilotton --report-version v3
   │
   ├─ load_config() ────────── config/clients.yaml 読込
   ├─ load_thresholds() ────── config/thresholds.yaml 読込
   │
   ├─ run_client(pilotton)
   │   │
   │   ├─ Phase 1: Extract
   │   │   └─ fetch_data() ─── Meta API / Google Ads API / TikTok API（API失敗時 CSV fallback）
   │   │       └─ adapters/{meta,google,tiktok}_adapter.py
   │   │           → 統一形式 {"campaigns": [...], "totals": {...}}
   │   │
   │   ├─ Phase 2: Analyze
   │   │   ├─ run_ads_audit() ──── analyzers/ads_audit.py
   │   │   │   → score, grade, issues, quick_wins, platform_summary
   │   │   ├─ run_anomaly_detection() ── analyzers/anomaly.py
   │   │   ├─ run_waste_detection() ──── analyzers/segment_waste.py
   │   │   ├─ run_fraud_audit() ──────── analyzers/fraud_audit.py
   │   │   ├─ detect_conflicts() ─────── engine/conflict_detector.py
   │   │   ├─ run_claude_analysis() ──── engine/claude_analyzer.py（v2 用）
   │   │   └─ run_seo_audit() ────────── seo/seo_audit.py
   │   │
   │   └─ Phase 3: Report
   │       ├─ Slack/Lark 通知 ───── outputs/slack_notify.py / lark_notify.py
   │       ├─ Twenty CRM 保存 ──── outputs/crm_twenty.py
   │       ├─ v2 PDF 生成 ───────── outputs/pdf_report.py（templates/report.html）
   │       └─ v3 PDF 生成 ───────── outputs/pdf_report_v3.py
   │           └─ engine/report_generator_v3.build_v3_context()
   │               ├─ benchmark_compare.compare_3axis()
   │               ├─ impact_estimator.calculate_minimum/realistic/independent_impact()
   │               ├─ priority_ranker.compute_top_actions()
   │               ├─ priority_ranker.compute_critical_alerts()
   │               ├─ ads_audit.detect_pixel_health()
   │               └─ claude_insights.generate_*()  ← API key 未設定時はフォールバック
   │
   └─ JSON 保存（reports/YYYY-MM-DD/{client}_results.json）
```

### CLI オプション
- `--report-version v2|v3|both`（デフォルト v2、後方互換性）
- `--dry-run`（Day 5.1 で追加、設定検証 + Meta API 疎通のみ）

---

## 5. 主要概念（プロジェクト固有用語）

### 5.1 米満氏理論（Yonemitsu Theory）
広告運用の独自哲学。Zynect の差別化軸。設計者は社外の専門家（米満氏）。

#### Google 9 原則（`docs/principles/google_principles.md`）
| ID | 原則名 | 一言 |
|----|--------|-----|
| P1 | 計測精度=学習シグナル精度 | 計測欠損は AI 学習を全面破壊 |
| P2 | 機械学習保護 | 短期判断で長期最適化を毀損するな |
| P3 | 結果指標非依存 | 品質スコア等の結果指標で停止判断するな |
| P4 | ネガティブシグナル保持 | 低パフォ要素は削除せず除外保持 |
| P5 | Budget Lost 先行解消 | 効率改善より機会損失解消が先 |
| P6 | 集約優先・分離 | 学習単位は集約、評価軸異質は分離 |
| P7 | バリエーション幅最大化 | 短い見出し × 多パターン |
| P8 | 自動化前提判断 | 旧式手動運用の知識を捨てよ |
| P9 | 説明責任・判断ログ | なぜその判断をしたか記録せよ |

#### Meta 10 原則（`docs/principles/meta_principles.md`）
M-α（計測）/ M-β（学習保護）/ M-γ（結果指標非依存）/ M-δ（ネガティブシグナル）/ M-ε（集約）/ M-ζ（CR多様性）/ M-η（Advantage+）/ M-θ（iOS14 計測欠損）/ M-ι（1Pデータ鮮度）/ M-λ（広告-LP整合）

### 5.2 6 グループ root_cause_group（v3.1 で 8 → 6 に再構築、ADR-002）

```yaml
# config/priority_weights.yaml の rule_root_cause セクション
measurement_foundation:          # 計測基盤（Pixel/CAPI/EMQ/AEM）
  duplicate_factor: 0.2          # 重複度極めて高い（CAPI 修復が他全てを底上げ）
delivery_learning_or_structure:  # 配信学習 + 構造（学習脱出/集約/予算分散）
  duplicate_factor: 0.3
creative_optimization:           # CR 最適化（量産/疲弊/Hook）
  duplicate_factor: 0.5
budget_allocation:               # 予算配分
  duplicate_factor: 0.4
targeting:                       # ターゲティング（オーディエンス/1Pデータ）
  duplicate_factor: 0.4
independent:                     # 独立施策（重複なし）
  duplicate_factor: 1.0
```

### 5.3 3 層インパクト表示（ADR-001）
営業時の過大評価を回避するため、改善見込み額を 3 軸で提示:

| 層 | 計算方法 | 用途 |
|----|---------|------|
| **最低値（confident）** | グループ最大値 + 残り × duplicate_factor、pixel 休眠時 measurement factor 0.1 + 非 measurement decay 0.7 | レポート主表示（緑帯、最大文字） |
| **現実値（realistic）** | 最低値と同じ式（pixel decay なし） | 中央表示 |
| **上限値（independent）** | 全件単純合算 | 小表示 + 「重複領域があるため到達困難」注記 |

### 5.4 pixel_health 連動（ADR-003）
クライアントの Meta Pixel が `dormant_days >= 270` または `duplicate_pixel_detected = true` の場合:
- `measurement_foundation` の duplicate_factor を 0.2 → **0.1** に縮小（計測修復が最優先のため重複度を更に高く見積もる）
- 非 measurement_foundation グループに **non_mf_decay = 0.7** を乗じる（計測未整備時の他施策効果減衰）
- レポートに警告ブロック表示

### 5.5 expected_impact フィールド
各ルールに付与（Day 2 で 90 ルール完了、全 224 ルール中）:
```yaml
- id: M02
  name: CAPI実装状況
  ...
  expected_impact:
    primary_metric: cv_count_change_pct
    primary_value: 18      # +18% 改善見込み
    confidence: high
    impact_horizon_weeks: 2
    rationale: "CAPI未実装はiOS14以降の計測欠損を補えずCV数を15〜25%失っている..."
  scenarios:               # Day 5.1 v2 で追加（カスタム振れ幅）
    conservative: 0.6
    realistic: 1.0
    optimistic: 1.4
  confidence_level: high
  implementation_steps:    # Day 5.1 v2 で追加（YAML フィールド化）
    - "Events Manager → 設定 → コンバージョン API のステータスを確認"
    - "未実装の場合: サーバー側で Conversions API SDK を実装、event_id でデデュプ"
    ...
  verification_method: "Events Manager の「コンバージョン API ヘルス」が緑色 / EMQ ≥ 7.0"
  estimated_duration: "実装 3〜5 営業日 / EMQ 改善まで 2〜4 週"
  root_cause_group: measurement_foundation
  priority_in_group: 3
  classification_confidence: 0.95
  classification_rationale: "Day 5.1 Task F-1 で人手分類確定"
  needs_review: false
```

### 5.6 業界別ベンチマーク（`config/benchmarks.yaml`）
6 業界（ec_retail / **beauty_d2c** / saas_b2b / finance / education / local_service）× 3 媒体 × 6 メトリクス（CTR/CPC/CPA/CVR/ROAS/Frequency）。**全て JPY 直書き**（v3.1 v2 で USD 自動換算ロジック削除、ADR 内に経緯記載）。

### 5.7 priority_ranker のパターン C（`docs/report_design/v3_priority_score_weights.md`）
Top5 ランキング式（クイックウィン優先）:
```
priority_score = (severity × impact × confidence) / sqrt(effort_hours) × quick_win_bonus
```
パラメータ A/B/C の 3 案あり、現在は **C（effort 重視）** 採用。

---

## 6. v2 と v3 の違い

| 項目 | v2 | v3 |
|------|----|----|
| ページ数 | 1〜3 | 9（表紙/サマリ/Top5/媒体×3/タイムライン/Insights/付録） |
| テンプレート | `templates/report.html`（単一） | `templates/v3/*.html`（8 分割） |
| 業界比較 | なし | 業界平均 / Zynect 推奨 / 現状の3軸比較 |
| 想定効果 | なし | 3層インパクト + 信頼度 + 効果発現週数 |
| Claude API | 補助分析 | 顧客語翻訳 / ナラティブ / Zynect Insights（フォールバック付） |
| 重み付け | 既存 scorer | `priority_weights.yaml` 外出し |
| 業界別ベンチマーク | なし | `benchmarks.yaml`（6業界×3媒体） |
| 並行運用 | デフォルト | `--report-version v3` で明示指定（Day 6 以降切替予定） |

---

## 7. データソース

### Meta Graph API（最重要）
- バージョン: v22.0
- アカウント単位: `act_{ad_account_id}`
- 主要エンドポイント:
  - `/{account}/campaigns` — キャンペーン基本情報
  - `/{account}/insights?level=adset` — 直近 30/90 日の支出/CV/CTR等（**`level=adset` 注意**: アンダースコアなしが正）
  - `/{account}/adspixels` — Pixel 一覧 + 最終発火日時
- トークン: System User Token（`META_ACCESS_TOKEN_*` を `.env` に格納、`access_token_env` で参照）
- レート制限: 標準 200 calls/hour/user、ビジネス Manager 経由で緩和

### CV カウントの落とし穴（v3.1 v2 で修正）⚠️
Meta API は **同じ 1 件の購入を 9 種類のラベルで重複報告**:
- `purchase`（unified counter、これだけ使う）
- `omni_purchase`、`offsite_conversion.fb_pixel_purchase`、`onsite_web_app_purchase`、`onsite_web_purchase`、`web_in_store_purchase`、`web_app_in_store_purchase`、`offsite_purchase_add_20_s_calls` 等
→ `adapters/meta_adapter.py` の `UNIFIED_CV_TYPES = ("purchase", "lead", "complete_registration")` 定義を必ず守ること。これらに `offsite_conversion.fb_pixel_*` を加えると **CV を 2 倍以上に過大カウント**する。

### CSV フォールバック
API 取得失敗時は `data/{client_id}*.csv` を読む（`adapters/csv_adapter.py`）。yamamoto_demo はデモ目的で CSV 専用。

---

## 8. 設定ファイル（`config/clients.yaml`）スキーマ

```yaml
defaults:
  timezone: Asia/Tokyo
  currency: JPY
  schedule: "0 9 * * *"
  anthropic_model: claude-sonnet-4-6
  anthropic_model_premium: claude-opus-4-7

clients:
  pilotton:
    # === v3 ブロック ===
    company:
      name: 株式会社パイロットン
      honorific: 御中
      industry: beauty_d2c        # benchmarks.yaml のキーと一致
      industry_label: 美容・D2C
    contact:
      name: ...
      honorific: 様
      title: ...
    report:
      display_name: 広告アカウント健康診断レポート
      include_zynect_insights: true
      report_period_days: 30

    # === 既存スキーマ（v2 後方互換） ===
    name: 株式会社パイロットン
    active: true
    objective: balanced  # cpa_minimize | cv_maximize | roas_target | balanced
    ads:
      meta:
        enabled: true
        account_id: act_566972639374407
        access_token_env: META_ACCESS_TOKEN_PILOTTON
        lookback_days: 30
        pixels:                     # Day 5.1 で詳細フィールド化
          - id: '1690318315033477'
            name: MYNAILPLEX_LP01
            duplicate: false
            dormant_days: 0
          - id: '1678979499646915'
            name: CLOCKING Pixel
            duplicate: true          # ← detect_pixel_health() が読む
            dormant_days: 270
      google: { enabled: false }
      tiktok: { enabled: false }
    notifications:
      platform: lark
      lark:
        webhook_env: LARK_WEBHOOK_PILOTTON
    intent_overrides: []  # 警告抑制（試験期間中の意図的設定）
```

---

## 9. .env（シークレット）

```
META_ACCESS_TOKEN_BANDAL=EAA...   # Meta System User Token（実値）
META_ACCESS_TOKEN_PILOTTON=EAA...
ANTHROPIC_API_KEY=                # 未設定時はフォールバック生成
PAGESPEED_API_KEY=
LARK_WEBHOOK_PILOTTON=
TWENTY_API_URL=
TWENTY_API_KEY=
```

`chmod 600 .env`、`.gitignore` 登録済み。

---

## 10. 重要な設計判断（ADR）

`docs/decisions/` に保管。新規エージェントは必ず一読:

| ADR | タイトル | 要点 |
|-----|---------|------|
| ADR-001 | 想定改善額の3層表示（パターンC）採用 | 過大評価回避、最低値主表示 |
| ADR-002 | 6 グループ root_cause_group 分類設計 | 8→6 グループへ再編、Meta 70 ルール自動分類 |
| ADR-003 | pixel_health 連動ロジック設計 | 休眠時 measurement factor 0.2→0.1、非 measurement に decay 0.7 |
| ADR-004 | CV カウント正規化と conversion_mapping.yaml 外部化 | Meta API CV 二重計上を YAML ベースで防止、Pydantic 検証 |
| ADR-005 | ChatWork 経由の指摘・完了・月次運用ループ | 1 ルーム運用、改善手順は本文展開 (外部リンク非依存)、3 日連続 clean で完了確定 |

---

## 10.1 ChatWork 運用ループ (ADR-005, Day 1-3 実装)

### 全体像

```
analyzer 結果 → indication_detector → IndicationState (state/...) → indication_filter
              → daily_chatwork_check.py (09:00 JST 日次) → ChatWork rid 435851481
                                                          → completion_notice (3日連続クリーン)
                                                          → daily_indication (新規指摘)
              → monthly_chatwork_report.py (1日 10:00 JST) → ChatWork + v3 PDF 添付
```

### 主要モジュール

| ファイル | 役割 |
|---------|------|
| `notifiers/chatwork_notifier.py` | ChatWork API v2 クライアント (テキスト/添付/idempotency/retry) |
| `templates/chatwork/_action_steps.md.j2` | rule_id 別の改善手順マクロ (5 主要 + フォールバック) |
| `templates/chatwork/daily_indication.md.j2` | 日次指摘テンプレート (事実/影響/手順) |
| `templates/chatwork/completion_notice.md.j2` | 解消通知テンプレート (before/after + 効果) |
| `templates/chatwork/monthly_report.md.j2` | 月次レポートテンプレート |
| `engine/indication_state.py` | 指摘状態 DB (open → resolved_pending → resolved_confirmed) |
| `engine/indication_filter.py` | severity / 日次cap / cooldown フィルタ |
| `engine/indication_detector.py` | analyzer 出力 → 統一 indication 形式 |
| `engine/monthly_aggregator.py` | 月次サマリ集計 |
| `scripts/daily_chatwork_check.py` | 日次ジョブエントリポイント |
| `scripts/monthly_chatwork_report.py` | 月次ジョブエントリポイント |
| `state/chatwork_sent.json` | idempotency ストア (送信済みハッシュ) |
| `outputs/chatwork_state/{client}_indications.json` | クライアント別指摘 DB |
| `outputs/chatwork_state/{client}_indications.archive/{YYYY-MM}.json` | 月次アーカイブ |

### 設計上の重要原則

1. **改善手順は ChatWork 本文内で完結** — `docs/guides/`, Notion, Drive 等の外部資料は使わない
2. **rule_id 安定 ID**: `{client}:{rule}:{platform}:{target}:{first_detected_date}` で再発を別 ID 化
3. **3 日連続クリーン + 一時欠損ガード** で誤検知率 < 5% を担保
4. **1 ルーム運用 (rid 435851481)**: 内部レビュー期間は `[テスト]` プレフィクス、本番化は kickoff day
5. **idempotency**: 同じ本文/ファイルは sha256 ハッシュで重複送信を防止
6. **自己監視**: 致命的失敗時に ChatWork へ critical 投稿 (1 日 1 回まで)

### 起動

`docs/operations/chatwork_scheduler_setup.md` 参照 (launchd 推奨、cron も併記)

---

## 11. 既知の制約・注意点

| 観点 | 内容 |
|------|------|
| Anthropic API 未設定 | `ANTHROPIC_API_KEY` 空 → 全て `_fallback_*()` 関数で生成、レポート品質は低下するが PDF は出る |
| CV 二重計上バグ | 過去レポートは `adapters/meta_adapter.py.bak.before_cv_dedup.20260502-143351` 修正前の値で生成されており **約 2 倍過大**。修正後の最新版が正 |
| TikTok ベンチマーク | finance / local_service 等 18 セルが null（公式 APAC レポート未取得） |
| `mobile_app` 業界 | benchmarks.yaml に未登録、bandal_gaming は `ec_retail` フォールバック |
| Health Score 業界平均 | 全 6 業界で null（Zynect 内部運用アカウントから 2026-Q3 集計予定） |
| pdf 大きさ | v3 約 2.4MB（システムフォント採用後）。これ以上の軽量化は Playwright 内蔵分の影響でフォントサブセット化が必要 |
| 後方互換性 | v2 (`templates/report.html`, `outputs/pdf_report.py`) は **絶対変更しない**。並行運用期間中の保険 |
| pipeline 実行ディレクトリ | `bpo-system/` でなく `bpo-system/bpo-system/` がプロジェクトルート（二層構造）。`pipeline.py` は後者で実行 |

---

## 12. テスト

```bash
python -m pytest tests/ -v              # 全テスト
python -m pytest tests/test_v3_engines.py -v  # v3 エンジンのみ（21 ケース）
```

`tests/test_v3_engines.py` カバー範囲:
- `benchmark_compare.compare_3axis()` 6 ケース（正常 / 境界 / 異常）
- `impact_estimator.estimate_for_rule()` 6 ケース（scenario / dedup 含む）
- `priority_ranker.compute_top_actions()` 5 ケース
- 3 関数（minimum / realistic / independent）3 ケース
- beauty_d2c 業界 1 ケース

---

## 13. 直近のクライアント運用状況

### 登録クライアント（`config/clients.yaml`）
| client_id | 企業 | 業界 | 状態 | 用途 |
|-----------|------|------|------|------|
| **pilotton** | 株式会社パイロットン | beauty_d2c | active | **PoC 第1号、MYNAILPLEX 単独運用中** |
| **bandal_gaming** | 株式会社バンダルゲーミング | ec_retail | active | 配信実績ゼロ、検証用 |
| **yamamoto_demo** | 山本テクノロジー株式会社 | ec_retail | active | CSV ベース、デモ用 |

### pilotton の現状（直近 30 日、CV 修正後）
- 月次支出: ¥1,446,455
- 月次 CV: 161 件
- CPA: ¥8,984（業界平均 ¥4,500 の約 2 倍、改善余地大）
- CTR: 2.37%（業界平均 1.80% 超え）
- ROAS: 0.00 倍（**Conversion Value 未送信**、計測不能 — PoC 提案の最重要施策）

### Pixel 健全性（pilotton）
| Pixel | 状態 |
|-------|------|
| MYNAILPLEX_LP01 | 🟢 アクティブ |
| アゲルキャリア_Pixel | 🟢 アクティブ（ただし直近 30 日配信ゼロ、別アカウント運用？要確認） |
| CLOOKING Pixel | 🔴 休眠 270 日 + 重複疑い |
| CLOOKING_ピクセル | 🔴 休眠 366 日 + 重複疑い |
| 削除 | 🔴 廃止予定 |

→ pixel_health 連動発動中（measurement_foundation factor 0.1 + non_mf_decay 0.7 適用）。

---

## 14. 次の AI が引き継ぐ典型タスク（参考）

| タスク種別 | 関連ファイル / 関数 |
|----------|-------------------|
| 新規クライアント追加 | `config/clients.yaml` + `.env` + `scripts/test_meta_connection.py` |
| 業界ベンチマーク更新 | `config/benchmarks.yaml`（出典明記、`※ 要確認`コメント保持） |
| 新ルール追加 | `config/rules/meta_rules.yaml` 等に `expected_impact` 含む全フィールド付与 |
| 重複排除係数調整 | `config/priority_weights.yaml` の `duplicate_factors` |
| Claude API プロンプト調整 | `engine/claude_insights.py` の `_invoke()` |
| v3 テンプレート編集 | `templates/v3/*.html` + `templates/v3/_styles.html` |
| v2 動作維持確認 | `python pipeline.py run yamamoto_demo --report-version v2` で smoke test |

---

## 15. 「これだけ読めば動かせる」3 行サマリ

1. **入口**: `python pipeline.py run <client_id> --report-version v3` を実行する。`config/clients.yaml` で `<client_id>` を確認、`.env` で API トークン確認。
2. **コア**: `engine/report_generator_v3.py:build_v3_context()` がレポートデータを構築する。ここを起点に benchmark_compare / impact_estimator / priority_ranker / claude_insights を読めば全体把握できる。
3. **判断記録**: 「なぜそう実装したか」は `docs/decisions/ADR-*.md` に集約。新規変更時は ADR 追加を検討。

---

*最終更新: 2026-05-03 / バージョン: v3.1（Day 5.1 v2 + CV 二重計上修正版）*
