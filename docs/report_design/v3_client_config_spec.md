# レポート v3 — クライアント設定ファイル仕様改修案

> **対象**: `config/clients.yaml` のスキーマ拡張提案
> **作成日**: 2026-05-01
> **目的**: v3 レポートに必要な企業情報・担当者情報・業界情報をクライアント単位で管理できるようにする
> **読者**: 実装担当 / クライアント設定オペレーター / オンボーディング担当

---

## 改修の背景

### 現行 `config/clients.yaml` の問題点

```yaml
# 現行
clients:
  yamamoto_demo:
    name: "山本デモ"      # ← レポートに「山本デモ 様」と表示される
    active: true
    objective: balanced
    ads:
      meta: ...
      tiktok: ...
      google: ...
    seo: ...
    adtruth: ...
    notifications: ...
    intent_overrides: ...
    crm: ...
```

### 問題1: 企業名と担当者名が分離されていない
- `name` フィールドが何を指すのか不明（企業名？プロジェクト名？担当者名？）
- v2 レポートでは「Prepared for: 山本デモ 様」と機械的に出力していた

### 問題2: 敬称が固定
- 法人宛 → 「御中」必要
- 個人宛 → 「様」必要
- 部署宛 → 「御中」または「各位」
- 切り替え不能

### 問題3: 業界情報がない
- v3 では業界平均との比較が必須機能
- 業界が未指定では「SaaS 業界平均65点に対して...」のような3軸比較ができない

### 問題4: ロゴ・ブランドカラーがない
- v3 表紙に企業ロゴを入れたい場合の余地がない（将来拡張）

---

## v3 提案スキーマ

### 拡張後の `clients:` ブロック

```yaml
clients:
  yamamoto_demo:
    # === v3 新設: クライアント識別情報 ===
    company:
      name: "株式会社山本"                    # 企業正式名称
      honorific: "御中"                        # 御中 / 様 / なし
      industry: "saas"                         # benchmarks.yaml と紐付け
      industry_label: "SaaS"                   # レポート表示用
      logo_path: "config/logos/yamamoto.png"  # 任意。表紙ロゴ用（将来）
      brand_color: "#1a1a1a"                   # 任意。ヘッダー差し色（将来）

    contact:
      name: "山本 太郎"                        # 担当者氏名
      honorific: "様"                          # 様 / 殿 / 先生
      title: "マーケティング部 部長"           # 任意。役職
      email: "yamamoto@example.com"            # 任意。CC送付用

    # === v3 新設: レポート設定 ===
    report:
      display_name: "広告アカウント健康診断レポート"  # タイトル上書き（任意）
      include_zynect_insights: true                  # P7 セクションを含むか
      include_appendix: true                          # P8 付録を含むか
      premium_model_for_insights: true               # Zynect Insights のみ Opus 使用
      report_period_days: 30                          # レポート期間（デフォルト30日）

    # === 既存フィールド（変更なし）===
    active: true
    objective: balanced
    ads:
      meta:
        account_id: "act_916507360736121"
        access_token_env: "META_ACCESS_TOKEN_BANDAL"
        lookback_days: 90
      tiktok:
        advertiser_id: "XXXXXXXXX"
        access_token_env: "TIKTOK_ACCESS_TOKEN_YAMAMOTO"
      google:
        customer_id: "XXX-XXX-XXXX"
        developer_token_env: "GOOGLE_ADS_DEVELOPER_TOKEN"
        credentials_path: "config/google-ads.yaml"
    seo:
      site_url: "https://example.com"
      landing_pages:
        - "https://example.com/lp1"
    adtruth:
      enabled: false
      api_key_env: "ADTRUTH_API_KEY_YAMAMOTO"
    notifications:
      slack:
        webhook_env: "SLACK_WEBHOOK_YAMAMOTO"
        channel: "#yamamoto-ads"
    intent_overrides:
      - rule_ids: ["C05", "C06"]
        reason: "テスト期間中のため意図的に低予算設定"
        registered_by: "運用担当"
        registered_at: "2026-04-30"
        expires_at: "2026-07-31"
        suppress_action: "skip_notification"
    crm:
      twenty:
        enabled: true
        company_id: ""

    # === 後方互換用（廃止予定）===
    # name: "山本デモ"  ← v3 では company.name に置換。一定期間は両方読み取り可。
```

---

## フィールド仕様詳細

### company ブロック（必須）

| フィールド | 型 | 必須/任意 | 例 | 用途 |
|-----------|---|----------|-----|------|
| `company.name` | string | **必須** | "株式会社山本" | 表紙・ヘッダーの企業名 |
| `company.honorific` | enum | **必須** | "御中" \| "様" \| "" | 敬称。"" は敬称なし |
| `company.industry` | enum | **必須** | "saas" | benchmarks.yaml の業界キー |
| `company.industry_label` | string | 任意 | "SaaS" | レポート表示用の業界名 |
| `company.logo_path` | string | 任意 | "config/logos/x.png" | 表紙ロゴ（将来拡張） |
| `company.brand_color` | string | 任意 | "#1a1a1a" | アクセントカラー（将来） |

#### industry の許容値（初期）
benchmarks.yaml と一致させる。

```yaml
industries:
  - saas              # SaaS／クラウドサービス
  - ecommerce         # EC・通販
  - local_service     # 地域密着サービス（飲食、美容、塾など）
  - b2b_enterprise    # B2B エンタープライズ
  - info_product      # 情報商材・オンライン講座
  - real_estate       # 不動産
  - healthcare        # 医療・ヘルスケア
  - finance           # 金融・保険
  - education         # 教育
  - mobile_app        # モバイルアプリ
  - other             # その他（ベンチマーク取得不可）
```

`other` の場合はベンチマーク比較を「比較データなし」として表示する。

---

### contact ブロック（必須）

| フィールド | 型 | 必須/任意 | 例 | 用途 |
|-----------|---|----------|-----|------|
| `contact.name` | string | 任意 | "山本 太郎" | 担当者氏名。未設定時は "ご担当者" |
| `contact.honorific` | enum | **必須** | "様" \| "殿" \| "先生" \| "" | 担当者敬称 |
| `contact.title` | string | 任意 | "マーケティング部 部長" | 役職 |
| `contact.email` | string | 任意 | "..." | レポート送付先 CC |

#### 表記出力ロジック

```python
def format_addressee(client_config) -> str:
    company = client_config["company"]
    contact = client_config.get("contact", {})

    parts = []
    parts.append(f"{company['name']} {company['honorific']}")

    if contact.get("name"):
        title = f"{contact['title']} " if contact.get("title") else ""
        parts.append(f"ご担当 {title}{contact['name']} {contact['honorific']}")
    else:
        parts.append("ご担当者様")

    return "\n".join(parts)
```

#### 出力例
- 法人 + 担当者あり:
  ```
  株式会社山本 御中
  ご担当 マーケティング部 部長 山本 太郎 様
  ```
- 法人 + 担当者未設定:
  ```
  株式会社山本 御中
  ご担当者様
  ```
- 個人事業主:
  ```
  山本 太郎 様
  ```

---

### report ブロック（任意）

| フィールド | 型 | 必須/任意 | デフォルト | 用途 |
|-----------|---|----------|----------|------|
| `report.display_name` | string | 任意 | "広告アカウント健康診断レポート" | タイトル上書き |
| `report.include_zynect_insights` | bool | 任意 | true | P7 セクション制御 |
| `report.include_appendix` | bool | 任意 | true | P8 付録制御 |
| `report.premium_model_for_insights` | bool | 任意 | false | Opus 4.7 を Insights で使うか |
| `report.report_period_days` | int | 任意 | 30 | レポート対象期間（日） |

---

## 後方互換性

### 旧フィールド `name` の扱い

```python
def get_company_name(client_config) -> str:
    # v3 優先
    if "company" in client_config and "name" in client_config["company"]:
        return client_config["company"]["name"]

    # 後方互換 (v2)
    if "name" in client_config:
        log.warning(f"client {client_config.get('id')} uses legacy 'name' field. Migrate to 'company.name'")
        return client_config["name"]

    raise ValueError("company.name または name フィールドが必須")
```

### 移行ステップ
1. **Phase 1（v3 リリース直後）**: `company` ブロック未設定時は旧 `name` を fallback として使い、警告ログを出す
2. **Phase 2（v3.1）**: 既存全クライアントの `clients.yaml` を一斉更新するマイグレーションスクリプト `scripts/migrate_clients_v3.py` を実行
3. **Phase 3（v3.2）**: 旧 `name` フィールドサポート廃止

---

## 関連ファイル

### v3 で新設するファイル

#### `config/benchmarks.yaml`（新設）
業界平均のベンチマーク値。`company.industry` キーと一致。

```yaml
industries:
  saas:
    description: "SaaS／クラウドサービス"
    health_score:
      median: 65
      p75: 80
    avg_cpa: 4800
    avg_roas: 2.5
    avg_ctr_search: 3.0
    avg_ctr_meta: 1.0
    avg_frequency: 2.5
    source: "WordStream Industry Benchmarks 2026 Q1"
    last_updated: "2026-04-15"

  ecommerce:
    description: "EC・通販"
    health_score:
      median: 60
      p75: 78
    avg_cpa: 2800
    avg_roas: 3.5
    ...
```

#### `config/terminology.yaml`（新設）
用語翻訳辞書（`v3_terminology_dict.md` から構造化）。

```yaml
terminology:
  CPA:
    full_name: "Cost Per Acquisition"
    plain_jp: "顧客獲得単価"
    description: "広告経由で1人の顧客を獲得するのにかかった広告費"
    risk_note: "CPA が高いと広告効率が悪く、利益を圧迫します"
    category: "metric"

  frequency:
    full_name: "Frequency"
    plain_jp: "同一ユーザーへの広告表示回数"
    description: "月あたり4回を超えると広告疲れの兆候"
    ...
```

#### `config/logos/`（新設ディレクトリ）
クライアント企業ロゴ格納用（任意）。`.gitignore` に登録し、リポジトリには含めない（顧客資産のため）。

---

## clients.yaml 完全例（v3 形式）

```yaml
defaults:
  timezone: "Asia/Tokyo"
  currency: "JPY"
  schedule: "0 9 * * *"
  anthropic_model: "claude-sonnet-4-6"
  anthropic_model_premium: "claude-opus-4-7"  # v3 新設

clients:
  yamamoto_demo:
    company:
      name: "株式会社山本"
      honorific: "御中"
      industry: "saas"
      industry_label: "SaaS"
    contact:
      name: "山本 太郎"
      honorific: "様"
      title: "マーケティング部 部長"
      email: "yamamoto@example.com"
    report:
      include_zynect_insights: true
      premium_model_for_insights: true
      report_period_days: 30
    active: true
    objective: balanced
    ads:
      meta:
        account_id: "act_916507360736121"
        access_token_env: "META_ACCESS_TOKEN_BANDAL"
        lookback_days: 90
      tiktok:
        advertiser_id: "XXXXXXXXX"
        access_token_env: "TIKTOK_ACCESS_TOKEN_YAMAMOTO"
      google:
        customer_id: "XXX-XXX-XXXX"
        developer_token_env: "GOOGLE_ADS_DEVELOPER_TOKEN"
        credentials_path: "config/google-ads.yaml"
    seo:
      site_url: "https://example.com"
      landing_pages:
        - "https://example.com/lp1"
    adtruth:
      enabled: false
      api_key_env: "ADTRUTH_API_KEY_YAMAMOTO"
    notifications:
      slack:
        webhook_env: "SLACK_WEBHOOK_YAMAMOTO"
        channel: "#yamamoto-ads"
    intent_overrides:
      - rule_ids: ["C05", "C06"]
        reason: "テスト期間中のため意図的に低予算設定"
        registered_by: "運用担当"
        registered_at: "2026-04-30"
        expires_at: "2026-07-31"
        suppress_action: "skip_notification"
    crm:
      twenty:
        enabled: true
        company_id: ""

  bandal_gaming:
    company:
      name: "株式会社バンダルゲーミング"
      honorific: "御中"
      industry: "mobile_app"
      industry_label: "モバイルアプリ"
    contact:
      name: ""
      honorific: "様"
    report:
      include_zynect_insights: false  # 簡易レポート運用
    active: true
    objective: cv_maximize
    ads:
      meta:
        account_id: "act_916507360736121"
        access_token_env: "META_ACCESS_TOKEN_BANDAL"
        lookback_days: 90
    notifications:
      platform: "lark"
      lark:
        webhook_env: "LARK_WEBHOOK_BANDAL"
```

---

## バリデーション要件

### 起動時チェック（pipeline.py）

クライアント設定読み込み時に以下を検証:

1. **必須フィールドの存在**:
   - `company.name`, `company.honorific`, `company.industry`
   - `contact.honorific`

2. **enum 値の妥当性**:
   - `company.honorific` は ["御中", "様", "殿", ""] のいずれか
   - `company.industry` は `benchmarks.yaml` のキーのいずれか

3. **後方互換警告**:
   - `company` ブロックがなく `name` のみの場合、警告ログ + マイグレーション推奨メッセージ

### 検証関数（提案）

```python
# engine/client_config_validator.py（新設）

ALLOWED_COMPANY_HONORIFICS = {"御中", "様", "殿", ""}
ALLOWED_CONTACT_HONORIFICS = {"様", "殿", "先生", ""}

def validate_client_config(client_id: str, config: dict, benchmarks: dict) -> list[str]:
    errors = []

    if "company" not in config:
        if "name" in config:
            log.warning(f"{client_id}: legacy 'name' field detected. Migrate to v3 schema.")
        else:
            errors.append("company ブロックが必須です")
        return errors

    company = config["company"]
    for field in ["name", "honorific", "industry"]:
        if field not in company:
            errors.append(f"company.{field} が必須です")

    if company.get("honorific") not in ALLOWED_COMPANY_HONORIFICS:
        errors.append(f"company.honorific は {ALLOWED_COMPANY_HONORIFICS} のいずれかで指定")

    if company.get("industry") not in benchmarks.get("industries", {}):
        errors.append(f"company.industry={company.get('industry')} が benchmarks.yaml に未定義")

    contact = config.get("contact", {})
    if contact.get("honorific") and contact["honorific"] not in ALLOWED_CONTACT_HONORIFICS:
        errors.append(f"contact.honorific は {ALLOWED_CONTACT_HONORIFICS} のいずれかで指定")

    return errors
```

---

## オンボーディング運用への影響

### `docs/onboarding_questionnaire.md` への追加項目（推奨）

新規クライアント受入時に取得する情報を以下に拡張:

1. **企業情報**:
   - 企業正式名称（登記名）
   - 屋号がある場合の屋号名（任意）
   - 業界（10業界からの選択）
   - 法人 / 個人事業主の区分

2. **担当者情報**:
   - ご担当者氏名
   - 役職
   - メールアドレス（CC送付用、任意）
   - 敬称（様 / 殿 / 先生）

3. **レポート設定**:
   - Zynect Insights セクションを含むか（営業価値の重視度）
   - レポート対象期間（30日 / 14日 / 7日）

これらを `clients.yaml` に転記する作業を、Day 2 以降のオンボーディング標準フローに組み込む。

---

## v3 設定仕様改修の引き継ぎ事項

1. **既存全クライアントのマイグレーション**: 既存 `clients.yaml` を v3 形式に書き換える `scripts/migrate_clients_v3.py` を Day 4 で実装。バックアップを必ず取得。
2. **`config/benchmarks.yaml` の初期データ作成**: 5業界（SaaS / EC / 地域サービス / B2B / 教育）のベンチマーク値を出典付きで整備。
3. **`config/terminology.yaml` の構造化**: `v3_terminology_dict.md` の60語を YAML 化。
4. **バリデーション関数の実装**: pipeline.py 起動時に呼び出し、設定ミスを早期発見。
5. **オンボーディング資料の更新**: `docs/onboarding_questionnaire.md` / `docs/onboarding.md` の改修。
6. **テンプレート側のフィールド参照差し替え**: `templates/v3/cover.html` 等で `{{ client.company.name }}` 形式に変更。
