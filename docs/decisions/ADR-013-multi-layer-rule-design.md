# ADR-013: 多層ルール体系の設計 (Foundation + Vertical + Platform + Precision)

| 項目 | 値 |
|------|---|
| **Status** | Proposed (5/5 中身実装、Phase B Week 1-3 全層完了) |
| **Decision Date** | 2026-05-05 |
| **Authors** | 山本 (要件提示) / Claude Code (設計) |
| **Related ADRs** | ADR-001/002/003/004/005 (Accepted), ADR-006/008/009/011/012 (Draft) |

---

## Context

### 既存 277 ルールの位置付け (ADR-001-005 で確立済)

既存の `config/rules/{google,meta,tiktok,seo,adtruth,common}_rules.yaml` 計 277 ルールは、**広告運用の数値診断 (KPI レイヤー)** を担当している:

- CTR 低下 / CPA 高騰 / 予算消化異常 / 学習フェーズ未脱出 / 広告セット数過多 等
- `analyzers/ads_audit.py` が pipeline で評価
- 6 root_cause_group + 11 軸トレードオフ + 3 層インパクトの体系で完成

### 既存 277 ルールでカバーできない領域

しかし、以下は **数値診断の射程外** (= 既存 277 ルールでは検知不能):

| 領域 | 例 | 既存 277 で検知不能な理由 |
|------|-----|------------------------|
| **業界規制法令遵守** | 薬機法・景表法・宅建業法・金商法 | 広告 KPI から法令違反は判定不能 |
| **同意管理基盤** | CMP 導入有無 / iOS ATT 対応 | クライアント環境設定の有無 |
| **計測基盤の前提条件** | サーバーサイド GTM / GA4 設定 | 設定有無は API データに現れない |
| **業界別ベストプラクティス** | EC: F2 転換率連動 / SaaS: LTV/CAC | KPI 単体ではなく構造の問題 |
| **EC プラットフォーム連携** | ECフォース 注文 API / Shopify Pixel | プラットフォーム固有の設定 |

これらは「**環境前提の診断**」であり、KPI 数値とは独立した次元。

### 自動提案エンジン (ADR-012) の課題

ADR-012 で設計した自動提案エンジンは、`config/auto_proposal_rules.yaml` に **7 ルール固定** で記述している。これでは:
- 業界別の差分 (薬機法 vs 金商法) を表現できない
- クライアント環境 (ECフォース vs Shopify) で適用ルールが切り替わらない
- ルール数が増えると単一 YAML が肥大化

---

## Decision

### D-1. **5 層構造への拡張**

既存 5 媒体ルール群 (Layer A) はそのまま維持し、上位に **4 層を増設**:

```
Layer A: 既存 5 媒体ルール (277 ルール、KPI 数値診断)
         google_rules / meta_rules / tiktok_rules / seo_rules / adtruth_rules / common_rules
         → analyzers/ads_audit.py が評価、本 ADR では一切触らない

Layer 0: Foundation (業界横断必須、~70 ルール)
         legal_compliance / privacy_protection / measurement_foundation /
         account_health / data_governance / operations_quality /
         ad_fraud_screening / security_continuity

Layer 1: Vertical (日本の業界別、~70 ルール)
         ec_d2c / subscription_saas / btob_lead / real_estate /
         finance / hr_recruiting / healthcare_clinic / education / local_service

Layer 2: EC/Business Platform (~30 ルール)
         ecforce / shopify / base / makeshop / futureshop / custom

Layer 3: Precision Category (精度カテゴリ横断、~80 ルール)
         measurement / audience / data_quality / creative /
         bidding / brand_safety / attribution

合計: 277 (既存) + 約 250 (新規) = 約 527 ルール
```

### D-2. 既存 277 ルールとの関係 (補完関係)

| 区分 | 担当領域 | 既存 / 新規 |
|------|---------|------------|
| 既存 277 ルール (Layer A) | **数値 KPI 診断** (CTR/CPA/ROAS 等の異常検知) | 既存維持 |
| ADR-013 新規 ~250 ルール (Layer 0-3) | **環境/法令/基盤診断** (前提条件・遵守状況) | 新規追加 |

**両者は補完関係**。例:
- 既存 M02 (CAPI 実装状況) = 「CAPI 設定が動いているか」を Meta API で検知
- 新 F-MF-07 (サーバーサイド GTM 導入) = 「ssGTM 環境構築の有無」を client_state.yaml で診断
- 新 V-EC-01 (薬機法 一般化粧品 56 効能) = 「クライアント業界が薬機法対象か」を vertical 設定で判定

### D-3. AdTruth (ADR-006) との明確な切り分け

| 区分 | 機能 | スコープ |
|------|------|---------|
| 既存 `config/rules/adtruth_rules.yaml` (15 ルール) | 広告ログベースの不正検知 (F01-F15) | Phase A から運用中 |
| ADR-006 (Draft、Phase B Week 2-3 実装予定) | **LP 配置型** 不正検知 (Zynect 独自開発) | 本 ADR では触らない |
| 新 `config/foundation/ad_fraud_screening.yaml` (8 ルール) | **AdTruth 導入前の事前診断** (業界 fraud 率乖離 / 短時間多発クリック等) | 本 ADR で新設 |

→ **既存 AdTruth ルール (F01-F15) は無改修**。新 `ad_fraud_screening.yaml` は AdTruth 本実装の前段階の診断として独立。

### D-4. 新規ルール YAML スキーマ (既存スキーマを拡張)

既存スキーマ (severity/polarity/axis_position/root_cause_group/duplicate_factor/prerequisite) を **完全継承**し、新規フィールドを追加:

```yaml
- id: <prefix>-<連番>          # 例: F-LC-01, V-EC-01
  layer: foundation | vertical | ec_platform | precision_category
  category: <カテゴリ名>
  
  # === 既存スキーマ準拠 (Layer A 既存ルールと同じ) ===
  severity: critical | high | medium | low | info
  polarity: negative | neutral | positive
  axis_position: TO-01〜TO-11 | null
  root_cause_group: measurement_foundation | delivery_learning_or_structure |
                    creative_optimization | budget_allocation | targeting | independent
  duplicate_factor: 0.2〜1.0
  prerequisite: <既存 rule_id> | <新 rule_id> | null
  
  # === ADR-013 新規必須フィールド ===
  applies_to:
    countries: [JP]
    verticals: [all] | [ec_d2c, ...]
    ec_platforms: [all] | [ecforce, ...]
    ad_platforms: [all] | [meta, ...]
    business_models: [b2c, b2b, both]
  trigger:
    condition: "<Python eval 可能な論理式>"
    operator: AND | OR
    sub_conditions: []
  data_source:
    - source: client_state | ad_platform_api | rule_evaluation
      fields / platform / rule_ids: [...]
  cooldown_days: <int>
  daily_cap_group: default | adr_005 | adr_013_legal
  template: <jinja2_filename>
  evidence_fields: []
  legal_reference:               # 法令系のみ (null 可)
    law: "薬機法"
    article: "第66条"
    description: "誇大広告等の禁止"
    disclaimer: "指摘レベルでの提示。最終判断はクライアント法務担当者に委ねる"
  rationale: "<このルールが必要な理由を 1 文で>"
  skip_if: "<スキップ条件式>" | null
```

### D-5. ID 命名規則

```
Layer A (既存):
  G01〜G108     Google
  M01〜M70      Meta
  T01〜T46      TikTok
  S01〜S45      SEO
  F01〜F15      AdTruth
  C01〜C15      Common

Layer 0 Foundation:
  F-LC-NN       Legal Compliance
  F-PP-NN       Privacy Protection
  F-MF-NN       Measurement Foundation
  F-AH-NN       Account Health
  F-DG-NN       Data Governance
  F-OQ-NN       Operations Quality
  F-AF-NN       Ad Fraud Screening (※ AdTruth (F01-F15) と区別)
  F-SC-NN       Security Continuity

Layer 1 Vertical:
  V-EC-NN       EC/D2C
  V-SS-NN       Subscription SaaS
  V-BL-NN       B2B Lead
  V-RE-NN       Real Estate
  V-FN-NN       Finance
  V-HR-NN       HR/Recruiting
  V-HC-NN       Healthcare/Clinic
  V-ED-NN       Education
  V-LS-NN       Local Service

Layer 2 EC Platform:
  P-EF-NN       ECフォース
  P-SH-NN       Shopify
  P-BA-NN       BASE
  P-MS-NN       MakeShop
  P-FS-NN       FutureShop
  P-CU-NN       Custom

Layer 3 Precision Category:
  PC-MS-NN      Measurement
  PC-AU-NN      Audience
  PC-DQ-NN      Data Quality
  PC-CR-NN      Creative
  PC-BD-NN      Bidding
  PC-BS-NN      Brand Safety
  PC-AT-NN      Attribution
```

→ **既存 F01-F15 (AdTruth) と新 F-LC-NN (Legal Compliance) はプレフィックスで衝突なし** (F vs F-LC)。既存 ID と完全に区別可能。

### D-6. 法令系ルールの安全装置 (3 重)

法令系 (Layer 0 legal_compliance + Layer 1 vertical の業法系) は誤指摘リスクが顕在化する:

| 安全装置 | 内容 |
|---------|------|
| 1. `legal_reference` フィールド必須 | 法律名・条文番号・条文要旨を明記 |
| 2. `disclaimer` フィールド必須 | 「Zynect は法務専門家ではなく、指摘レベルの気づきとして提示」 |
| 3. 投稿テンプレート末尾の固定文 | 「最終判断はクライアント法務担当者に委ねる」を強制挿入 |

加えて `clients.yaml` に `auto_proposal.legal_disclaimer_required: true` フィールドを追加し、法令系ルール発火時のテンプレートで disclaimer を必須レンダリング (省略時はテストで FAIL)。

### D-7. データソース統合 (`data_source` フィールド)

新規ルールは 3 種類のデータソースから値を取得:

```yaml
data_source:
  # (1) クライアント状態 (ADR-012 で設計済)
  - source: client_state
    fields: [capi_setup_status, ec_platform, ecforce_access_granted]
  
  # (2) 既存 adapters 経由の広告プラットフォーム API
  - source: ad_platform_api
    platform: meta
    fields: [pixel_dormant_days, capi_emq_score, total_cost]
  
  # (3) 既存 277 ルールの評価結果 (ads_audit.py 出力)
  - source: rule_evaluation
    rule_ids: [M01, M02, M04]   # これらが現状違反かを参照
```

→ 既存実装 (`adapters/`, `analyzers/ads_audit.py`, `engine/indication_state.py`) を最大限再利用。

### D-8. 自動提案エンジンの 5 層対応 (auto_proposal_engine.py 改訂)

ADR-012 で設計した engine を以下のように拡張:

```python
def run_auto_proposal(client_id: str, dry_run: bool = False) -> dict:
    state = load_client_state(client_id)
    client_cfg = load_client_config(client_id)
    
    # 1. 全 5 層をロード (既存 ADR-012 の 7 ルールも含める)
    rules = load_all_layers()  # foundation/, verticals/, ec_platforms/, precision_categories/
    
    # 2. 環境マッチング (applies_to で絞込)
    environment_matched = filter_by_environment(rules, client_cfg)
    
    # 3. data_source 解決 (state + adapters + rule_evaluation)
    enriched = resolve_data_sources(environment_matched, client_cfg, state)
    
    # 4. trigger / prerequisite / skip_if / cooldown 評価
    eligible = evaluate_rules(enriched, state, history, today)
    
    # 5. severity + 6 グループスコアでソート
    sorted_rules = apply_severity_priority(eligible, weights)
    
    # 6. daily_cap_group ごとに上限適用
    selected = enforce_caps(sorted_rules)
    
    # 7. テンプレート rendering + ChatWork 投稿
    posted = [render_and_post(rule, state, client_cfg, dry_run) for rule in selected]
    
    return summary
```

→ ADR-012 の `auto_proposal_rules.yaml` の 7 ルールは Layer 0/1/2/3 の YAML に**移行**するか、共存。本 ADR では **共存方式** を選択 (既存 7 ルールは ADR-012 で既に動作しているため触らない)。

---

## Alternatives Considered

### A-1. 既存 277 ルールに法令・環境系を追加 (単一層維持)

**却下理由**:
- `meta_rules.yaml` 70 ルールに「薬機法」を追加すると意味的に異質
- 既存 ADR-002 の 6 root_cause_group 設計と整合しない
- ルール検索性 (どの YAML を見れば法令系があるか) が悪化

### A-2. 業界別に独立した analyzers/ サブモジュールを実装

**却下理由**:
- コード重複 (各業界で同じ trigger 評価ロジックを書く)
- YAML 駆動の宣言的設計のメリットを失う
- 業界追加コストが高い (新規 .py 実装が必要)

### A-3. legal_compliance を AI (Claude) に判定させる

**却下理由**:
- API コスト不確定
- 法令判定は再現性が必要 (同入力で同出力)
- 法務担当者がルール定義を直接編集できる方が運用しやすい

### A-4. AdTruth 既存 15 ルール (F01-F15) を Foundation 層に統合

**却下理由**:
- ADR-006 (Draft) の独立性を保つ
- 既存 `analyzers/fraud_audit.py` の動作を変えない
- F01-F15 は「広告ログ統計ベース」、F-AF-NN は「AdTruth 導入前事前診断」で性質が異なる

---

## Result (実装後の確認指標)

| 指標 | 期待値 | 計測方法 |
|------|--------|---------|
| 既存 277 ルールの動作 | **無変更**、340 件 既存テスト全 PASS 維持 | pytest |
| 新規ルール件数 (5/5 完了時点) | 約 108 ルール (Foundation 49 + Vertical EC 12 + ECフォース 8 + Precision 39) | rule loader カウント |
| Phase B 完了時点の総ルール数 | 約 527 ルール | 同上 |
| pilotton --dry-run の発火件数 | 環境マッチした 30-50 ルールが trigger 評価対象 | run_auto_proposal --dry-run |
| 法令系の disclaimer 必須化 | 100% (テンプレート末尾に固定文挿入) | テスト |
| 新規追加テスト | 20 件以上 (合計 360 PASS) | pytest |

---

## Tradeoffs / Risks

### T-1. ルール総数 277 → 527 = 1.9 倍化

- ロード時間増加リスク
- **緩和策**: lazy load (環境マッチ後にのみ data_source 解決)、起動時 1 秒以内目標

### T-2. 法令系の誤指摘リスク

- 「薬機法違反」と誤指摘して顧客の信頼を損なう
- **緩和策**: §D-6 の 3 重安全装置 + Phase A 内部レビュー期間で誤指摘率を測定

### T-3. 業界判定の精度

- `clients.yaml` の `vertical: ec_d2c` は山本さんが手動設定 → 誤分類のリスク
- **緩和策**: kickoff day で確認、`category_subtype: cosmetics_health` で薬機法対象を明示

### T-4. ルール YAML の保守コスト

- 527 ルール × 9 業界 × 6 EC プラットフォーム = 多次元の組合せが膨張
- **緩和策**: `applies_to: [all]` を default にして、必要に応じて絞込

### T-5. 既存 ADR-012 との二重管理

- ADR-012 の 7 ルールは別 YAML、本 ADR の YAML 群と二重管理
- **緩和策**: Phase B Week 1 で ADR-012 の 7 ルールを Foundation 層に**移行統合** (本 ADR では共存維持、後日リファクタ)

---

## Implementation Plan (5/5 〜 Phase B Week 3)

### 5/5 (本日中、~6h)

| 区分 | ファイル | 状態 |
|------|---------|------|
| ADR | `docs/decisions/ADR-013-multi-layer-rule-design.md` | ✅ 本ファイル |
| アーキテクチャ | `docs/architecture/rule_taxonomy.md` | ✅ 中身 |
| Foundation 完全実装 | `config/foundation/{legal_compliance,privacy_protection,measurement_foundation,account_health,data_governance}.yaml` | ✅ 中身 |
| Foundation 骨格 | `config/foundation/{operations_quality,ad_fraud_screening,security_continuity}.yaml` | ✅ 骨格 |
| Vertical 完全実装 | `config/verticals/ec_d2c.yaml` | ✅ 中身 |
| Vertical 骨格 | `config/verticals/{他 8 業界}.yaml` | ✅ 骨格 |
| EC Platform 完全実装 | `config/ec_platforms/ecforce.yaml` | ✅ 中身 |
| EC Platform 骨格 | `config/ec_platforms/{他 5}.yaml` | ✅ 骨格 |
| Precision 完全実装 | `config/precision_categories/{measurement,audience,data_quality}.yaml` | ✅ 中身 |
| Precision 骨格 | `config/precision_categories/{creative,bidding,brand_safety,attribution}.yaml` | ✅ 骨格 |
| エンジン改訂 | `engine/auto_proposal_engine.py` (5 層対応 loader + filter) | ✅ |
| クライアント設定 | `config/clients.yaml` pilotton 拡張 (vertical/ec_platform/category_subtype) | ✅ |
| 提案資料更新 | `docs/proposals/pilotton/proposal_v3_supplement_auto_engine.md` 改訂 | ✅ |
| テスト | `tests/test_rule_loader_multi_layer.py` (20 ケース) | ✅ |

### Phase B Week 1 (5/14-5/17)

- 残り Vertical 8 ファイルの中身実装 (薬機法/宅建業法/金商法/医療広告ガイドライン等)
- 残り Precision 4 ファイルの中身実装

### Phase B Week 2 (5/20-5/24)

- 残り EC Platform 5 ファイルの中身実装 (Shopify / BASE / MakeShop / FutureShop / Custom)
- ADR-012 の 7 ルールを Foundation 層に移行統合

### Phase B Week 3 (5/27-5/31)

- 全層完成、527 ルール体制で運用開始
- ADR-006 AdTruth LP 配置検知の実装と統合

---

## References

- ADR-001: [3 層インパクト表示](./ADR-001-three-layer-impact-display.md)
- ADR-002: [6 root_cause_group](./ADR-002-six-root-cause-groups.md)
- ADR-003: [pixel_health 連動](./ADR-003-pixel-health-coupling.md)
- ADR-004: [CV 正規化](./ADR-004-cv-normalization-and-conversion-mapping.md)
- ADR-005: [ChatWork ループ](./ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-006 (Draft): AdTruth LP 配置検知 (Phase B Week 2-3 実装予定)
- ADR-011: [ChatWork 自動通知グループ](./ADR-011-chatwork-auto-notification-group.md)
- ADR-012: [自動提案エンジン](./ADR-012-auto-proposal-engine.md)
- 詳細仕様: `docs/architecture/rule_taxonomy.md`
- 既存ルール定義: `config/rules/`
