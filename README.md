# BPO System (Zynect Media Agent)

> 広告運用代理店 **Zynect Media** が、クライアントの広告アカウントを米満氏理論ベースで自動監査し、PDF レポート + ChatWork 経由の日次・月次運用ループまで自動化する **BPO プラットフォーム**。

---

## 1. このサービスは何か

広告運用代理店が「監査 → レポート → 提案 → 運用通知 → 完了確認 → 月次総括」を **30 クライアント規模でも破綻しない自動化** を実現する。

ただ機械的にメトリクスを並べるだけのツールではなく、米満氏理論 (Hagakure / 不知火 / Unlocking) に根ざした **9 + 10 = 19 原則** で「なぜそれが問題か」「明日から何をすべきか」「効果はどれくらいか」までを構造化して出力する。

### 提供価値

| 既存ツールの問題 | 本サービスの提供 |
|------------------|------------------|
| 専門用語の羅列で顧客が理解できない | 米満氏理論ベースの「目的 + ゴール状態 + 選択肢」記述 |
| 改善提案が抽象的で「明日から何を」が分からない | 5 主要 rule_id に詳細手順を本文展開 (ChatWork で完結) |
| 想定効果が出ない、または出ても根拠が雑 (過大評価) | 3 層インパクト表示 (確実値 / 現実値 / 上限値) で過大評価回避 |
| 米満氏理論を反映した監査が他社にない | Google 9 原則 + Meta 10 原則 + 11 トレードオフ軸を全 277 ルールに紐付け |
| 改善が完了したか BPO 側が能動確認するコストが高い | ChatWork で 3 日連続クリーン → 自動完了通知 |
| 月次効果額の合算がない | 月次レポート + PDF 添付で営業面・契約継続を支援 |

### 対象ユーザ

- **Zynect Media (内部)**: 広告運用オペレーター、提案担当
- **クライアント企業**: 月額広告費 ¥50万 〜 ¥1,500万 規模の D2C / EC / 受託サービス業
- **Phase A の最初の本番クライアント**: パイロットン (株式会社パイロットン、beauty_d2c)

---

## 2. Phase A 実装済み機能 (2026-05-03 時点)

### 監査・分析

- **5 媒体・277 ルール (有効化済み)**: Google Ads 108 + Meta 70 + TikTok 46 + SEO 45 + AdTruth 15 + Common 15
- **業界別ベンチマーク**: 5 業界 × 3 媒体 × 6 メトリクス、現状値との 3 軸比較
- **YAML ベースの宣言的ルール定義**: severity / category / polarity / 11 軸トレードオフ / root_cause_group の多次元タグ
- **CV 正規化**: ADR-004 で Meta API の二重計上を `conversion_mapping.yaml` で防止
- **Pixel 健全性連動**: ADR-003 で休眠 Pixel 検知時に measurement_foundation 系の重み係数を動的調整
- **Claude API 定性分析**: claude-sonnet-4-6 + claude-opus-4-7 (Premium Insights)。フォールバックあり、API 未設定でも PDF は出力される

### レポート出力

- **v3 PDF レポート (8 ページ構成)**: 表紙 / サマリ / Top5 アクション / 媒体×3 / Insights / 付録
- **3 層インパクト表示** (ADR-001): 確実値 (minimum) / 現実値 (realistic) / 上限値 (independent) を併記、過大評価を避ける
- **6 root_cause_group 重複排除** (ADR-002): measurement / delivery / creative / budget / targeting / independent
- **顧客向け pptx 提案資料**: 13 ページ構成、SSoT (Single Source of Truth) 強制 + ADR トレーサビリティ

### ChatWork 運用ループ (ADR-005)

- **日次指摘通知** (毎朝 09:00 JST、launchd 自動起動): 検知 → 統一 indication 形式 → severity フィルタ + 日次 cap 3 件 + cooldown 7 日 → ChatWork 投稿
- **解消通知** (3 日連続クリーン検知): before/after の対比 + 達成効果 (3 層) を併記
- **月次レポート** (毎月 1 日 10:00): 期間サマリ + v3 PDF 添付
- **改善手順は ChatWork 本文に完結**: 外部 (Notion / GitHub / Drive) 依存ゼロ、Jinja2 マクロで rule_id 別に展開
- **3 層知識陳腐化対策**:
  - 層 1: 「目的 + ゴール状態 + 選択肢」の抽象度高い記述
  - 層 2: 全指摘末尾に「YYYY/MM 時点の情報、画面異なれば生成 AI に聞いて」の免責文を自動挿入
  - 層 3: 半年に 1 回 WebSearch で公式情報を再反映 (次回 2026-11)

### スケジューラ・運用

- **APScheduler 8 ジョブ**: 週次フル監査 / 日次 Fraud / 月次ベンチ / 判断エスカ / 学習レビュー / **日次 ChatWork** / **月次 ChatWork**
- **launchd 統合 (macOS)**: `scripts/launchd/com.zynect.bpo.daily-chatwork.plist` で常駐起動
- **Idempotency**: ChatWork 投稿は (room_id, body) sha256 で重複送信防止
- **自己監視**: 致命的失敗時に ChatWork へ critical 投稿 (1 日 1 回まで)

### 安全設計

- **TOKEN ローテーション支援**: `scripts/setup/write_chatwork_token.sh` で重複貼付検出 (Cmd+V 2 回押し対策)、長さチェック、cat -e 行末確認、自動バックアップ
- **環境変数の遅延読込**: API トークン未設定でも import エラーにならない (`load_dotenv` + `_resolve_token`)
- **dry_run モード**: ChatWork API を呼ばずにペイロードだけ生成、テスト用

---

## 3. テスト・品質

| カテゴリ | 件数 |
|---------|------|
| ルール schema 検証 (severity / polarity / axis_position / prerequisite) | 32 件 |
| ChatWork notifier (mock) | 7 件 |
| ChatWork テンプレート (5 主要 rule_id 別 + 免責文) | 24 件 |
| Indication 状態管理 (state 遷移 / cooldown / 一時欠損ガード) | 14 件 |
| v3 エンジン (impact / priority_ranker / 3 層算出) | 50+ 件 |
| 統合テスト (pipeline / CRM / Slack) | 80+ 件 |
| **合計** | **340 件 PASS** |

E2E 検証済:
- ChatWork スモークテスト (text + 解消通知 + PDF 添付、idempotency 動作確認)
- launchctl 経由の launchd 起動テスト (exit code 0、message_id 取得)
- python3 直接実行テスト (Meta API 連携 → 22 件検知 → cap 3 件投稿)

---

## 4. Phase B / C ロードマップ (Draft)

### Phase B (5/14 〜 5/28、2 週間想定)

| 週 | 主要タスク | 関連 ADR |
|---|----------|---------|
| W1 (5/14-5/17) | 内部レビュー終了 / pilotton kickoff / **ops_alert** 実装 (社内 Slack 障害通知) | ADR-008 候補 |
| W1-W2 | rule_id 名整合性修正 (M01 vs ChatWork alias、F-rule YAML vs 実装) | — |
| W2 (5/20-5/24) | **トレードオフ設計実装** (3 段階アクション + 偽陽性コスト + 都度学習) | ADR-009 候補 |
| W2-W3 | **AdTruth (LP タグ型不正検知)** 選択肢調査 → ADR 化 → MVP 実装 | ADR-006 候補 |
| W3 (5/27-5/28) | 月次レポート初回送信 (ChatWork + PDF 添付)、Phase A 振り返り | — |

### Phase C (6 月以降、計画段階)

- 複数クライアント並列対応 (clients.yaml スケール、Business プラン移行)
- 顧客返信の NLP 自動応答 (Claude API)
- ChatWork → Slack ブリッジ (内部メンバ多人数化時)

---

## 5. アーキテクチャ概要

### パイプラインフロー

```
pipeline.py run <client>
  ├── 1. データ取得    (adapters/{platform}_adapter.py)
  │     └─ Meta API / Google Ads API / TikTok API / CSV フォールバック
  ├── 2. データ検証    (adapters/validator.py)
  ├── 3. 監査          (analyzers/ads_audit.py + checks/{platform}/)
  │     └─ YAML ルール 277 件評価 → issues 配列
  ├── 4. 異常検知      (analyzers/anomaly.py)
  ├── 5. 不正検知      (analyzers/fraud_audit.py、F01-F15)
  ├── 6. SEO 監査     (seo/seo_audit.py、site_url 設定時)
  ├── 7. Claude 分析   (engine/claude_analyzer.py、API 設定時)
  ├── 8. 競合解決      (engine/conflict_detector.py、トレードオフ軸ベース)
  ├── 9. レポート生成  (engine/report_generator_v3.py)
  │     └─ 3 層インパクト + 業界比較 + Top5 + 6 グループ重複排除
  └── 10. 出力        (outputs/pdf_report_v3.py / Slack / CRM)
```

### ChatWork 運用ループ (ADR-005)

```
[毎朝 09:00 launchd]
  → daily_chatwork_check.py
    → analyzer 結果 → indication_detector → IndicationState
    → indication_filter (severity / cap / cooldown)
    → ChatWork 投稿 (rid 435851481)
    → completion_notice (3 日連続クリーン検知時)

[毎月 1 日 10:00]
  → monthly_chatwork_report.py
    → 月次集計 (engine/monthly_aggregator.py)
    → ChatWork 本文投稿 + v3 PDF 添付 (Free プラン 3MB / 5MB 警告)
    → resolved_confirmed → archived 移行
```

---

## 6. 主要概念

### 米満氏理論

- **Google 9 原則** (Hagakure / 不知火 / Unlocking 系列): 構造の粒度 / 学習シグナル / 入札次元 / 評価対象 / KW 運用 / クリエイティブ-LP / クリエイティブ管理 / 広告フォーマット / IS Lost 構造
- **Meta 10 原則** (M-α 〜 M-λ): 計測基盤 / 配信学習 / クリエイティブ / 予算配分 / ターゲティング / etc

### 11 トレードオフ軸 (TO-01〜TO-11)

| ID | 軸名 | 左極 ↔ 右極 |
|----|------|-------------|
| TO-01 | 構造の粒度 | 細分化 ↔ 集約 |
| TO-02 | 学習シグナル | ポジティブ強化 ↔ ネガティブ保持 |
| TO-03 | 入札次元 | 頻度の幅 ↔ 深度の強さ |
| TO-04 | 評価対象 | 品質スコア(結果) ↔ Ad Rank(原因) |
| TO-05 | KW 運用 | KW 追加で可視化 ↔ 学習データ集約 |
| TO-06 | クリエイティブ-LP | 広告統一 ↔ LP 個別最適 |
| TO-07 | クリエイティブ管理 | 負け止め ↔ 学習継続 |
| TO-08 | 広告フォーマット | 訴求網羅 ↔ バリエーション幅 |
| TO-09 | IS Lost 構造 | Budget 最適化 ↔ Ad Rank 最適化 |
| TO-10 | 時間軸 | 短期効率 ↔ 長期学習 |
| TO-11 | コントロール権 | 人的最適化 ↔ システム自動化 |

### 6 root_cause_group + duplicate_factor

```
measurement_foundation       0.2 (休眠時 0.1)  計測基盤 (CAPI/Pixel/Domain)
delivery_learning_or_structure 0.3            配信学習・構造
creative_optimization        0.5              クリエイティブ最適化
budget_allocation           0.4              予算配分
targeting                   0.4              ターゲティング
independent                 1.0              独立施策
```

### 3 層インパクト

| 層 | 内容 | 用途 |
|----|------|------|
| minimum (確実値) | 各 root_cause_group 内で最大 1 件採用 + duplicate_factor + pixel_health 連動 | 顧客提示時の保守的試算 |
| realistic (現実値) | duplicate_factor のみ (pixel_health 連動なし) | 標準試算 |
| independent (上限値) | 重複排除なし、各施策が独立に最大効果を発揮した想定 | 理論天井 (到達困難) |

### ChatWork 都度学習 (Phase B、ADR-009 候補)

- 検知時に「block / monitor / investigate どれを選びますか?」を ChatWork で質問
- 回答を `outputs/client_preferences/{client}.yaml` に蓄積
- 10-20 件で TO-XX 軸ごとの選好を回帰推定
- `engine/scorer.py` の重み (customer_preference_multiplier) に動的反映

---

## 7. 技術スタック

| カテゴリ | 採用技術 |
|---------|---------|
| 言語 | Python 3.9+ (3.12 推奨、venv) |
| YAML | pyyaml + ruamel.yaml |
| Web API | urllib (標準) / requests |
| AI / LLM | Anthropic Claude API (sonnet-4-6 / opus-4-7) |
| テンプレート | Jinja2 (StrictUndefined、未定義変数を早期検出) |
| PDF 生成 | Playwright (Chromium) |
| pptx 生成 | python-pptx |
| バリデーション | Pydantic v2 |
| テスト | pytest + ruff |
| スケジューラ | APScheduler + macOS launchd |
| 設定 | YAML (`config/`) + `.env` (python-dotenv) |
| 通知 | ChatWork API v2 / Slack Webhook (Block Kit) |

---

## 8. セットアップ

```bash
# 1. 仮想環境
python3 -m venv venv
source venv/bin/activate

# 2. 依存関係
pip install -r requirements.txt

# 3. 環境変数
cp .env.example .env
# .env を編集 (META_ACCESS_TOKEN_*, CHATWORK_API_TOKEN, ANTHROPIC_API_KEY 等)

# 4. Playwright (PDF/SEO 用)
playwright install chromium

# 5. 動作確認
venv/bin/python3 -m pytest tests/ --ignore=tests/test_pptx_generation.py
# → 340 passed in ~14s

# 6. パイロットン監査実行
venv/bin/python3 pipeline.py run pilotton --report-version v3
# → reports/YYYY-MM-DD/pilotton_report_v3.pdf
```

### ChatWork トークン安全書込 (推奨)

```bash
bash scripts/setup/write_chatwork_token.sh
# → 重複貼付検出付きの安全プロンプト (Cmd+V 2 回押し対策)
```

### launchd 自動起動 (macOS、毎朝 09:00 自動実行)

詳細: [`docs/operations/launchd_setup.md`](./docs/operations/launchd_setup.md)

```bash
mkdir -p logs
cp scripts/launchd/com.zynect.bpo.daily-chatwork.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
launchctl list | grep zynect    # → 登録確認
```

---

## 9. ディレクトリ構造 (主要のみ)

```
bpo-system/
├── pipeline.py                  # 🎯 エントリポイント
├── config/
│   ├── clients.yaml             # クライアント設定 (pilotton / yamamoto_demo / bandal_gaming)
│   ├── thresholds.yaml          # 監査閾値
│   ├── benchmarks.yaml          # 業界別ベンチマーク (5 業界 × 3 媒体 × 6 メトリクス)
│   ├── priority_weights.yaml    # 優先度スコア + 6 グループ重複排除係数
│   ├── conversion_mapping.yaml  # CV 正規化 (ADR-004)
│   └── rules/
│       ├── google_rules.yaml    # 108 ルール
│       ├── meta_rules.yaml      # 70 ルール
│       ├── tiktok_rules.yaml    # 46 ルール
│       ├── seo_rules.yaml       # 45 ルール
│       ├── adtruth_rules.yaml   # 15 ルール (F01-F15)
│       ├── common_rules.yaml    # 15 ルール (C01-C15)
│       └── tradeoff_axes.yaml   # 11 軸 (TO-01 〜 TO-11)
├── adapters/                    # 外部 API 連携
├── analyzers/                   # 監査ロジック (ads_audit / anomaly / fraud_audit)
├── engine/
│   ├── impact_estimator.py      # 3 層インパクト計算 (ADR-001)
│   ├── priority_ranker.py       # Top5 ランキング
│   ├── report_generator_v3.py   # v3 PDF 生成
│   ├── claude_insights.py       # Claude Premium Insights
│   ├── indication_state.py      # ChatWork 指摘状態 DB (ADR-005)
│   ├── indication_filter.py     # severity / cap / cooldown (ADR-005)
│   ├── indication_detector.py   # analyzer 結果 → 統一 indication
│   └── monthly_aggregator.py    # 月次レポート集計
├── notifiers/
│   └── chatwork_notifier.py     # ChatWork API クライアント (text + file 添付)
├── templates/
│   ├── v3/                      # v3 PDF テンプレート (Jinja2)
│   └── chatwork/
│       ├── daily_indication.md.j2
│       ├── completion_notice.md.j2
│       ├── monthly_report.md.j2
│       ├── _action_steps.md.j2  # rule_id 別改善手順マクロ (5 主要 + フォールバック)
│       └── _disclaimer_ai_assist.md.j2  # 共通免責文 (層 2)
├── scripts/
│   ├── daily_chatwork_check.py
│   ├── monthly_chatwork_report.py
│   ├── chatwork_smoke_test.py
│   ├── chatwork_rule_sample_post.py
│   ├── launchd/                 # launchd plist マスター
│   └── setup/                   # 安全な秘密情報書込スクリプト
├── outputs/
│   ├── pdf_report_v3.py         # v3 PDF 出力
│   ├── chatwork_state/          # ChatWork 指摘状態 (clientごとに JSON)
│   ├── pilotton/                # pilotton 用効果額算出 etc
│   └── slack_notify.py          # Slack Block Kit 通知
├── integrations/
│   └── scheduler.py             # APScheduler (8 ジョブ統合)
├── tests/                       # 340 件 PASS
├── docs/
│   ├── decisions/               # ADR-001 〜 ADR-005 (5 件 Accepted)
│   ├── architecture/            # tradeoff_design.md (ADR-009 候補)
│   ├── operations/              # launchd_setup / chatwork_scheduler_setup
│   ├── proposals/pilotton/      # pilotton 専用提案資料
│   ├── principles/              # 米満氏理論まとめ (Google 9 + Meta 10)
│   ├── report_design/           # v3 設計文書 (6 ファイル)
│   ├── PROJECT_OVERVIEW_FOR_AI.md  # 引継ぎ用詳細ドキュメント (571 行)
│   └── scoring_design.md        # 3 層重み構造の設計
└── state/                       # idempotency ストア (ChatWork 送信ハッシュ)
```

---

## 10. 主要コマンド

```bash
# 全クライアント監査 (v2 のみ)
venv/bin/python3 pipeline.py run all

# 特定クライアント (v3 PDF 出力)
venv/bin/python3 pipeline.py run pilotton --report-version v3

# v2 + v3 並行生成 (移行期間用)
venv/bin/python3 pipeline.py run pilotton --report-version both

# ChatWork 日次チェック (手動、dry-run)
venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton --dry-run --prefix "[テスト] "

# ChatWork 月次レポート (前月集計)
venv/bin/python3 scripts/monthly_chatwork_report.py --client pilotton --period 2026-04

# テスト
venv/bin/python3 -m pytest tests/ --ignore=tests/test_pptx_generation.py
```

---

## 11. 関連ドキュメント

### Architecture Decision Records (ADR)

| ID | タイトル | Status |
|----|---------|--------|
| [ADR-001](./docs/decisions/ADR-001-three-layer-impact-display.md) | 想定改善額の 3 層表示 (パターン C) | Accepted |
| [ADR-002](./docs/decisions/ADR-002-six-root-cause-groups.md) | 6 root_cause_group 分類設計 | Accepted |
| [ADR-003](./docs/decisions/ADR-003-pixel-health-coupling.md) | pixel_health 連動ロジック | Accepted |
| [ADR-004](./docs/decisions/ADR-004-cv-normalization-and-conversion-mapping.md) | CV 正規化と conversion_mapping.yaml 外部化 | Accepted |
| [ADR-005](./docs/decisions/ADR-005-chatwork-indication-completion-monthly-loop.md) | ChatWork 経由の指摘・完了・月次運用ループ | Accepted |
| ADR-006 (候補) | LP タグ型不正検知の選択方針 | Draft |
| ADR-008 (候補) | ops_alert 社内 Slack 通知 | Draft |
| [ADR-009 (候補)](./docs/architecture/tradeoff_design.md) | トレードオフ設計 + 顧客選好学習 | Draft |

### 設計文書

- [PROJECT_OVERVIEW_FOR_AI.md](./docs/PROJECT_OVERVIEW_FOR_AI.md) — 引継ぎ用詳細解説 (571 行)
- [scoring_design.md](./docs/scoring_design.md) — 3 層重み構造の設計
- [report_design/](./docs/report_design/) — v3 PDF 設計 6 ファイル
- [principles/](./docs/principles/) — 米満氏理論まとめ

### 運用ドキュメント

- [launchd_setup.md](./docs/operations/launchd_setup.md) — launchd 登録手順 (macOS)
- [chatwork_scheduler_setup.md](./docs/operations/chatwork_scheduler_setup.md) — スケジューラ詳細
- [client_management.md](./docs/client_management.md) — クライアント追加手順
- [onboarding.md](./docs/onboarding.md) — 新規クライアント受入

### 提案資料

- [docs/proposals/pilotton/effective_impact_summary.md](./docs/proposals/pilotton/effective_impact_summary.md) — pilotton 効果額算出 (¥29,505 〜 ¥105,521 / 月)

---

## 12. 開発フロー

### コード変更時

1. 該当ルール / 設定の YAML を編集
2. `pytest tests/ -v` で全件 PASS 確認
3. v3 レポート生成で動作確認 (`pipeline.py run <client> --report-version v3`)
4. 重要な設計判断は ADR を新規起案 (`docs/decisions/ADR-NNN-...md`)

### 新規クライアント追加時

1. `config/clients.yaml` にエントリ追加
2. `.env` に `<SERVICE>_<KEY>_<CLIENT_ID>` 形式で API トークン追加
3. `pipeline.py test` で設定チェック
4. 初回 v3 PDF 生成 + ChatWork ルーム ID 設定

### Phase B / C で予定する破壊的変更

- AdTruth (LP タグ型) 導入時に同意管理レイヤー新設 (Phase B Week 3-4)
- 複数クライアント並列実行時に scheduler の job 数増加 (Phase B Week 2)

---

## ライセンス

社内利用のみ (Zynect Media 内部プロダクト)。

---

## Contact

- Maintainer: 山本 (Zynect Media)
- 開発支援: Claude Code (Anthropic)
