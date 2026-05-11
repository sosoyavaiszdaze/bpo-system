# Rule Governance Migration

Date: 2026-05-10
Status: Draft for production migration

## Goal

Move the rule/YAML system from an MVP checklist into a production rule governance layer.

The business goal is not "detect many issues." The goal is:

- preserve CV volume
- reduce CPA
- avoid ROAS/LTV regression
- turn every customer-facing rule into a measurable operational case

## Current Baseline

`scripts/rule_registry_audit.py` is the source of truth for the current migration baseline.

As of 2026-05-10:

- Total YAML rules: 550
- Enabled rules: 464
- Disabled rules: 86
- Customer-visible rules: 18
- High/critical enabled rules: 148
- High/critical enabled rules without customer messaging: 131
- Rules with expected impact: 95
- Rules with root cause group: 318
- Rules with decision axis: 114
- Rules using Python eval trigger conditions: 248
- Draft/placeholder-like rules: 135
- Legacy variant IDs: 27

## Production Rule Contract

Every production rule must be representable as one decision unit:

```yaml
id: F-MF-01
lifecycle: active
owner: zynect
layer: foundation
category: measurement_foundation
severity: high
root_cause_group: measurement_foundation
decision_axis: measurement_recovery
optimization_goal:
  primary: preserve_cv
  secondary: reduce_cpa
applies_to:
  countries: [JP]
  ad_platforms: [meta]
trigger:
  dsl:
    and:
      - eq: [client_state.meta_purchase_event_verified, false]
customer_message:
  title: CVイベントの動作確認
  today_action: Meta Events ManagerでPurchaseイベント発火を確認してください。
  yes_no_question: Purchaseイベントは直近24時間で発火していますか？
  action_options:
    A: 確認済み
    B: 未確認
    C: 確認したい
impact_estimate:
  lift_rate:
    minimum: 0.01
    realistic: 0.03
    upper: 0.08
  measurement_window:
    signal: 1-2週
    verdict: 4週
risk_control:
  cv_loss_risk: low
  do_not_do:
    - 計測確認前にCV獲得キャンペーンを停止しない
answer_source_preference: [api, validator, chatwork_reply]
outcome:
  primary_metric: cpa
  guardrail_metric: conversions
  measurement_window_days: 28
```

## Migration Gates

### P0: Customer Visibility Gate

High/critical enabled rules must not silently disappear.

Required before a rule is customer-visible:

- `customer_message.title`
- `today_action`
- `yes_no_question`
- `action_options`
- `answer_source_preference`
- `impact_estimate.measurement_window`

Rules missing this contract stay internal or shadow-only.

### P1: CV Preservation Gate

CPA-related recommendations must declare whether they can reduce CV volume.

Required fields:

- `optimization_goal.primary`
- `optimization_goal.secondary`
- `risk_control.cv_loss_risk`
- `risk_control.do_not_do`
- `outcome.guardrail_metric`

The system should reject recommendations that improve CPA by simply cutting high-CV delivery without diagnosis.

### P2: DSL Gate

New rules must not use Python `eval` conditions.

Allowed during transition:

- existing `trigger.condition` remains supported
- new production rules use `trigger.dsl`
- audit reports `unsafe_eval_trigger` until migrated

### P3: Lifecycle Gate

Every rule must declare one lifecycle:

- `draft`: not included in production scoring
- `shadow`: evaluated and logged, not sent to customers
- `active`: production eligible
- `deprecated`: kept for historical reads only

Skeleton rules must never be counted as production coverage.

## Operating Model

The rule system should be operated like a product, not a static checklist:

1. Rule fires.
2. Decision trace records why it fired or did not fire.
3. Customer notification is sent only if messaging and source requirements pass.
4. Customer/API response updates the operational case.
5. Outcome tracker measures CPA/CV/ROAS impact.
6. Rule feedback UI shows whether the rule is useful, noisy, stale, or missing evidence.
7. Operators improve rule thresholds, copy, prerequisite graph, and impact assumptions.

## Near-Term Backlog

1. Add the P0/P1 schema fields to the top 40 high/critical unmapped rules.
2. Add lifecycle metadata to all YAML rules.
3. Add CI warning mode for `high_severity_unmapped`, `placeholder_marked_active`, and `unsafe_eval_trigger`.
4. Add a Rule Registry UI filter for:
   - high/critical unmapped
   - draft/placeholder
   - missing impact estimate
   - missing answer source
   - eval trigger
5. Convert new AdTruth Proposal rules to `trigger.dsl` and `CV Preservation` contract first.

