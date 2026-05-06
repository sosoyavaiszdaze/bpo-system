# ADR-015: クライアント MarTech スタック総合管理

- **Status**: Accepted (Phase A 一次実装、5/7 着手)
- **Date**: 2026-05-07
- **Deciders**: Zynect Media
- **Related**: ADR-013 (5 層ルール), ADR-012 (auto_proposal_engine), ADR-005 (ChatWork 自動通知)

## 1. Context

ADR-013 で導入した applies_to は `ec_platforms` カテゴリしかカバーしておらず、クライアントの
MarTech スタック (tag_manager / analytics / ma / crm / cdp / ad_platforms / capi_status /
ab_testing / chatbot) の他カテゴリは未管理。

そのため、

- pilotton が GTM を使っているか直書きかも分からないまま GTM 前提のルール (例: `dataLayer` 命名規約)
  を毎日通知してしまう
- HubSpot を使っていないクライアントに HubSpot 設定指摘を送ってしまう
- CDP 不在のクライアントに「Treasure Data 連携の dedup_key 設定」を指摘してしまう

といった「**運用者の環境に存在しないツールの指摘**」が日次 ChatWork に積み上がり、Phase A の
自動化体験そのものを破壊する。

業界では SmartBug Media / marketingops.com 等の MarTech Audit Template が「カテゴリ × ツール
選択肢のマトリクス」として確立しており、検出手段は Wappalyzer 型 (HTTP / HTML / Cookie シグナル)
+ ヒアリング型 (質問票) のハイブリッドが標準。

## 2. Decision

クライアント MarTech スタック全体を Zynect 側で **宣言 + 自動検証 + ヒアリング補完** の 3 段で
把握し、ルールの applies_to にスタックカテゴリを許容することで「環境に存在するツールの指摘
だけ」を顧客に届ける。

### 2.1 データモデル: client_tech_stack

`config/clients.yaml` の各クライアント直下に `tech_stack` セクションを新設。

```yaml
pilotton:
  tech_stack:
    ec_platform:  { value: ecforce, confidence: high,   last_verified: 2026-05-07, source: declared }
    tag_manager:  { value: gtm,     confidence: medium, last_verified: 2026-05-07, source: detected }
    analytics:    [ga4]                         # リスト形式は複数併用想定 (GA4 + 何か)
    cdp:          { value: none,    confidence: medium, last_verified: 2026-05-07, source: hearing }
    ma:           { value: unknown, confidence: low,    last_verified: null,        source: pending_hearing }
    crm:          { value: unknown, confidence: low,    last_verified: null,        source: pending_hearing }
    ad_platforms: [meta]                        # clients.yaml の ad_platforms と整合 (重複保持)
    capi_status:
      meta:   enabled
      google: not_configured
    ab_testing:   { value: unknown, confidence: low,  source: pending_hearing }
    chatbot:      { value: unknown, confidence: low,  source: pending_hearing }
```

各エントリは以下のいずれか:
- 単一値: `{ value, confidence, last_verified, source }`
- リスト: `[<value>, ...]` (analytics / ad_platforms 等の複数併用)
- ネスト: `capi_status: { <platform>: <status> }`

`confidence` 取りうる値: `high` / `medium` / `low`
`source` 取りうる値: `declared` (顧客から直接ヒア)、`detected` (自動検出)、`hearing` (ChatWork 質問回答)、`pending_hearing` (未確認、要質問)

### 2.2 シグネチャライブラリ

`config/tech_stack_signatures.yaml` を新設。Wappalyzer の technologies.json を参考に、各カテゴリ
× ツールに対して **strong / medium / weak シグナル** を定義。検出ルールは以下の 6 種類:

- `http_header:<key>` (例: `X-ShopId`)
- `html_contains:<substring>` (例: `googletagmanager.com/gtm.js`)
- `js_global:<varname>` (例: `Shopify`, `dataLayer`)
- `cookie:<name>` (例: `_shopify_y`)
- `domain_regex:<regex>` (例: `\.myshopify\.com$`)
- `url_path_regex:<regex>` (例: `/lp\?u=`)

判定ルール:
- strong シグナル 1 件マッチ → confidence = `high`
- medium シグナル 2 件以上マッチ → confidence = `medium`
- weak シグナルのみ → confidence = `low`
- 完全マッチなし → カテゴリ「未検出」(=ヒアリング推奨)

### 2.3 validator 実装

`validators/client_tech_stack_validator.py`:

```python
def validate_client_tech_stack(client_id: str) -> dict:
    """
    1. clients.yaml から LP / サンクスページ URL を取得
    2. urllib (or playwright) で HEAD/GET → response_text と headers と cookies を取得
    3. tech_stack_signatures.yaml と照合 → カテゴリごとの検出結果を生成
    4. clients.yaml の宣言値と突合 → 4 状態判定:
       - 一致 (declared == detected): confidence 維持 / 上昇
       - 検出のみ (declared==unknown && detected!=unknown): 「未宣言だが検出」を社内通知
       - 宣言のみ (declared!=unknown && detected==unknown): 「宣言されているが検出できず」社内通知
       - 不一致 (declared != detected): 警告、該当カテゴリ依存ルール抑止
    5. 検証履歴を outputs/{client_id}/tech_stack_verification.yaml に append
    6. 同期ずれは社内 ChatWork に通知
    """
```

### 2.4 評価エンジン側の対応

`engine/auto_proposal_engine.py` の `_filter_by_environment()` を拡張し、ルール YAML の
`applies_to` に以下のキーを許容:

- `ec_platforms`, `tag_managers`, `analytics_platforms`, `mas`, `crms`, `cdps`,
  `ad_platforms`, `capi_status`, `ab_testing_tools`, `chatbots`

各キーは現状の `verticals` / `business_models` 等と同じ `_match_list()` ロジックで突合。

**フェイルセーフ**:
- スタック値が `unknown` または confidence=`low` のカテゴリに依存するルールは **評価対象から除外**
- 除外件数を社内 ChatWork に集約通知 (例: "pilotton: ma 設定未確認、MA 系 18 ルールをスキップ")

### 2.5 ChatWork オンボーディング自動質問

`scripts/onboarding_chatwork_parser.py`:

1. `tech_stack` 内 `confidence: low` または `source: pending_hearing` のカテゴリを抽出
2. カテゴリごとに A/B/C/D 形式の質問を生成 (例: 「お使いの CRM を教えてください: A) Salesforce / B) HubSpot / C) kintone / D) 自社開発 / E) なし / F) その他」)
3. ChatWork 投稿 (1 質問 1 メッセージ、idempotency_key は category 名 + 質問日)
4. ChatWork API のメッセージ取得で回答パース (A/B/C/D の単一文字 or キーワード一致)
5. `clients.yaml` を atomic write で更新、`source: hearing`、`confidence: medium`

Phase A は枠組み作成のみ。本格運用は 5/8 以降。

### 2.6 既存ルールの applies_to 棚卸し

`docs/architecture/rule_stack_dependency_matrix.md` に「rule_id × 必要スタックカテゴリ」の
マトリクスを記録。Phase A 5/7 中は**マトリクスのみ作成**、各ルールの applies_to 書き換えは
5/8-5/14 で順次実施。

### 2.7 監査ログ

`outputs/{client_id}/tech_stack_verification.yaml` に検証履歴 (timestamps + signal hits +
4状態判定 + 社内通知済 flag) を append。提案資料で「Zynect は毎週、お客様の MarTech 環境を
自動検証します」という差別化材料に活用。

### 2.8 段階的検出カテゴリ

| Phase | カテゴリ | 検出主体 |
|---|---|---|
| **A 今日 (5/7)** | ec_platform / tag_manager / analytics / ad_platforms / capi_status | 自動検出 |
| A 今週 (5/8〜) | ma / crm / cdp / ab_testing / chatbot | ヒアリング |
| B Week 2-3 | 検出精度向上、独自ツール対応、API 認証付き深堀検証 | 自動 + API |

## 3. 設計原則 (Phase A 厳守)

- **不確実な情報で間違った指摘を出さない** (フェイルセーフ最優先)
- **自動検出できないものはヒアリングで補完** (推測しない)
- **検出履歴を蓄積し提案資料の差別化材料に活用** (時系列の継続性が価値)
- **既存 ADR-013 の 5 層構造とは整合** (applies_to 拡張のみ、ルール体系自体は変えない)

## 4. 影響

- 既存ルール: 5/7 時点では applies_to 未拡張のため評価動作は不変。マトリクス作成と
  evaluator の拡張だけ先行する形 (互換性維持)。
- 新規ルール: 今後追加されるルールは applies_to にスタックカテゴリを必須記入とする。
- 顧客通知: 5/7 提案以降、pilotton ChatWork に「環境にないツールの指摘」が混入しなくなる。

## 5. テスト方針

- 単体: tech_stack_signatures.yaml の各シグナルが期待通りマッチすること (mock HTML)
- 統合: pilotton の LP に対する validator 実行で ec_platform=ecforce が一致と判定されること
- evaluator: applies_to に新カテゴリを持つ擬似ルールが、tech_stack に応じて in/out 判定されること
- 既存 397 PASS 維持

## 6. Phase B 拡張予定

- **API 認証つき深堀検証**: GA4 Property Settings API、HubSpot Portal API 等で内部設定を直接読取
- **動的 SPA 対応**: Playwright で JS 描画後の DOM を捕捉
- **MarTech カバレッジスコア**: 業界平均比でクライアントのスタック充足度を可視化 (提案資料用)

## 7. 関連ファイル

- `config/clients.yaml` (tech_stack 追加)
- `config/tech_stack_signatures.yaml` (新規)
- `validators/client_tech_stack_validator.py` (新規)
- `engine/auto_proposal_engine.py` (`_filter_by_environment` 拡張)
- `scripts/onboarding_chatwork_parser.py` (新規、Phase A 枠組みのみ)
- `outputs/{client_id}/tech_stack_verification.yaml` (出力)
- `docs/architecture/rule_stack_dependency_matrix.md` (新規、棚卸しマトリクス)
