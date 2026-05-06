# ルール × MarTech スタック依存マトリクス

ADR-015 §2.6 棚卸しドキュメント。各ルールがどのスタックカテゴリに依存するかを整理し、`applies_to` 拡張時の参照表として使う。

- **作成日**: 2026-05-07 (5/7 提案前 一次版)
- **対象**: `config/rules/*.yaml` (Layer A 277 ルール) + `config/foundation/*.yaml` + `config/verticals/*.yaml` + `config/ec_platforms/*.yaml` + `config/precision_categories/*.yaml`
- **方針**: 5/7 はマトリクス作成のみ、各ルール YAML の `applies_to` 拡張書換は 5/8〜5/14 で順次

## 1. 依存カテゴリの定義 (ADR-015)

| カテゴリキー (applies_to) | 対応 tech_stack 値 | スキップ条件 (confidence:low) |
|---|---|---|
| `ec_platforms` | tech_stack.ec_platform.value | スキップ |
| `tag_managers` | tech_stack.tag_manager.value | スキップ |
| `analytics_platforms` | tech_stack.analytics (list) | スキップ |
| `mas` | tech_stack.ma.value | スキップ |
| `crms` | tech_stack.crm.value | スキップ |
| `cdps` | tech_stack.cdp.value | スキップ |
| `ad_platforms` | tech_stack.ad_platforms (list) | (常にハイ confidence、スキップ対象外) |
| `capi_status` | tech_stack.capi_status (dict) | platform 単位で評価 |
| `ab_testing_tools` | tech_stack.ab_testing.value | スキップ |
| `chatbots` | tech_stack.chatbot.value | スキップ |

## 2. Layer A (既存 277 ルール) の主依存パターン

### 2.1 Google Ads ルール (108 件、`config/rules/google_rules.yaml`)
| ルール ID パターン | 主依存カテゴリ | 備考 |
|---|---|---|
| G01-G15 (アカウント基盤) | `ad_platforms: [google]` | 既存 applies_to 同等 |
| G16-G40 (キーワード/RSA/PMax) | `ad_platforms: [google]` | |
| G41-G65 (CV計測/Enhanced CV) | `ad_platforms: [google]`, `capi_status: {google: ...}` | Enhanced Conversions の有無で分岐 |
| G66-G108 (拡張機能) | `ad_platforms: [google]` | |

### 2.2 Meta ルール (70 件、`config/rules/meta_rules.yaml`)
| ルール ID パターン | 主依存カテゴリ | 備考 |
|---|---|---|
| M01 (Pixel 設置) | `ad_platforms: [meta]` | tag_managers 不問 |
| M02 (CAPI) | `ad_platforms: [meta]`, `capi_status: {meta: not_configured}` | **CAPI 未設定時のみ指摘** |
| M03-M10 (Pixel 健全性) | `ad_platforms: [meta]` | |
| M11-M40 (Audience/Creative) | `ad_platforms: [meta]` | |
| M41-M65 (Advantage+/最適化) | `ad_platforms: [meta]` | |
| M62 (アトリビューション) | `ad_platforms: [meta]`, `analytics_platforms: [ga4, adobe_analytics, ...]` | analytics 必須 |

### 2.3 TikTok ルール (46 件) — `ad_platforms: [tiktok]` で統一

### 2.4 SEO ルール (45 件) — `ad_platforms` 非依存、`analytics_platforms` 推奨

### 2.5 AdTruth Fraud ルール (15 件) — `ad_platforms: [meta, google, tiktok]`

## 3. Foundation 層 (Layer 0、約 70 ルール、`config/foundation/*.yaml`)

| サブ層 | ファイル | 主依存カテゴリ |
|---|---|---|
| measurement_foundation | `config/foundation/measurement_foundation.yaml` | `tag_managers`, `analytics_platforms`, `ad_platforms` |
| account_health | `config/foundation/account_health.yaml` | `ad_platforms` |
| ad_fraud_screening | `config/foundation/ad_fraud_screening.yaml` | `ad_platforms` |
| data_governance | `config/foundation/data_governance.yaml` | (横断、tech_stack 全般依存) |
| privacy_protection | `config/foundation/privacy_protection.yaml` | `analytics_platforms`, `tag_managers` |
| legal_compliance | `config/foundation/legal_compliance.yaml` | (横断、ec_platforms 一部依存) |
| security_continuity | `config/foundation/security_continuity.yaml` | `ad_platforms` |
| operations_quality | `config/foundation/operations_quality.yaml` | (横断) |

**重要 trigger フィールド** (Foundation 層で頻出する client_state 依存):

- `cv_dedupe_key_implemented` ← `tag_managers + capi_status` 依存
- `cmp_implemented` ← `tag_managers` 依存
- `ga` プレフィクスの client_state ← `analytics_platforms: [ga4]` 依存

## 4. Vertical 層 (Layer 1、約 70 ルール、`config/verticals/*.yaml`)

| ファイル | 主依存カテゴリ |
|---|---|
| `ec_d2c.yaml` | `ec_platforms`, `verticals: [ec_d2c]` |
| `subscription_saas.yaml` | `verticals: [subscription_saas]`, `crms` (顧客管理 SaaS) |
| `btob_lead.yaml` | `mas` (リード育成必須), `crms` |
| `healthcare_clinic.yaml` | `verticals: [healthcare_clinic]`, `legal_compliance` 横断 |
| `finance.yaml` | `verticals: [finance]`, `legal_compliance` 横断 |
| `education.yaml` | `verticals: [education]` |
| `hr_recruiting.yaml` | `verticals: [hr_recruiting]` |
| `local_service.yaml` | `verticals: [local_service]` |
| `real_estate.yaml` | `verticals: [real_estate]` |

## 5. EC Platform 層 (Layer 2、約 50 ルール、`config/ec_platforms/*.yaml`)

| ファイル | 主依存カテゴリ |
|---|---|
| `ecforce.yaml` (P-EF-*) | `ec_platforms: [ecforce]` |
| `shopify.yaml` (P-SH-*) | `ec_platforms: [shopify]` |
| `makeshop.yaml` (P-MS-*) | `ec_platforms: [makeshop]` |
| `futureshop.yaml` (P-FS-*) | `ec_platforms: [futureshop]` |
| `custom.yaml` (P-CU-*) | `ec_platforms: [custom]` |
| `base.yaml` (P-BS-*) | `ec_platforms: [all]` (Layer 共通) |

## 6. Precision Category 層 (Layer 3、約 60 ルール、`config/precision_categories/*.yaml`)

| ファイル | 主依存カテゴリ |
|---|---|
| `attribution.yaml` | `analytics_platforms`, `capi_status` |
| `audience.yaml` | `mas`, `cdps` |
| `bidding.yaml` | `ad_platforms` |
| `brand_safety.yaml` | `ad_platforms` |
| `creative.yaml` | `ad_platforms`, `ab_testing_tools` |
| `data_quality.yaml` | `tag_managers`, `analytics_platforms` |
| `measurement.yaml` | `tag_managers`, `analytics_platforms`, `capi_status` |

## 7. 5/7 提案前 暫定ルール

5/7 提案までに `applies_to` 書換が完了するルール:

- なし (今日はマトリクスのみ)

5/8〜5/14 で順次書換予定 (優先順):

1. **MA/CRM/CDP 依存**: `mas` / `crms` / `cdps` フィールド追加 (約 40 ルール)
2. **CAPI 状態依存**: `capi_status` 追加 (M02, G42, T03 等 約 15 ルール)
3. **Analytics 依存**: `analytics_platforms` 追加 (M62, S系 約 20 ルール)
4. **Tag Manager 依存**: `tag_managers` 追加 (Foundation 層 約 30 ルール)

## 8. 既存挙動への影響

**5/7 時点では既存挙動は変わらない**。理由:

- evaluator (`engine/auto_proposal_engine.py:_filter_by_environment`) は新カテゴリの applies_to が「無い場合」は常にマッチ (`["all"]` 互換扱い)
- 既存 ルール YAML には新カテゴリ未指定 → 全て通過
- 既存 397 PASS テストも維持される

**5/8 以降の書換時**は、各カテゴリで顧客 tech_stack が `confidence: low` の場合に該当ルールがスキップされ、ChatWork 通知の精度が向上する。

## 9. 関連ファイル

- `docs/decisions/ADR-015-client-tech-stack-management.md` (本マトリクスの母 ADR)
- `config/tech_stack_signatures.yaml` (シグネチャライブラリ)
- `validators/client_tech_stack_validator.py` (自動検出 validator)
- `engine/auto_proposal_engine.py` (`_filter_by_environment` 拡張)
