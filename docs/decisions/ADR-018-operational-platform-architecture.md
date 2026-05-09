# ADR-018: MVP から運用プラットフォームへの進化

- **Status**: Proposed
- **Date**: 2026-05-09
- **Related**: ADR-005, ADR-011, ADR-012, ADR-013, ADR-016, ADR-017

## 1. Context

Phase A MVP では、pilotton 1 社に対して ChatWork 日次通知、回答取り込み、指摘解消通知、月次レポートの一連の運用が成立した。

一方で、現在の構成は「1 社を人間が見ながら回す」には十分だが、10〜20 社以上、複数媒体、複数担当者、複数ルール層に拡張すると破綻しやすい。

主な課題:

1. **状態が YAML / JSON ファイルに散在**
   - `outputs/chatwork_state/*`
   - `outputs/chatwork_responses/*`
   - `outputs/auto_proposal_history/*`
   - `outputs/client_preferences/*`
   - `state/chatwork_sent.json`
   - `outputs/{client}/tech_stack_verification.yaml`

2. **指摘後のライフサイクルが弱い**
   - 顧客が本当に対応したか
   - Zynect 側が何を支援したか
   - 対応後に数値がどう変わったか
   - どのルールが有効だったか
   - どのルールが誤検知だったか
   が横断的に追えない。

3. **クライアント別の運用異常に気づきにくい**
   - API 失敗
   - ChatWork 投稿失敗
   - 回答取り込み失敗
   - データ欠損
   - 同じ指摘の滞留
   - 通知が多すぎる / 少なすぎる
   が client 単位で監視されていない。

4. **ルール数が増えたが、ルール改善ループが未成熟**
   - 約 525+ ルールを持つが、どのルールが成果に寄与したかが蓄積されていない。
   - 顧客回答・実対応・効果測定が rule_id と結びついていないため、重みや優先度の改善が経験知に依存する。

5. **実行基盤が単一 Mac の launchd**
   - 本番運用が 1 台のローカルマシンに依存している。
   - スリープ、ネットワーク、OS 更新、ローカル環境差分が単一障害点になる。

## 2. Decision

Phase B/C では、Zynect BPO System を「通知スクリプト」から「運用プラットフォーム」へ進化させる。

中核に置く概念は **Operational Case** とする。

Operational Case とは、1 つの指摘・仮説・提案が、顧客対応と効果測定まで進む単位である。

```
Rule Evaluation
  -> Case Created
  -> Client Notified
  -> Client Responded
  -> Action Planned
  -> Action Implemented / Dismissed / Needs Help
  -> Outcome Measured
  -> Rule Feedback Updated
```

## 3. Target Architecture

### 3.1 論理構成

```
                 +-------------------+
                 |  Scheduler / Jobs |
                 +---------+---------+
                           |
                           v
 +-------------+   +---------------+   +------------------+
 | Connectors  |-->| Data Snapshot |-->| Rule Evaluation  |
 | Meta/Google |   | normalized    |   | 525+ rules       |
 +-------------+   +---------------+   +--------+---------+
                                                   |
                                                   v
                                         +------------------+
                                         | Operational Case |
                                         | lifecycle DB     |
                                         +--------+---------+
                                                  |
                 +--------------------------------+------------------------------+
                 |                                |                              |
                 v                                v                              v
        +----------------+              +-------------------+          +----------------+
        | ChatWork / CRM |              | Outcome Tracker   |          | Observability  |
        | notifications  |              | before/after      |          | job health     |
        +----------------+              +-------------------+          +----------------+
```

### 3.2 データ層

ADR-017 の SQLite 移行を拡張し、単なる state 統合ではなく、運用台帳として使う。

Phase B の最小構成は SQLite でよい。ただし API サーバー化 / 複数ワーカー化する段階では PostgreSQL に移行できるスキーマにしておく。

推奨:

| Phase | DB | 目的 |
|---|---|---|
| Phase B | SQLite (`state/zynect.db`) | 1〜5 社、ローカル運用の安定化 |
| Phase C | PostgreSQL | 10〜50 社、複数ジョブ/複数担当者 |
| Phase D | PostgreSQL + queue + object storage | 50 社以上、監査ログ/証跡長期保管 |

### 3.3 コアテーブル

#### clients

クライアント基本情報。

```sql
CREATE TABLE clients (
  client_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  vertical TEXT,
  ec_platform TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  owner_user_id TEXT,
  chatwork_room_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

#### data_snapshots

媒体 API / validator / CRM / 手入力から取得した日次データの正規化スナップショット。

```sql
CREATE TABLE data_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  source TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  error_json TEXT,
  UNIQUE(client_id, source, snapshot_date)
);
```

#### rule_evaluations

各 rule_id がいつ、何を根拠に、どう判定されたか。

```sql
CREATE TABLE rule_evaluations (
  evaluation_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  evaluation_date TEXT NOT NULL,
  status TEXT NOT NULL,          -- triggered / clean / skipped / unknown / data_unavailable
  severity TEXT,
  priority_score REAL,
  evidence_json TEXT NOT NULL,
  data_sources_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

#### operational_cases

指摘・提案・仮説のライフサイクル本体。

```sql
CREATE TABLE operational_cases (
  case_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT,
  owner_type TEXT NOT NULL DEFAULT 'client', -- client / zynect / system
  source_evaluation_id TEXT,
  first_detected_at TEXT NOT NULL,
  last_detected_at TEXT,
  notified_at TEXT,
  resolved_at TEXT,
  closed_at TEXT,
  payload_json TEXT NOT NULL
);
```

Status は以下に統一する。

| status | 意味 |
|---|---|
| `open` | 未対応 / 未確認 |
| `waiting_client` | 顧客回答待ち |
| `waiting_zynect` | Zynect 側支援待ち |
| `planned` | 対応予定あり |
| `implemented` | 対応実施済み、効果測定待ち |
| `monitoring` | 実施後の効果観測中 |
| `resolved` | 解消確認済み |
| `dismissed` | 対応不要 / 対象外 |
| `stale` | 長期未対応 |

#### case_events

ケースの全履歴。ChatWork返信も、Zynect側メモも、システム判定もここに積む。

```sql
CREATE TABLE case_events (
  event_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor_type TEXT NOT NULL,      -- client / zynect / system / api
  actor_id TEXT,
  event_at TEXT NOT NULL,
  message TEXT,
  payload_json TEXT
);
```

#### action_items

実際にやること。顧客に聞くだけで終わらせないためのタスク管理。

```sql
CREATE TABLE action_items (
  action_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  title TEXT NOT NULL,
  owner_type TEXT NOT NULL,      -- client / zynect
  status TEXT NOT NULL,          -- todo / doing / blocked / done / canceled
  due_date TEXT,
  completed_at TEXT,
  evidence_url TEXT,
  payload_json TEXT
);
```

#### outcome_measurements

対応後にどう変わったか。

```sql
CREATE TABLE outcome_measurements (
  outcome_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  baseline_start TEXT,
  baseline_end TEXT,
  measurement_start TEXT,
  measurement_end TEXT,
  baseline_value REAL,
  measured_value REAL,
  change_pct REAL,
  estimated_value_yen REAL,
  confidence TEXT,
  notes TEXT,
  payload_json TEXT
);
```

#### rule_feedback

ルール改善用の学習テーブル。

```sql
CREATE TABLE rule_feedback (
  feedback_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  case_id TEXT,
  feedback_type TEXT NOT NULL,   -- useful / false_positive / unclear / too_hard / too_late
  outcome_score REAL,
  comment TEXT,
  created_at TEXT NOT NULL
);
```

#### job_runs

クライアントごとの異常検知。

```sql
CREATE TABLE job_runs (
  job_run_id TEXT PRIMARY KEY,
  job_name TEXT NOT NULL,
  client_id TEXT,
  scheduled_at TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,          -- running / success / partial_failure / failed
  error_json TEXT,
  metrics_json TEXT
);
```

## 4. Application Layer

### 4.1 分割方針

`engine/` 直下に巨大ファイルを増やさず、業務境界ごとに package 化する。

```
engine/
├── stores/
│   ├── db.py
│   ├── clients.py
│   ├── snapshots.py
│   ├── cases.py
│   ├── responses.py
│   ├── outcomes.py
│   └── jobs.py
├── rules/
│   ├── loader.py
│   ├── evaluator.py
│   ├── selector.py
│   └── feedback.py
├── cases/
│   ├── lifecycle.py
│   ├── action_planner.py
│   ├── outcome_tracker.py
│   └── notification_policy.py
├── notifications/
│   ├── chatwork_builder.py
│   ├── ack_builder.py
│   └── digest_builder.py
└── observability/
    ├── job_monitor.py
    ├── health_check.py
    └── self_alert.py
```

### 4.2 重要な境界

| 境界 | 責務 |
|---|---|
| `connectors/` | 外部 API から生データ取得 |
| `data_snapshots` | 取得データの保存。加工前の証跡 |
| `rules/` | ルール評価。通知しない |
| `cases/` | 運用状態の更新。顧客対応・効果測定を持つ |
| `notifications/` | ChatWork / CRM 向け文面生成 |
| `observability/` | ジョブ失敗・クライアント異常の監視 |

ルール評価と通知を分離する。これにより「検知したが通知しない」「通知したが未対応」「対応済みだが効果未確認」を区別できる。

## 5. Client Operations Dashboard

複数クライアント運用では、ChatWorkだけを見ても全体像がわからない。最低限、CLI または簡易 Web UI で以下を見られるようにする。

### 5.1 Client Health

| 指標 | 意味 |
|---|---|
| `last_successful_run_at` | 最後に正常実行した時刻 |
| `data_freshness_hours` | 最新データの鮮度 |
| `open_cases_count` | 未解決ケース数 |
| `waiting_client_count` | 顧客回答待ち |
| `waiting_zynect_count` | Zynect側対応待ち |
| `stale_cases_count` | 長期滞留 |
| `notification_count_7d` | 直近7日の通知量 |
| `response_rate_30d` | 顧客回答率 |
| `measured_outcome_yen_30d` | 計測済み改善額 |

### 5.2 Alerts

以下は自己監視通知の対象にする。

| alert | 条件 |
|---|---|
| `client_job_failed` | client 単位の job failed |
| `data_stale` | 24h 以上データ更新なし |
| `notification_failed` | ChatWork 投稿失敗 |
| `response_ingest_failed` | ChatWork 回答取り込み失敗 |
| `too_many_notifications` | 1 client 1日上限超過 |
| `case_stale` | open / waiting が 14日以上滞留 |
| `outcome_missing` | implemented から 14日以上 outcome 未計測 |

## 6. Rule Improvement Loop

525+ ルールを大規模運用で使うには、ルールの「良し悪し」を測る必要がある。

各ルールに以下のメトリクスを持つ。

| metric | 意味 |
|---|---|
| `trigger_count` | 発火回数 |
| `notification_count` | 通知回数 |
| `client_response_rate` | 顧客回答率 |
| `confirmed_done_rate` | 対応済み率 |
| `false_positive_rate` | 誤検知 / 対象外率 |
| `implementation_rate` | 実施率 |
| `outcome_measurement_rate` | 効果測定まで到達した率 |
| `median_time_to_response` | 回答までの中央値 |
| `median_time_to_resolution` | 解消までの中央値 |
| `estimated_value_yen` | 改善額 |

これにより、優先度重みや cooldown を経験ではなくデータで調整する。

## 7. Scheduling

launchd は Phase A の MVP 実行基盤として維持するが、Phase C では以下へ移行する。

| Phase | Scheduler | 理由 |
|---|---|---|
| Phase A/B | launchd | 低コスト、すぐ動く |
| Phase C | GitHub Actions scheduled / VPS cron / Cloud Run Jobs | 単一Mac依存を外す |
| Phase D | Queue worker + managed scheduler | 多クライアント並列処理 |

Phase B では、launchd のままでも `job_runs` に全実行結果を記録し、単一障害点を可視化する。

## 8. Migration Plan

### Step 1: SQLite schema + repository layer

- `engine/stores/db.py`
- `migrations/001_operational_platform.sql`
- `scripts/migrate_state_to_db.py`
- JSON/YAML から DB への import
- 既存 JSON/YAML は当面 read fallback として残す

### Step 2: Operational Case 導入

- `IndicationState` の open/resolved を `operational_cases` に写像
- ChatWork 回答を `case_events` と `action_items` に保存
- `waiting_client` / `waiting_zynect` を明確化

### Step 3: Outcome Tracker

- 対応前後の KPI を `outcome_measurements` に保存
- completion notice は「解消」だけでなく「効果測定中」「高止まり継続」を表現

### Step 4: Job Monitoring

- 全 entrypoint の冒頭/終了で `job_runs` を記録
- 失敗時は client 単位で self alert
- 24h データ欠損 / 14日滞留を検知

### Step 5: Rule Feedback

- 誤検知 / 対応不要 / 有効だった指摘を `rule_feedback` に保存
- 月次で rule quality report を出す

### Step 6: Scheduler 移行

- launchd から cloud/VPS へ移行
- ChatWork token / client secrets は `.env` 直書きから secret manager 相当へ移す

## 9. Non-Goals

Phase B でやらないこと:

- いきなり Kubernetes 化しない
- 最初から巨大な Web 管理画面を作らない
- ルール 525 件を一気に JSONLogic 化しない
- ChatWork を捨てない
- CRM を主導線にしない

まずは「運用の真実が DB に残る」状態を作る。

## 10. Success Criteria

Phase B 完了条件:

1. すべての通知・回答・対応状態が `case_id` で追える
2. クライアントごとの job 成功/失敗が 1 コマンドで見える
3. 「顧客が対応したか」「Zynectが支援待ちか」「効果測定中か」が区別できる
4. 解消通知が「条件が消えた」だけでなく「数値が改善したか」を示す
5. rule_id ごとの回答率・誤検知率・効果測定率が出せる
6. 新クライアント追加時に state ファイルを手で増やさない

Phase C 完了条件:

1. launchd 依存を外す
2. PostgreSQL / managed DB に移行可能な schema になる
3. 10〜20 社の client health が一覧できる
4. 失敗が ChatWork または管理画面に自己通知される

## 11. First Implementation Slice

最初に実装するべき最小単位:

1. `migrations/001_operational_platform.sql`
2. `engine/stores/db.py`
3. `engine/stores/cases.py`
4. `engine/stores/jobs.py`
5. `scripts/migrate_state_to_db.py`
6. `scripts/client_health.py`

これだけで、既存の通知ロジックを大きく壊さずに、次の価値が得られる。

- state 散在の統合開始
- クライアント別異常の可視化
- 指摘後の対応状況の追跡
- 将来の効果測定 / rule feedback の土台

## 12. Screenshot Assessment Coverage

2026-05-09 時点のアーキテクチャ棚卸しで挙がった観点は、本 ADR では以下のように扱う。

| 観点 | 現状課題 | ADR-018 での対応 |
|---|---|---|
| データ永続化 | YAML / JSON が散在し、10〜20 社で破綻しやすい | `state/zynect.db` を運用台帳にし、`clients / snapshots / cases / events / outcomes / jobs` へ統合 |
| ルール体系 | 5 層 + 約 525+ ルールはあるが、連携が暗黙的 | `rule_evaluations` と `rule_feedback` で rule_id 単位の評価・成果・誤検知を記録 |
| エントリポイント | `pipeline.py` と `daily_chatwork_check.py` が並走 | ADR-016 を前提に、日次運用は job と case lifecycle に寄せる |
| スケジューリング | macOS launchd 1 台が単一障害点 | Phase B は `job_runs` で可視化、Phase C で cloud/VPS scheduler へ移行 |
| 設定管理 | YAML 読込と `.env` の企業別 secret が増えると爆発 | `clients` / secret manager 相当 / source 別 snapshot へ分離する方針 |
| モジュール分割 | `engine/` に巨大ファイルが増えている | `stores / rules / cases / notifications / observability` へ業務境界で分割 |
| テスト | 件数は多いが実運用頻度・状態遷移の保証が弱い | case lifecycle / migration / job health / outcome tracker のテストを追加対象にする |
| ドキュメント | ADR は強い | ADR-018 を上位設計にし、ADR-017 の SQLite 移行を運用台帳化へ拡張 |

## 13. Additional Concerns

スクリーンショット外で、今後の運用拡大時に不安が残る点。

### 13.1 Multi-tenant data isolation

複数社運用では、client_id の取り違えが最も危険。通知先 room_id、API token、データ snapshot、case、response はすべて client_id を必須にし、DB 制約とテストで担保する。

対策:

- 全テーブルに `client_id` を持たせる
- ChatWork 送信時に `client_id -> room_id` の整合チェック
- `pilotton` のデータが別 room に出ない regression test

### 13.2 Secret management

`.env` に企業別 token が増えると、更新漏れ・誤送信・漏洩リスクが上がる。

対策:

- Phase B は `.env` を継続しつつ、secret 名の命名規則を固定
- Phase C で 1Password CLI / Doppler / AWS Secrets Manager / GCP Secret Manager 相当へ移行
- ログや self alert に token / customer_id を出さないマスキングテスト

### 13.3 Data retention and backup

効果測定には過去データが必要。今の JSON ファイル運用では、削除・上書き・マシン故障がそのまま証跡喪失になる。

対策:

- SQLite は日次 backup
- `data_snapshots` は raw payload を保持
- 月次で gzip archive
- restore rehearsal を月1回実施

### 13.4 Notification fatigue

ルールが増えるほど、正しくても通知が多すぎる問題が起きる。顧客は「全部読む」運用をしない。

対策:

- 1日1まとめを維持
- `case_stale` と `waiting_client` は別枠でまとめる
- 重要度だけでなく `expected_value / urgency / customer burden` で通知順を決める
- 返信がない rule は聞き方を変える。単純再通知しない

### 13.5 API quota and external failures

Meta / Google / ChatWork / EC platform は rate limit・一時障害・権限切れが起こる。

対策:

- `data_snapshots.status = success / partial / failed / stale`
- API failure と clean 判定を混同しない
- retry は job 単位ではなく connector 単位に分離
- token invalid は client health の critical alert

### 13.6 Migration risk

JSON/YAML から DB へ移る時に、既存の通知 suppression や回答履歴を壊すと顧客に二重通知が出る。

対策:

- 移行前に full backup
- import 後に record count と hash を比較
- 1週間は dual-read / single-write で確認
- migration dry-run report を出す

### 13.7 Human workflow ownership

顧客が `C: 確認したい` と返した後、Zynect側の担当者が動かなければUXは悪いまま。

対策:

- `waiting_zynect` status を明示
- action owner を `client / zynect` で分ける
- Zynect 側未対応が 24h を超えたら self alert
- 月次で担当者別 backlog を出す

### 13.8 Rule governance

525+ ルールは「作ったら終わり」ではなく、廃止・統合・重み調整が必要。

対策:

- `rule_feedback` に false positive / useful / too_hard を蓄積
- 低反応・低成果 rule は月次レビュー
- 法令系と広告成果系を同じ優先度軸で扱わない
- ルール変更は ADR または rule changelog に残す

### 13.9 Cost visibility

Claude API、媒体 API、ChatWork、将来DB/サーバー費用が増える。

対策:

- `job_runs.metrics_json` に API call 数・LLM cost を保存
- client_id 別の月次コストを出す
- fallback が使われた回数も可視化する

### 13.10 Security and audit trail

運用代行に近い性質があるため、「誰が・いつ・何を判断したか」を残す必要がある。

対策:

- `case_events.actor_type / actor_id` を必須化
- 手動 override は理由必須
- 顧客回答の原文を保持
- 重要 case は evidence URL / screenshot / API payload hash を保存

## 14. Product Strategy and Moat

このサービスの moat は「広告運用の通知 bot」ではなく、以下の複合資産にある。

1. **運用ケースの履歴データ**
   - どの rule_id が、どの業種・媒体・EC platform で発火したか
   - 顧客がどう回答したか
   - 実際に対応されたか
   - 対応後に成果がどう変化したか

2. **ルール品質の学習データ**
   - 誤検知率
   - 顧客回答率
   - 実装率
   - 効果測定到達率
   - 改善額

3. **業種・媒体・EC platform 別の運用知**
   - ecforce × Meta
   - Shopify × Google
   - SaaS × Meta lead ads
   のような組み合わせごとの「どの指摘が効くか」。

4. **対応後の効果測定**
   - 単なる指摘ではなく、「対応したら何が改善したか」まで返せること。

したがって、Phase B/C の開発判断では、以下を優先する。

| 優先 | 方向性 | 理由 |
|---|---|---|
| 1 | Operational Case / Outcome DB | moat の原材料になる |
| 2 | Rule feedback loop | 525+ ルールの質を改善できる |
| 3 | Client health / job observability | 多社運用で信頼を落とさない |
| 4 | API/validator で答えられる質問の自動解決 | 顧客負担を下げる |
| 5 | 管理画面 / CRM 連携 | DB が整ってからでよい |

「きれいなアーキテクチャ」そのものは moat ではない。moat は、運用から得られる判断データと、顧客が成果を感じるフィードバックループである。

## 15. Detailed Architecture Risks

2026-05-09 の追加レビューで挙がった詳細論点を、設計課題として整理する。

### 15.1 Full-file read/write architecture

現状は indication 1 件の status 更新でも、企業単位の JSON 全体を読み込み、丸ごと書き戻す。

問題:

- 1 社 1,000 indications × 12 ヶ月でファイルが MB 級になる
- 日次 3 回 retry × 媒体 × 525+ ルールで読み書き回数が増える
- 部分更新・トランザクション・行ロックがない

対応:

- `operational_cases` / `case_events` / `rule_evaluations` へ行単位で保存
- SQLite WAL mode を有効化
- JSON/YAML は config / fixture / export 用に限定

### 15.2 Cross-client queries are impossible

現状では、以下のような運用クエリが全 JSON grep になる。

- 過去 30 日で resolved になった指摘の中央値日数
- クライアント横断で頻出する rule_id
- critical が連続発生している企業ランキング
- 回答率が低いクライアント
- outcome 未計測の implemented case

対応:

- `operational_cases`, `case_events`, `outcome_measurements`, `rule_feedback` を正規化
- `scripts/client_health.py` と `scripts/rule_quality_report.py` を作る

### 15.3 Snapshot consistency and backup

分散ファイルでは、復旧時に以下のような不整合が起きる。

- indication は 9:05
- chatwork_sent は 9:07
- auto_proposal_history は 9:10

ADR-005 の 9:00/9:15/9:30 retry は通知冪等性を守るが、状態整合性は守らない。

対応:

- DB transaction で `send -> record -> case event` を一貫更新
- 日次 backup は DB 単位
- migration 時は full snapshot + restore rehearsal

### 15.4 Git-tracked and gitignored operational files are mixed

`outputs/client_state/*.yaml` は tracked、`outputs/chatwork_state/*.json` は ignored など、運用ルールがファイルごとに違う。

問題:

- 新企業追加時に何を commit すべきか属人化
- runtime state が git に混ざる
- 顧客固有設定と運用履歴の境界が曖昧

対応:

- Git tracked: rule, template, schema, non-secret config
- DB: runtime state, responses, cases, job runs, outcomes
- Secret manager: token, room-specific secret, API credential
- Export: human-readable YAML snapshot は生成物扱い

### 15.5 Parallel classification axes are not unified

現状、以下の評価軸が並列に存在する。

- root_cause groups
- tradeoff axes
- Foundation categories
- Precision categories
- EC platform layer
- vertical layer
- severity / polarity / priority
- intent override

問題:

- どの軸が通知順に効いたのか追えない
- 二重判定が起きる
- `intent_filter -> indication_filter -> auto_proposal` のどこで落ちたか不明

対応:

- `rule_evaluations.evidence_json` に各評価軸の breakdown を保存
- `decision_trace` を標準化する

```json
{
  "rule_id": "F-MF-01",
  "matched": true,
  "filters": [
    {"name": "applies_to", "result": "pass"},
    {"name": "intent_override", "result": "downgrade", "from": "critical", "to": "medium"},
    {"name": "daily_cap", "result": "suppressed"}
  ],
  "score_breakdown": {
    "severity": -10,
    "expected_value": -20,
    "customer_burden": 5
  }
}
```

### 15.6 Prerequisite graph is implicit

ルール間 prerequisite が YAML に手書きされているが、全体グラフを可視化・検証する機構がない。

問題:

- 新ルール追加時に上流条件の重複を grep で確認する必要がある
- 循環 dependency を検出できない
- 「未達 prerequisite のため通知されない」理由が顧客にも運用者にも見えない

対応:

- `rule_dependencies` table を作る
- rule load 時に dependency graph を構築
- cycle / missing rule / duplicate prerequisite を CI で検出
- `scripts/rule_graph.py --client pilotton` で可視化

### 15.7 Duplicate symptom detection is missing

Foundation measurement、Precision measurement、Layer A M02 など、同じ症状を別 rule_id で指摘する可能性がある。

conflict_detector は対立検出であり、重複統合とは別問題。

対応:

- `symptom_key` を rule 定義に追加
- 通知前に same symptom group を bundle
- 顧客には 1 件として出し、内部では複数 rule_id を紐づける

例:

```yaml
symptom_key: measurement.pixel_or_capi_quality
primary_rule: F-MF-01
related_rules: [M02, M03, X-PI1]
```

### 15.8 Rule ID体系の三重化

現状:

- code ID: `G01`, `M02`, `T05`
- YAML ID: `GOOGLE_001`, `META_002`
- new layer ID: `F-MF-01`, `F-LC-04`, `V-EC-01`, `P-EF-02`, `X-PI1`, `ANO_CPA_SPIKE`

問題:

- `id_mapper.py` が deprecated なのに残っている
- 1 文字違いで rule_messaging 未定義になり、顧客通知から消える
- 新規ルール追加時の正しい ID 体系が不明

対応:

- canonical ID を `rule_registry` で定義
- alias は registry にだけ持つ
- code 側は canonical ID だけを返す
- `rule_messaging` 未定義は CI error に昇格。ただし `customer_visible: false` を明示した rule は除外

### 15.9 Fallback禁止が silent missing を生む

rule_messaging 未定義 rule は顧客向けに出さない方針はノイズ抑制として正しい。しかし 525+ ルール中、顧客向け messaging が未定義の rule が多いと、検出しても顧客価値にならない。

対応:

- `customer_visible` を rule registry に追加
- `customer_visible: true` なのに messaging 未定義なら CI fail
- `customer_visible: false` なら internal-only として明示
- unmapped rule 件数を job_runs metrics に保存

### 15.10 eval DSL is unsafe and operationally opaque

`eval()` を `__builtins__` なしで実行しても、長期的な安全な sandbox とはみなさない。

また、例外時に False を返すと「発火しなかった」のか「壊れて評価不能」なのか区別できない。

対応:

- ADR-017 の JSONLogic 移行を前倒し
- `trigger_eval_status = pass / fail / error / missing_data` を保存
- preflight で全ルールを representative context に対して評価
- eval error は debug ではなく rule_evaluations に残す

### 15.11 Cache consistency owner is missing

複数箇所で `rule_messaging.yaml` や mapping YAML を別々に cache している。

問題:

- preview と production で違う cache を見る
- reload 条件がない
- テストで cache 汚染が起き得る

対応:

- `engine/config_registry.py` を作る
- YAML loader / cache / reload / schema validation を一元化
- config version hash を job_runs に保存

### 15.12 State machine is implicit

auto_proposal、ChatWork response、client_state が平面的なフラグ集合になっている。

問題:

- `not_started -> in_progress -> completed -> verified` の不変条件がない
- completed から not_started へ巻き戻る事故を防げない
- 手動/API/Claude の3経路更新が競合する

対応:

- `operational_cases.status` を正にする
- state transition table を定義
- invalid transition は例外
- すべての遷移を `case_events` に記録

### 15.13 Tests are module-heavy but flow-light

単体テストは厚いが、全 525 ルール、rule_messaging、ID registry、notification、response ingestion、outcome までの end-to-end が薄い。

対応:

- `tests/test_rule_registry_integrity.py`
- `tests/test_operational_case_lifecycle.py`
- `tests/test_multiclient_isolation.py`
- `tests/test_daily_job_e2e.py`
- `tests/test_migration_state_to_db.py`

### 15.14 CI green is not production confidence

CI は通っても、本番で reply parsing / context / self alert が壊れた履歴がある。

対応:

- production-like fixture で daily job e2e
- `--dry-run` と本番経路の差分を縮める
- smoke test を CI ではなく deploy前 checklist に追加
- GitHub Issues / Projects で運用バグを管理

### 15.15 Phase A/B/C is overloaded

`phase: A/B/C` が以下を兼任している。

- 顧客導入フェーズ
- プロダクト機能フェーズ
- 通知運用フェーズ

対応:

```yaml
client_lifecycle_stage: onboarding | active | mature | churn_risk
feature_flags:
  adtruth: phase_a
  auto_proposal: phase_b
notification_mode: test | production | paused
```

### 15.16 Template and logic are too tightly coupled

顧客別文体、A/B test、返信率改善をしたい場合に、Jinja2 template を git release しないと変えられない。

対応:

- template registry を DB/config に分離
- `template_variant` を client/case に持たせる
- 顧客別 tone は template parameter にする
- ChatWork 以外の channel でも同じ case payload から render できるようにする

### 15.17 ChatWork lock-in

ChatWork は Phase A の主チャネルだが、Slack / Teams / Email / LINE WORKS に拡張するには、送信だけでなく返信取り込み・冪等性・状態同期が必要。

対応:

- `notification_channels`
- `notification_messages`
- `reply_events`
- channel adapter interface

ChatWork 固有の parser は adapter 配下へ閉じ込める。

### 15.18 Clock and timezone consistency

`datetime.now()` が各モジュールに散在している。

問題:

- 深夜跨ぎで同一 job 内の日付がズレる
- JST/UTC 混在で clean days が off-by-one

対応:

- job 開始時に `run_date_jst` を固定
- 全関数へ context として渡す
- DB は UTC timestamp + local business date を両方保存

### 15.19 PDF/report generation scalability

Playwright / Chromium PDF 生成は月次 20 社程度なら許容だが、日次添付や ad-hoc 再生成が増えると詰まる。

対応:

- report generation を job queue 化
- HTML render と PDF export を分ける
- artifact storage を導入
- 同一月次レポートは cache

### 15.20 Observability is local-file only

構造化ログは良いが、集約・メトリクス・trace がない。

対応:

- OpenTelemetry の signals: traces / metrics / logs に寄せる
- `trace_id`, `job_run_id`, `case_id`, `client_id`, `rule_id` を全ログに入れる
- rule evaluation count / suppressed count / unmapped count / API latency を metrics 化

### 15.21 Data provenance and AI output boundaries

ベンチマーク、外部調査、Claude 生成、独自理論が混在すると、事実と推測の境界が曖昧になる。

対応:

- `source_type`: api / benchmark / customer_reply / human_override / llm_inference
- `source_url` / `source_version` / `observed_at`
- LLM 出力は `inference` として保存し、fact と混ぜない

### 15.22 clients.yaml is a scaling bottleneck

全社が 1 YAML に入ると、編集競合・アクセス制御・機密境界で破綻する。

対応:

- DB `clients` table へ移行
- non-secret client profile は `config/clients/{client_id}.yaml` でもよい
- secret と runtime state は git から外す

### 15.23 Partial failure contract is not standardized

`errors`, `skipped`, `dry_run`, `failed`, `attempted` などの戻り値が各関数でばらつく。

対応:

標準 result object:

```python
{
  "ok": bool,
  "status": "success" | "partial_failure" | "failed" | "skipped",
  "attempted": int,
  "succeeded": int,
  "failed": int,
  "errors": [...],
  "side_effects": [...]
}
```

dry-run は side effect adapter を差し替える形にし、本番コード内の `if not dry_run` を減らす。

## 16. Revised Roadmap

上記を踏まえ、ADR-017 の Phase B 計画を以下に上書きする。

### Phase B0: Stabilize Current Production

目的: 明日の運用を壊さない。

- client_id / run_date を全ログに固定
- ChatWork 誤通知・誤反映の regression test
- `internal_unmapped_rule` を daily summary に出す
- critical self alert の経路確認

### Phase B1: Operational DB Minimum

目的: 運用の真実を DB に残す。

- SQLite schema
- `job_runs`
- `operational_cases`
- `case_events`
- `action_items`
- JSON/YAML import
- client health CLI

### Phase B2: Rule Registry and Decision Trace

目的: 525+ ルールを安全に運用する。

- canonical rule registry
- alias / deprecated ID の整理
- customer_visible と messaging coverage check
- decision_trace 保存
- prerequisite graph validation
- duplicate symptom bundling

### Phase B3: Outcome and Feedback Loop

目的: 顧客価値と moat を作る。

- outcome measurements
- before/after metrics
- rule feedback
- monthly rule quality report
- case resolved と performance recovered を分離

### Phase C: Multi-client Runtime

目的: 10〜20社に耐える。

- active clients iterator
- per-client failure boundary
- scheduler 移行
- secret manager
- notification channel abstraction
- centralized observability

## 17. Research Alignment

本 ADR は以下の定石と整合させる。

- Twelve-Factor App:
  - config と code を分離する
  - DB / queue / external API を backing service として扱い、付け替え可能にする
  - logs を event stream として扱う
- OpenTelemetry:
  - logs / metrics / traces を相関させ、未知の問題に答えられるようにする
  - `trace_id`, `job_run_id`, `client_id`, `case_id`, `rule_id` を関連づける
- OWASP Secrets Management:
  - secret を中央管理し、アクセス制御・監査・ローテーション・失効を持つ
- SQLite:
  - Phase B では WAL mode により atomic commit と reader/writer 並行性を得る
  - Phase C では PostgreSQL へ移行可能な schema に保つ
