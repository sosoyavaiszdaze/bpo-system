# Rule Taxonomy: 5 層ルール体系の俯瞰マトリクス

| 項目 | 値 |
|------|---|
| 作成日 | 2026-05-05 |
| 関連 ADR | ADR-013 (本体), ADR-001/002/003 (既存スキーマ参照) |

---

## 1. 5 層構造の全体像

```
                                                       ┌──────────────────────┐
   既存 277 ルール (Layer A)                          │ analyzers/ads_audit  │
   ───────────────────────                            │  → KPI 数値診断        │
   google_rules.yaml      108                          └──────────┬───────────┘
   meta_rules.yaml         70                                     │
   tiktok_rules.yaml       46                                     │
   seo_rules.yaml          45                                     │
   adtruth_rules.yaml      15  (F01-F15 既存維持)                 │
   common_rules.yaml       15                                     ▼
                                                       ┌──────────────────────┐
                                                       │ engine/scorer.py +   │
                                                       │ priority_ranker.py   │
                                                       └──────────────────────┘

   ─────────────────────────  本 ADR-013 で増設 (約 250 ルール) ─────────────────────

   Layer 0: Foundation (8 ファイル / ~70 ルール)              ┌───────────────────────┐
   ─────────────────────────                                  │ engine/                │
   F-LC-NN  legal_compliance       12 ★中身                   │ auto_proposal_engine   │
   F-PP-NN  privacy_protection     10 ★中身                   │  → 環境/法令/基盤診断   │
   F-MF-NN  measurement_foundation 12 ★中身                   │  → 環境マッチ          │
   F-AH-NN  account_health          8 ★中身                   │  → ChatWork 自動投稿   │
   F-DG-NN  data_governance         7 ★中身                   └───────────────────────┘
   F-OQ-NN  operations_quality      6 □骨格
   F-AF-NN  ad_fraud_screening      8 □骨格
   F-SC-NN  security_continuity     5 □骨格

   Layer 1: Vertical (9 ファイル / ~70 ルール)
   ─────────────────────────
   V-EC-NN  ec_d2c                 12 ★中身
   V-SS-NN  subscription_saas      10 □骨格
   V-BL-NN  btob_lead              10 □骨格
   V-RE-NN  real_estate             8 □骨格
   V-FN-NN  finance                 8 □骨格
   V-HR-NN  hr_recruiting           6 □骨格
   V-HC-NN  healthcare_clinic       6 □骨格
   V-ED-NN  education               5 □骨格
   V-LS-NN  local_service           5 □骨格

   Layer 2: EC Platform (6 ファイル / ~30 ルール)
   ─────────────────────────
   P-EF-NN  ecforce                 8 ★中身
   P-SH-NN  shopify                 5 □骨格
   P-BA-NN  base                    5 □骨格
   P-MS-NN  makeshop                5 □骨格
   P-FS-NN  futureshop              4 □骨格
   P-CU-NN  custom                  3 □骨格

   Layer 3: Precision Category (7 ファイル / ~80 ルール)
   ─────────────────────────
   PC-MS-NN measurement            15 ★中身
   PC-AU-NN audience               12 ★中身
   PC-DQ-NN data_quality           12 ★中身
   PC-CR-NN creative               12 □骨格
   PC-BD-NN bidding                10 □骨格
   PC-BS-NN brand_safety           10 □骨格
   PC-AT-NN attribution             9 □骨格

★中身: 5/5 中に内容実装    □骨格: ID とカテゴリのみ、Phase B Week 1-3 で中身追加
```

---

## 2. 既存 root_cause_group との対応表

ADR-002 で確立した 6 グループに、新規ルールも分類する:

| root_cause_group | 既存 (Layer A) | 新規 Foundation (Layer 0) | 新規 Vertical (Layer 1) | 新規 Precision (Layer 3) | duplicate_factor |
|---|---|---|---|---|---|
| measurement_foundation | M01-M10 等 | F-MF-* (大半) / F-DG-* (一部) | V-EC-04, V-SS-* (LTV計測) | PC-MS-*, PC-DQ-*, PC-AT-* | 0.2 (休眠時 0.1) |
| delivery_learning_or_structure | M21-M28 等 | — | V-EC-06, V-SS-* (学習) | PC-BD-* (一部) | 0.3 |
| creative_optimization | M40-M50 等 | F-LC-* (一部、誇大広告系) | V-EC-12 (レビュー), V-RE-* (画像) | PC-CR-* | 0.5 |
| budget_allocation | M60-M65 等 | — | — | PC-BD-* (大半) | 0.4 |
| targeting | M51-M58 等 | F-DG-* (1st party) | V-EC-09, V-RE-* (地域) | PC-AU-* | 0.4 |
| independent | F01-F15, X-PI* | F-LC-* (大半、法令) / F-AH-* / F-SC-* / F-AF-* | V-* (法令系全般) | PC-BS-* | 1.0 |

→ 法令系は **independent** (他要因と独立) で `duplicate_factor: 1.0`、計測系は既存通り。

---

## 3. 11 軸トレードオフ (axis_position) との対応

ADR-002/既存 tradeoff_axes.yaml の 11 軸 (TO-01〜TO-11) に新規ルールも紐付け可能:

| 軸 | 軸名 | 新規ルール例 |
|---|------|------------|
| TO-01 | 構造の粒度 | V-EC-09 (クロスセル/アップセル計測の構造) |
| TO-02 | 学習シグナル | F-MF-08 (CV 定義), PC-MS-* (重複/欠損計測) |
| TO-04 | 評価対象 | PC-AT-* (アトリビューション) |
| TO-06 | クリエイティブ-LP | F-LC-03 (打消し表示の視認性) |
| TO-10 | 時間軸 | V-EC-04 (LTV 連動 = 長期最適化) |
| TO-11 | コントロール権 | F-MF-07 (サーバーサイド GTM = 自動化) |
| **null** | (法令系は軸対象外) | F-LC-*, V-*-法令系 |

→ **法令系 (legal_compliance + vertical 業法系) は `axis_position: null`** が原則。米満氏理論のトレードオフではなく、二者択一でない遵守事項のため。

---

## 4. severity 分布 (新規ルール想定)

| severity | 件数想定 | 用途 |
|----------|---------|------|
| critical | ~30 件 | 法令違反 (薬機法 / 景表法 / 業法) |
| high | ~80 件 | 計測基盤の重大な欠落、CMP 未導入等 |
| medium | ~100 件 | ベストプラクティス未実施 |
| low | ~30 件 | 改善余地レベル |
| info | ~10 件 | 確認推奨 (任意) |

ADR-005 の severity フィルタ (critical + high のみ通知) と整合。

---

## 5. applies_to による環境マッチング

新規スキーマの `applies_to` フィールドで「どのクライアントに適用するか」を判定:

```yaml
applies_to:
  countries: [JP]                   # 国コード
  verticals: [ec_d2c]                # 業界 (clients.yaml の vertical と一致)
  ec_platforms: [ecforce]            # EC プラットフォーム
  ad_platforms: [meta]               # 広告媒体
  business_models: [b2c]             # ビジネスモデル
```

| keyword | 意味 |
|---------|------|
| `[all]` | 全マッチ (フィールド省略可) |
| `[A, B]` | A or B のいずれか (OR) |

例: pilotton (vertical=ec_d2c, ec_platform=ecforce, ad_platforms=[meta]) には:
- `applies_to.verticals: [all]` → 全 Foundation ルールが対象
- `applies_to.verticals: [ec_d2c]` → V-EC-* が対象
- `applies_to.ec_platforms: [ecforce]` → P-EF-* が対象
- `applies_to.verticals: [subscription_saas]` → 対象外 (環境ミスマッチ)

---

## 6. data_source の 3 系統

### 系統 1: client_state (ADR-012 で確立)

```yaml
data_source:
  - source: client_state
    fields: [capi_setup_status, ec_platform, ecforce_access_granted, vertical]
```

`outputs/client_state/{client_id}.yaml` を読込み。Phase A 〜 B では手動更新メイン。

### 系統 2: ad_platform_api (既存 adapters 経由)

```yaml
data_source:
  - source: ad_platform_api
    platform: meta
    fields: [pixel_dormant_days, capi_emq_score, total_cost, total_conversions]
```

`adapters/meta_adapter.py` 等で取得済の最新データを参照。pipeline.py の出力を再利用。

### 系統 3: rule_evaluation (既存 277 ルール評価結果)

```yaml
data_source:
  - source: rule_evaluation
    rule_ids: [M01, M02, M04]
```

`analyzers/ads_audit.py` の出力 `audit.issues[]` を参照。「既存 M02 が違反状態か」を新規ルールから条件参照可能。

---

## 7. trigger 条件式 (Python eval ベース)

```yaml
trigger:
  condition: "client_state.ec_platform == 'ecforce' and client_state.ecforce_access_granted == False"
  operator: AND
  sub_conditions: []
```

- Python の `eval()` を **限定 namespace で安全実行**
  - 利用可能名前空間: `client_state` / `ad_platform_data` / `rule_evaluation`
  - 禁止: `__import__`, `eval`, `exec`, `os`, `sys` 等
- 複雑な条件は `sub_conditions` に分割可能

---

## 8. cooldown_days 設計 (ADR-005 + ADR-012 と整合)

```yaml
cooldown_days: 7    # 同一 rule_id を 7 日に 1 回まで投稿
daily_cap_group: default | adr_005 | adr_013_legal
```

**daily_cap_group**:
- `default`: 既存 ADR-005 の指摘 cap (3 件/日) 枠を共有
- `adr_005`: 完了通知系
- `adr_013_legal`: 法令系専用枠 (1 件/日、緊急時のみ)

法令系は誤指摘の影響が大きいため別枠で抑制。

---

## 9. 法令系ルール特例 (legal_reference + disclaimer)

```yaml
legal_reference:
  law: "景品表示法"
  article: "第5条第1号"
  description: "優良誤認表示の禁止"
  disclaimer: |
    Zynect Media は法律の専門家ではなく、本指摘は気づきレベルでの提示です。
    最終的な遵守判断はクライアントの法務担当者にご委ねください。
```

→ **テンプレート末尾に `legal_reference.disclaimer` を強制レンダリング** (ADR-013 D-6)。

---

## 10. 命名規則早見表

```
Layer A (既存 277 ルール、無変更):
  G##  Google Ads     M##  Meta Ads      T##  TikTok Ads
  S##  SEO            F##  AdTruth (15)  C##  Common

Layer 0 Foundation (8 カテゴリ):
  F-LC-##  Legal Compliance         F-AH-##  Account Health
  F-PP-##  Privacy Protection       F-DG-##  Data Governance
  F-MF-##  Measurement Foundation   F-OQ-##  Operations Quality
  F-AF-##  Ad Fraud Screening       F-SC-##  Security Continuity
  ↑ Foundation の F は既存 AdTruth F## と「-」で区別 (F01 vs F-LC-01)

Layer 1 Vertical (9 業界):
  V-EC-##  EC/D2C                   V-FN-##  Finance
  V-SS-##  Subscription SaaS        V-HR-##  HR/Recruiting
  V-BL-##  B2B Lead                 V-HC-##  Healthcare/Clinic
  V-RE-##  Real Estate              V-ED-##  Education
                                    V-LS-##  Local Service

Layer 2 EC Platform (6 プラットフォーム):
  P-EF-##  ECフォース    P-SH-##  Shopify    P-BA-##  BASE
  P-MS-##  MakeShop      P-FS-##  FutureShop P-CU-##  Custom

Layer 3 Precision Category (7 カテゴリ):
  PC-MS-##  Measurement  PC-AU-##  Audience   PC-DQ-##  Data Quality
  PC-CR-##  Creative     PC-BD-##  Bidding    PC-BS-##  Brand Safety
  PC-AT-##  Attribution
```

---

## 11. ファイル配置

```
config/
├── rules/                      ← Layer A (既存、無改修)
│   ├── google_rules.yaml
│   ├── meta_rules.yaml
│   ├── tiktok_rules.yaml
│   ├── seo_rules.yaml
│   ├── adtruth_rules.yaml
│   └── common_rules.yaml
│
├── foundation/                 ← Layer 0 (新規)
│   ├── legal_compliance.yaml          ★中身
│   ├── privacy_protection.yaml        ★中身
│   ├── measurement_foundation.yaml    ★中身
│   ├── account_health.yaml            ★中身
│   ├── data_governance.yaml           ★中身
│   ├── operations_quality.yaml        □骨格
│   ├── ad_fraud_screening.yaml        □骨格
│   └── security_continuity.yaml       □骨格
│
├── verticals/                  ← Layer 1 (新規)
│   ├── ec_d2c.yaml                    ★中身
│   ├── subscription_saas.yaml         □骨格
│   ├── btob_lead.yaml                 □骨格
│   ├── real_estate.yaml               □骨格
│   ├── finance.yaml                   □骨格
│   ├── hr_recruiting.yaml             □骨格
│   ├── healthcare_clinic.yaml         □骨格
│   ├── education.yaml                 □骨格
│   └── local_service.yaml             □骨格
│
├── ec_platforms/               ← Layer 2 (新規)
│   ├── ecforce.yaml                   ★中身
│   └── {他 5}.yaml                    □骨格
│
└── precision_categories/       ← Layer 3 (新規)
    ├── measurement.yaml               ★中身
    ├── audience.yaml                  ★中身
    ├── data_quality.yaml              ★中身
    └── {他 4}.yaml                    □骨格
```

---

## 12. References

- ADR-013: [本体](../decisions/ADR-013-multi-layer-rule-design.md)
- 既存スキーマ: `config/rules/meta_rules.yaml` の定義を参照
- 既存軸定義: `config/rules/tradeoff_axes.yaml` (11 軸)
- 既存重み: `config/priority_weights.yaml` (6 グループ + duplicate_factor)
