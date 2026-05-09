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

