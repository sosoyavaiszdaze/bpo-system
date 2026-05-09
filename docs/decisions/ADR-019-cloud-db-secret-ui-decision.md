# ADR-019: Cloud / DB / Secret / Operations UI Decision

- **Status**: Proposed
- **Date**: 2026-05-09
- **Related**: ADR-013, ADR-017, ADR-018

## 1. Context

ADR-018 で Zynect BPO System を「通知スクリプト」から「運用プラットフォーム」へ進化させる方針を決めた。

次に決めるべきことは以下。

1. どのクラウドアカウントを使うか
2. DB をどこまで SQLite で進め、いつ PostgreSQL へ移すか
3. `.env` の企業別 secret をどう管理するか
4. 企業数が増えた時、運用者が何を見る UI を作るか
5. 525+ ルールの整合性と PDCA をどう管理するか
6. CPA / ROAS / 工数削減などの成果指標をどう計測するか

## 2. Decision

Phase B/C の標準クラウドは **Google Cloud Platform** とする。

推奨構成:

| 領域 | Phase B | Phase C |
|---|---|---|
| Scheduler | macOS launchd 継続 | Cloud Scheduler |
| Worker | local Python | Cloud Run Jobs |
| DB | SQLite (`state/zynect.db`) | Cloud SQL for PostgreSQL |
| Secrets | `.env` + 命名規則固定 | Secret Manager |
| UI | CLI / local read-only dashboard | Cloud Run service + auth |
| Logs | local JSON log + `job_runs` | Cloud Logging + DB `job_runs` |

AWS は EventBridge Scheduler + ECS/Fargate + RDS + Secrets Manager で同等に構成できる。ただし、Zynect の現段階では Cloud Run Jobs の方が部品数が少なく、非エンジニア運用者に説明しやすい。

## 3. Rationale

### 3.1 GCP を選ぶ理由

Cloud Run Jobs は日次バッチのような「起動して完了するジョブ」に合う。Cloud Scheduler から指定時刻に起動でき、タイムゾーン付き cron を設定できる。

Cloud SQL for PostgreSQL は Cloud Run から接続する公式導線があり、将来 UI / API サーバーを Cloud Run に置く場合にも同じ基盤で扱える。

Secret Manager は IAM で最小権限を付けられ、secret rotation も運用に乗せられる。

### 3.2 AWS を今回の第一候補にしない理由

AWS は堅牢だが、ECS/Fargate、EventBridge Scheduler、RDS、Secrets Manager、VPC/Security Group/IAM role の理解が必要になる。将来大規模化した時は十分選択肢だが、MVP から Phase C への移行としては学習・運用コストが重い。

## 4. User Actions Needed

Phase B 中はユーザー側の作業は最小でよい。

Phase C 移行前に必要な意思決定:

1. Google Cloud の請求先アカウントを作る
2. 本番プロジェクト名を決める。例: `zynect-bpo-prod`
3. リージョンを決める。原則 `asia-northeast1`
4. 本番通知の内部監視先を決める。ChatWork / Slack / email のいずれか
5. データ保持期間を決める
   - raw API snapshot: 13か月
   - ChatWork response / case event: 24か月
   - job log summary: 24か月
   - full debug payload: 90日
6. 顧客返信後の Zynect 側 SLA を決める
   - `C: 確認したい` / `wants_help`: 1営業日以内
   - critical API failure: 当日中

## 5. Operations UI Scope

UI は「派手な管理画面」ではなく、運用者が毎朝見るコックピットから始める。

### 5.1 Phase B UI: Read-only Operations Console

最初の画面:

| 画面 | 目的 |
|---|---|
| Client Health | 全クライアントの異常・未対応・データ鮮度を見る |
| Case Inbox | `waiting_zynect` / `waiting_client` / `stale` を処理する |
| Rule Quality | ルール別の発火数・回答率・誤検知率・効果測定率を見る |
| Outcome | CPA / ROAS / CV / 工数削減の改善推移を見る |
| Job Runs | 日次ジョブの成功/失敗とエラーを見る |

Phase B はまず read-only。編集は CLI / YAML のままでもよい。

### 5.2 Phase C UI: Operator Actions

次に UI から可能にする操作:

- case を `waiting_zynect -> planned -> implemented -> monitoring -> resolved` に進める
- 顧客返信を rule feedback に変換する
- false positive / too_hard / useful を記録する
- notification suppress / cooldown / owner を設定する
- unmapped rule に messaging を割り当てる

## 6. Outcome Metrics

前回議論した成果指標は `outcome_measurements` へ入れる。

標準 KPI:

| metric | 意味 | 計算例 |
|---|---|---|
| `cpa_change_pct` | CPA改善率 | `(baseline_cpa - measured_cpa) / baseline_cpa` |
| `roas_change_pct` | ROAS改善率 | `(measured_roas - baseline_roas) / baseline_roas` |
| `cv_change_pct` | CV増加率 | `(measured_cv - baseline_cv) / baseline_cv` |
| `spend_efficiency_pct` | 同成果に必要な広告費の削減率 | `(baseline_spend_per_cv - measured_spend_per_cv) / baseline_spend_per_cv` |
| `ops_hours_saved` | 運用工数削減時間 | `before_hours - after_hours` |
| `ops_cost_saved_yen` | 運用工数削減額 | `ops_hours_saved * hourly_rate` |
| `time_to_response_hours` | 顧客回答までの時間 | `client_response_at - notified_at` |
| `time_to_resolution_days` | 解消までの日数 | `resolved_at - first_detected_at` |
| `false_positive_flag` | 誤検知 | `rule_feedback.feedback_type = false_positive` |

レポートでは、CPA / ROAS だけでなく、運用工数削減を必ず入れる。Zynect の価値は「広告成果改善」だけでなく「運用判断の自動化・抜け漏れ削減」にもあるため。

## 7. Impact Calibration

改善予測は、単純な `monthly_spend * expected_lift` では過大にも過小にもなりやすい。

Phase B では既存の `impact_estimator.py` を維持しつつ、以下の補正軸を追加する。

```
calibrated_effect =
  base_effect
  * industry_factor
  * product_factor
  * budget_scale_factor
  * media_mix_factor
  * ops_quality_factor
  * data_confidence_factor
```

### 7.1 Calibration Dimensions

| dimension | 例 | 目的 |
|---|---|---|
| `industry` | beauty_d2c / ec_retail / subscription_saas / finance | 業界ごとの CVR / CPA / ROAS の相場差を反映 |
| `product_type` | 単品通販 / 定期購入 / 高単価商材 / B2B lead | 購買検討期間と LTV 差を反映 |
| `monthly_spend_band` | under_300k / 300k_1m / 1m_3m / over_3m | 予算規模による学習量・改善余地を反映 |
| `media_mix` | meta_only / meta_google / google_only / multi_channel | 媒体構成による施策効果の出方を反映 |
| `ops_quality_tier` | low / standard / advanced | 既存運用品質が高いほど追加改善率を保守化 |
| `data_confidence` | api_verified / partial / manual / unknown | 実データの信頼度が低い時は予測幅を広げる |

### 7.2 Required Inputs

`clients` または client profile に以下を持たせる。

| field | source |
|---|---|
| `industry` | client config / onboarding |
| `product_type` | onboarding |
| `monthly_spend_band` | media API snapshot |
| `media_mix` | clients.yaml / media connector |
| `ops_quality_tier` | rule_evaluations + response history から算出 |
| `data_confidence` | data_snapshots.status / source |

### 7.3 ChatWorkへの反映

ChatWork の指示文では、金額だけでなく予測の前提も短く出す。

例:

```
▼ 期待効果
  確実: 月 +¥30,000
  現実: 月 +¥80,000
  上限: 月 +¥200,000
  前提: beauty_d2c / 月額100〜300万円規模 / Meta中心 / 計測品質: partial
  兆候: 1〜2週 / 判定: 4週
```

この前提が見えることで、顧客にも Zynect 側にも「なぜその数字なのか」を説明できる。

### 7.4 Rule Feedbackとの接続

実績が溜まったら `rule_feedback` と `outcome_measurements` から補正係数を更新する。

| 観測 | 次の調整 |
|---|---|
| 同じ業界で outcome が consistently high | `industry_factor` を上げる |
| 予算小規模で効果が出にくい | `budget_scale_factor` を下げる |
| ops_quality が高い顧客で伸びが小さい | `ops_quality_factor` を下げる |
| manual data で外れが多い | `data_confidence_factor` を下げ、予測幅を広げる |

## 8. Axis / Rule Integration

現状、軸の設計は存在するが、すべてが1つの registry に統合されているわけではない。

### 8.1 Current State

| 軸 | 現状 | 課題 |
|---|---|---|
| `layer` | foundation / vertical / ec_platform / precision などに存在 | Layer A と新層の ID 体系が混在 |
| `root_cause_group` | 6分類として多くのルールに存在 | 効果計算・通知順序・重複排除への接続が一部のみ |
| `axis_position` | TO-01〜TO-11 または null | null / neutral が多く、判断軸として弱いルールが残る |
| `applies_to` | vertical / ec_platform / ad_platform に存在 | tech_stack confidence と連携するが、trace が弱い |
| `expected_impact` | 一部ルールに存在 | 業界・商材・媒体構成による補正が未実装 |
| `rule_messaging` | 顧客表示用に別定義 | rule 本体との同期漏れリスク |

### 8.2 Target State

Rule Registry で以下を1行に統合する。

```
canonical_rule_id
layer
root_cause_group
decision_axis
applies_to
prerequisites
duplicates
expected_impact_profile
messaging_profile
calibration_profile
```

### 8.3 Decision Trace

各 rule evaluation では、最終的な通知前に以下を保存する。

| step | 保存内容 |
|---|---|
| environment_match | vertical / ec_platform / media_mix が合ったか |
| data_availability | API / validator / manual のどれで見たか |
| trigger_eval | 発火条件の結果 |
| prerequisite_eval | 上流条件の充足 |
| duplicate_suppression | 類似ルールに吸収されたか |
| priority_score | 通知順序スコア |
| impact_calibration | 予測係数と前提 |
| messaging_status | 顧客表示対象か / internal only か |

これで「軸や項目がちゃんと連携できているか」を UI で検証できる。

## 9. Rule Governance for 525+ Rules

525+ ルールは、発火させるだけでは価値にならない。以下の5段階で管理する。

```
Rule Registry
  -> Rule Evaluation
  -> Operational Case
  -> Outcome Measurement
  -> Rule Feedback
```

### 9.1 Rule Registry

全 rule_id に最低限以下を持たせる。

| field | 目的 |
|---|---|
| canonical_rule_id | ID体系の統一 |
| layer | Layer A / foundation / vertical / ec_platform / precision / tradeoff |
| root_cause_group | 6分類との接続 |
| decision_axis | 15前後の判断軸との接続 |
| prerequisites | 上流依存 |
| duplicates | 重複/類似ルール |
| owner | ルール責任者 |
| customer_visible | 顧客通知対象か |
| messaging_status | mapped / unmapped / internal_only |

### 9.2 Decision Trace

各 rule evaluation には以下を残す。

- 入力データ
- 発火/非発火
- skip reason
- prerequisite 状態
- severity 変更
- cooldown/cap 判定
- messaging mapped/unmapped

これにより「なぜこの指摘が出た/出なかった」を UI で説明できる。

### 9.3 Rule Quality Loop

月次で以下を確認する。

| 指標 | 判断 |
|---|---|
| 発火多いが返信率低い | 文面または優先度を見直す |
| 回答は多いが効果測定に進まない | action が曖昧 |
| false positive が高い | trigger / prerequisite を修正 |
| outcome が高い | 類似ルールへ展開 |
| too_hard が多い | Zynect 側代行メニュー化 |

## 10. Implementation Order

ADR-017 の Phase B を ADR-018/019 に合わせて並べ替える。

| 優先 | 実装 | 理由 |
|---|---|---|
| B0 | 現行 launchd + ChatWork の安定化 | pilotton 運用を止めない |
| B1 | SQLite operational DB を本番経路に接続 | case / response / outcome の真実を残す |
| B2 | Client Health CLI / read-only UI | 企業数増加時の運用異常を見える化 |
| B3 | Rule Registry / Decision Trace | 525 ルールの整合性を管理 |
| B4 | Impact Calibration + Outcome Tracker | CPA / ROAS / 工数削減を効果として蓄積し、予測係数を更新 |
| B5 | JSONLogic 移行 | eval とサイレント失敗をなくす |
| C1 | Docker化 + Cloud Run Jobs PoC | Mac依存を外す準備 |
| C2 | Secret Manager 移行 | `.env` 爆発を止める |
| C3 | Cloud SQL PostgreSQL 移行 | 10〜20社に対応 |
| C4 | UI に編集操作を追加 | 運用チームで回せる状態にする |

AdTruth / Google / TikTok / CRM / Slack / Lark は、C1 以降に connector と notification abstraction に乗せる。先に個別実装すると、現在の ChatWork 専用構造を増やしてしまう。

## 11. References

- Google Cloud: Cloud Run Jobs on schedule
  - https://cloud.google.com/run/docs/execute/jobs-on-schedule
- Google Cloud: Cloud SQL for PostgreSQL from Cloud Run
  - https://cloud.google.com/sql/docs/postgres/connect-run
- Google Cloud: Secret Manager best practices
  - https://cloud.google.com/secret-manager/docs/best-practices
- AWS: EventBridge Scheduler for ECS tasks
  - https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html
- AWS: Secrets Manager
  - https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html
- AWS: RDS
  - https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html
