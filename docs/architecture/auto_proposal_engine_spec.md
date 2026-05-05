# 自動提案エンジン 詳細仕様 (ADR-012 実装ガイド)

| 項目 | 値 |
|------|---|
| 作成日 | 2026-05-05 |
| 関連 ADR | ADR-011 (Bot 通知グループ), ADR-012 (自動提案エンジン) |
| 実装着手 | 2026-05-05 |
| 主目的 | 5/5 実装着手のための具体スキーマ + コード構造の確定 |

---

## 1. ファイル構成 (実装される 14 ファイル)

```
bpo-system/
├── config/
│   ├── auto_proposal_rules.yaml         🆕 (初期 7 ルール、~150 行)
│   └── clients.yaml                     ✏️ chatwork_rooms フィールド追加
├── engine/
│   └── auto_proposal_engine.py          🆕 メインエンジン (~400 行)
├── notifiers/
│   └── chatwork_notifier.py             ✏️ Bot トークン優先ロジック追加
├── templates/chatwork/
│   ├── _client_request_base.md.j2       🆕 共通枠 (~30 行)
│   ├── _client_request_ecforce_access.md.j2          🆕 (~40 行)
│   ├── _client_request_capi_setup.md.j2              🆕 (~50 行)
│   ├── _client_request_domain_verification.md.j2     🆕 (~40 行)
│   ├── _client_request_first_party_data.md.j2        🆕 (~50 行)
│   ├── _client_request_adtruth_lp_questions.md.j2    🆕 (~60 行)
│   ├── _client_request_aem_review.md.j2              🆕 (~40 行)
│   └── _client_request_monthly_summary_preview.md.j2 🆕 (~30 行)
├── scripts/
│   ├── run_auto_proposal.py             🆕 (~80 行、CLI エントリ)
│   └── setup/
│       └── new_client.py                🆕 (~150 行、新規追加スクリプト)
├── outputs/
│   ├── client_state/                    🆕 ディレクトリ
│   │   └── pilotton.yaml                🆕 (初期化、~40 行)
│   └── auto_proposal_history/           🆕 ディレクトリ
│       └── pilotton.yaml                🆕 (初期化、空)
└── tests/
    └── test_auto_proposal_engine.py     🆕 (~250 行、12 ケース)
```

---

## 2. `config/clients.yaml` 拡張詳細

### 既存 + 新規フィールド (pilotton 例)

```yaml
clients:
  pilotton:
    company:
      name: 株式会社パイロットン
      honorific: 御中
      industry: beauty_d2c
      ec_platform: ecforce              # 🆕 ec_platform フィールド (ADR-012)
    
    active: true
    objective: balanced
    lookback_days: 30
    
    ads:
      meta:
        account_id: act_566972639374407
        access_token_env: META_ACCESS_TOKEN_PILOTTON
        pixels:
          - id: 12345
            name: MYNAILPLEX_main
            note: メインピクセル
            duplicate: false
            dormant_days: 312
    
    # === 🆕 ChatWork 設定 (ADR-011) ===
    chatwork_rooms:
      main: 435851481                   # 自動通知グループ (集約方針)
      staging: 435851481                # 内部レビュー期間中は本番と共用
    chatwork_bot_token_env: CHATWORK_BOT_API_TOKEN
    
    # === 🆕 経済指標 (ADR-009 §6.3 + 自動提案で参照) ===
    economics:
      aov_jpy: 15000                    # 仮値、ヒアリング後に実値
      ltv_aov_ratio: 3.0
      operational_cost_per_indication_jpy: 1000
      false_positive_tolerance: 0.05
    
    notifications:
      platform: chatwork                # 🆕 chatwork primary
      slack: { channel: "#pilotton" }   # 既存維持 (ops_alert 候補で使用)
      lark: { webhook_env: "" }
    
    seo:
      site_url: ""                       # 未設定なら SEO 監査スキップ
```

---

## 3. `outputs/client_state/{client_id}.yaml` 完全スキーマ

### pilotton.yaml 初期化版 (5/5 作成想定)

```yaml
# === 自動提案エンジン (ADR-012) のクライアント状態 ===
client_id: pilotton
last_updated: 2026-05-05T20:30:00+09:00
state_schema_version: 1

# === Phase 進捗 ===
phase: A                                     # A | B | C
phase_a_started_at: 2026-05-04T09:00:00+09:00
phase_a_internal_review_until: 2026-05-17    # 14 日間
phase_b_started_at: null
phase_c_started_at: null

# === EC プラットフォーム連携 ===
ec_platform: ecforce
ecforce_access_granted: false                # ECフォース 管理画面の閲覧権限
ecforce_fb_cv_api_configured: false          # FB CV API タブで設定済
ecforce_csv_export_tested: false             # 受注/顧客 CSV エクスポートが可能と確認済

# === 計測基盤 (measurement_foundation 関連) ===
capi_setup_status: not_started               # not_started | in_progress | completed | verified
capi_emq_score: null                         # 数値、6.0 以上で healthy
pixel_status: dormant                        # dormant | active | consolidated
pixel_dormant_days: 312                      # adapters/meta_adapter.py から自動更新
domain_verification_status: not_started      # not_started | dns_pending | completed
domain_verification_method: dns              # dns | html_file | meta_tag
aem_events_configured: false                 # ※2025/6 仕様変更で形骸化、value 最適化のみ確認

# === 1st パーティ + Customer Audience ===
first_party_data_received: false             # CSV 受領済
first_party_data_received_at: null
customer_audience_uploaded: false
customer_audience_match_rate_pct: null       # アップロード後の Meta マッチ率
lookalike_audience_created: false

# === AdTruth (LP 不正検知、Phase B Week 3-4) ===
adtruth_lp_questions_completed: false        # Q1-Q5 ヒアリング完了
adtruth_consent_banner_implemented: false
adtruth_privacy_policy_updated: false
adtruth_tag_installed: false

# === 顧客側ヒアリング項目 (確定値) ===
aov_jpy_confirmed: null                      # ヒアリング後の実値
ltv_aov_ratio_confirmed: null
retargeting_campaigns_active: null           # bool
retargeting_campaign_names: []
brand_search_ads_running: null
cv_double_count_check_done: null

# === 自動投稿履歴サマリ (auto_proposal_history との重複は許容、参照用) ===
last_request_sent_at: null
last_request_rule_id: null
total_requests_sent: 0
total_requests_acknowledged: 0               # 顧客返信があった件数
```

### 状態フィールドの更新タイミング (3 経路)

```
1. 手動更新 (Phase A 〜 B 移行期、まだメイン)
   scripts/update_client_state.py --client pilotton \
       --field capi_setup_status --value completed

2. 自動検知 (Phase B Week 1 以降に実装拡充)
   adapters/meta_adapter.py の取得結果から:
     - pixel_dormant_days を毎日更新
     - capi_emq_score を毎日更新
   adapters/ecforce_adapter.py (Phase B Week 1 新規) から:
     - ecforce_access_granted を初回呼出成功時に true 化
     - aov_jpy_confirmed を ecforce bi 経由で実値取得

3. ChatWork 顧客返信パース (Phase B Week 3 以降、Claude 補助)
   templates/chatwork/_client_request_*.md.j2 への返信を Claude API で構造化
     → 「権限付与しました」「CSV 送りました」を検知して自動更新
```

---

## 4. `config/auto_proposal_rules.yaml` 完全版 (5/5 着手分の 7 ルール)

```yaml
version: 1
last_updated: 2026-05-05

defaults:
  daily_cap: 1                                # 1 client 1 日 1 件
  cooldown_days_default: 7
  deadline_days_default: 14

# === 演算子サポート ===
# trigger / prerequisite / skip_if の各条件は以下の形式:
#   - <field>: <value>             ← 等価判定
#   - <field>: { ">": <value> }    ← 比較演算子
#   - <field>: { "in": [a, b] }    ← リスト所属
#   - day_of_month: <int>          ← 特殊フィールド (calendar 評価)

rules:
  - id: ecforce_access_request
    name: ECフォース 管理画面の閲覧権限ご依頼
    priority: 100
    template: _client_request_ecforce_access.md.j2
    trigger:
      - ecforce_access_granted: false
      - ec_platform: ecforce
    prerequisite:
      - phase: { in: ["A", "B"] }
    cooldown_days: 7
    deadline_days: 7
    rationale: "FB CV API 設定および AOV/LTV 自動取得の前提となる権限"
    skip_if: []

  - id: capi_setup_request
    name: Meta CAPI 設定のご依頼
    priority: 95
    template: _client_request_capi_setup.md.j2
    trigger:
      - capi_setup_status: not_started
    prerequisite:
      - ecforce_access_granted: true
      - domain_verification_status: completed
    cooldown_days: 5
    deadline_days: 14
    rationale: "M02 解消で月次 ¥50,000+ の効果回復"

  - id: domain_verification_request
    name: ドメイン認証 (DNS TXT) のご依頼
    priority: 90
    template: _client_request_domain_verification.md.j2
    trigger:
      - domain_verification_status: not_started
    prerequisite: []
    cooldown_days: 7
    deadline_days: 7
    rationale: "AEM 設定 + iOS14.5+ 配信の必須基盤"

  - id: first_party_data_request
    name: 顧客リスト (1st パーティ) のご共有
    priority: 80
    template: _client_request_first_party_data.md.j2
    trigger:
      - first_party_data_received: false
    prerequisite:
      - ecforce_access_granted: true
    cooldown_days: 14
    deadline_days: 14
    rationale: "Lookalike 1-3% シードで新規 CPA -10〜15% 効果"
    skip_if:
      - phase: A

  - id: adtruth_lp_questions
    name: 不正検知タグ設置のための事前ご質問
    priority: 60
    template: _client_request_adtruth_lp_questions.md.j2
    trigger:
      - adtruth_tag_installed: false
      - adtruth_lp_questions_completed: false
      - phase: B
    prerequisite:
      - first_party_data_received: true
    cooldown_days: 30
    deadline_days: 21
    rationale: "Phase B Week 3-4 LP タグの法務 + 技術ヒアリング"

  - id: aem_events_review
    name: AEM (集計イベント測定) の現状確認
    priority: 40
    template: _client_request_aem_review.md.j2
    trigger:
      - aem_events_configured: false
      - capi_setup_status: completed
    prerequisite:
      - domain_verification_status: completed
    cooldown_days: 30
    deadline_days: 14
    rationale: "2025/6 仕様変更後の value 最適化 ON 確認"

  - id: monthly_report_summary_preview
    name: 月次レポート発信予告
    priority: 50
    template: _client_request_monthly_summary_preview.md.j2
    trigger:
      - day_of_month: 1
    prerequisite:
      - phase: { in: ["B", "C"] }
    cooldown_days: 28
    deadline_days: null
    rationale: "毎月 1 日 10:00 月次レポート (ADR-005) 投稿前の予告"
```

---

## 5. テンプレート設計 (7 ファイル)

### 5.1 共通基底: `_client_request_base.md.j2`

```jinja2
{# 全 _client_request_*.md.j2 が extends する共通枠 (ADR-012 D-5) #}
[info][title]【自動提案】{% block title %}{% endblock %}[/title]
[Zynect Auto-Reporter からの自動投稿 / {{ today }}]

{{ client_name }} {{ honorific or "御中" }}

弊社の自動診断システムが現在の運用状態を分析した結果、以下の対応をお願いしたいと判断しました。

▼ ご依頼内容
{% block request_body %}{% endblock %}

▼ 重要度・期限
　・優先度: {{ priority_label }}
{% if deadline %}
　・推奨完了期限: {{ deadline }} ({{ deadline_days }} 日以内)
{% endif %}

▼ なぜ今これが必要か
　{{ rationale }}

{% block specific_content %}{% endblock %}

▼ ご質問・ご相談
　・本依頼に関する質問はこのスレッドにご返信ください
　・状況に変化があれば弊社運用担当 (山本) までご連絡ください

[Zynect Media 自動運用システム / {{ rule_id }} / {{ today }}]
[/info]
```

### 5.2 個別テンプレ: `_client_request_ecforce_access.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}ECフォース 管理画面の閲覧権限ご依頼{% endblock %}

{% block request_body %}
　ECフォース 管理画面 → 「アカウント管理」(ecforce accounts) で
　Zynect Media 担当 (山本 / 弊社運用チーム) へスタッフメンバー追加をお願いします。
{% endblock %}

{% block specific_content %}
▼ 必要権限 (最小セット、管理者権限は不要)
　・受注                          [閲覧]
　・顧客                          [閲覧]
　・商品                          [閲覧]
　・外部連携アカウント管理          [閲覧 + 設定変更]
　　└ うち「FB CV API」タブの設定変更権限が必須
　・ecforce bi                    [閲覧]
　※ 配送 / 在庫 / 経理 等は不要

▼ 弊社からのご質問 (権限付与の前確認)
　Q1. ECフォース「FB CV API」タブで既に何らかの設定が入っていますか?
　Q2. Meta Pixel ID は把握されていますか?
　Q3. リターゲティング キャンペーン (RT_) は現在運用されていますか?

▼ セキュリティ
　・閲覧/設定変更権限は弊社運用 2 名のみに付与
　・パスワード等の認証情報は ChatWork 経由ではなく管理画面の本人発行リンクで完結
　・kickoff 終了後 1 ヶ月毎に弊社側でアクセス監査記録を残します
{% endblock %}
```

### 5.3 個別テンプレ: `_client_request_capi_setup.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}Meta CAPI 設定のご依頼{% endblock %}

{% block request_body %}
　ECフォース 管理画面 → 「外部連携アカウント管理」→「FB CV API」タブで
　Meta Conversions API の連携設定をお願いします。
{% endblock %}

{% block specific_content %}
▼ 設定手順 (5 ステップ、所要 10-15 分)
　1. ECフォース 管理画面ログイン
　2. 「外部連携アカウント管理」→「FB CV API」タブを開く
　3. 弊社が Meta Business Manager で発行するアクセストークンを入力
　　 (本依頼に対する弊社からの返信時にトークンをお送りします)
　4. テストイベント機能で疎通確認
　5. 本番有効化、48 時間後 EMQ スコア 6.0 以上で完了

▼ Pixel との dedup について
　ECフォース は CAPI 送信時に event_id を自動付与するため、
　Pixel との dedup_key 設定は不要です (= 二重計上の懸念なし)

▼ 期待効果 (M02 解消想定)
　・月次 ¥{{ projected_monthly_benefit_jpy or "50,000+" }} の改善余地
　・Pixel 休眠 ({{ pixel_dormant_days or "312" }} 日継続) 解消の起点
{% endblock %}
```

### 5.4 個別テンプレ: `_client_request_domain_verification.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}ドメイン認証 (DNS TXT) のご依頼{% endblock %}

{% block request_body %}
　Meta Business Manager のブランドセーフティ設定で、対象ドメインを
　DNS TXT レコード方式で認証してください。
{% endblock %}

{% block specific_content %}
▼ 推奨方式: DNS TXT レコード
　・最も永続的、サブドメイン含めて一括認証可能
　・反映時間: 通常 15 分、最大 4 時間

▼ 手順 (4 ステップ)
　1. Meta Business Manager → ブランドセーフティ → 「ドメイン」を開く
　2. 対象ドメイン (例: example.com) を追加
　3. 表示される TXT レコード (facebook-domain-verification=xxxxx) を
　    ドメイン管理サービス (Route 53 / お名前.com / Cloudflare 等) に追加
　4. DNS 反映後に Meta 側「認証」ボタンを押下、「認証済み」を確認

▼ 認証完了で解放される機能
　・AEM (集計イベント測定) の設定が解放
　・iOS14.5+ ユーザに対する広告配信の最適化が改善

▼ DNS 編集権限がない場合
　HTML ファイル方式 / メタタグ方式の選択肢もありますので、
　ドメイン管理権限の有無をご返信ください。
{% endblock %}
```

### 5.5 個別テンプレ: `_client_request_first_party_data.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}顧客リスト (1st パーティデータ) のご共有{% endblock %}

{% block request_body %}
　ECフォース から顧客リスト CSV をエクスポートいただき、
　弊社の Customer Audience アップロード窓口へご送付ください。
{% endblock %}

{% block specific_content %}
▼ Customer File 形式仕様
　・形式: CSV (UTF-8、ヘッダー必須、ZIP 不可)
　・必須カラム: email または phone (どちらか最低 1 つ)
　・推奨カラム: first_name / last_name / city / state / zip / country / dob

▼ プライバシー対応
　・SHA256 ハッシュ化はあらかじめ実施 (Meta 側で再ハッシュ不要)
　・送付時は弊社側 Secure File Transfer 経由 (kickoff day にご案内)
　・ChatWork での添付送信は禁止 (個人情報保護のため)

▼ 期待効果
　・Lookalike 1-3% シードで新規 CPA -10〜15% 改善見込み
　・既存顧客の「除外設定」で予算効率向上
　・マッチ率目安: 30% 以上で OK、50% 以上で良好

▼ 個情法対応
　・取得済み同意の範囲内で第三者提供 (Meta) が可能か事前確認お願いします
　・退会顧客は別途「除外リスト」として管理しますので併せてご連絡ください
{% endblock %}
```

### 5.6 個別テンプレ: `_client_request_adtruth_lp_questions.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}不正検知タグ設置のための事前ご質問{% endblock %}

{% block request_body %}
　ランディングページに不正検知タグ (JavaScript) を設置するため、
　以下 5 件のご質問にご回答ください (所要 10 分)。
{% endblock %}

{% block specific_content %}
▼ 取得予定データ (確認のため明記)
　・IP アドレス、User Agent、デバイス fingerprint
　・滞在時間、スクロール深度、マウス動作、リファラ、クリック情報

▼ ご質問 5 件
　Q1. プライバシーポリシーへの記載追加は弊社が文案提供で進めて OK ですか?
　Q2. 同意バナー設置の実装は弊社が代行する形で OK ですか?
　Q3. EU/UK ユーザーは含まれますか? (GDPR 対応の要否確認)
　Q4. 既存の Cookie 利用同意フローは現在どうなっていますか?
　Q5. LP の HTML テンプレートを編集できる方は社内にいらっしゃいますか?

▼ 期待効果と費用
　・業界推計で広告費の 10〜20% が無効トラフィックと推定
　・タグ設置による捕捉精度向上を期待
　・追加コスト: ゼロ (弊社内製実装、外部 SaaS 不使用)
　・ページ表示速度への影響: タグサイズ 12KB minified、Lighthouse TBT < 50ms 想定

▼ 工数
　・法務レビュー 1〜2 日 + Zynect 実装支援 4〜6 日 (Phase B Week 3-4)
{% endblock %}
```

### 5.7 個別テンプレ: `_client_request_aem_review.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}AEM (集計イベント測定) 設定の現状確認{% endblock %}

{% block request_body %}
　Meta Events Manager で AEM (集計イベント測定) と value 最適化の
　現状をご確認ください。
{% endblock %}

{% block specific_content %}
▼ 仕様変更注記 (2025 年 6 月以降)
　Meta は AEM の 8 イベント枠制限と手動優先順位設定を撤廃しました。
　現在は対象ドメインの全イベントが自動集計される仕様です。

▼ 確認事項 (旧仕様の手動設定は不要)
　1. 対象ドメインが Meta に「認証済み」として登録されているか
　2. Events Manager で「データの質」スコアが算出されているか (EMQ 6.0 以上)
　3. Purchase イベントで value 最適化が ON になっているか
　4. value 最適化の参照金額レンジが実績ベースか (例: ¥3,000 〜 ¥50,000)

▼ 期待効果
　・iOS opt-out ユーザの CV value 推定が機能、ROAS 系広告セット効率化
　・SKAdNetwork 経由の CV が正しく集計されること

▼ 弊社で確認できる情報
　・現状の EMQ スコア: {{ capi_emq_score or "未取得" }}
　・iOS SKAN 計上比率: 過去 30 日の Ads Manager レポートから自動算出可能
{% endblock %}
```

### 5.8 個別テンプレ: `_client_request_monthly_summary_preview.md.j2`

```jinja2
{% extends "_client_request_base.md.j2" %}
{% block title %}{{ today }} 今月の月次レポート発信予告{% endblock %}

{% block request_body %}
　本日 10:00 頃に当月の月次運用レポート (PDF 添付) を本ルームへ自動投稿します。
{% endblock %}

{% block specific_content %}
▼ 月次レポートの内容
　・先月の指摘件数 / 解消件数 / 進行中
　・達成効果 (3 層: 確実値 / 現実値 / 上限値)
　・解消ハイライト (重要度順)
　・次月の重点フォーカス
　・添付 PDF: 8 ページ構成の v3 詳細レポート

▼ ご確認のお願い
　・レポート受信後、内容に齟齬がございましたら本スレッドへご返信ください
　・修正対応は 3 営業日以内に再送いたします

▼ 自動運用ステータス
　・直近 30 日の自動投稿: {{ auto_post_count_30days or "(集計中)" }} 件
　・指摘 → 解消の中央値: {{ median_resolution_days or "—" }} 日
　・進捗良好な施策: {{ top_progress_rule_id or "—" }}
{% endblock %}
```

---

## 6. `engine/auto_proposal_engine.py` 詳細設計

### 6.1 関数シグネチャ一覧

```python
"""企業別自動提案エンジン (ADR-012)"""
from pathlib import Path
from typing import Optional
from datetime import datetime, date

# === Public API ===
def run_auto_proposal(
    client_id: str,
    dry_run: bool = False,
    today: Optional[str] = None,
) -> dict:
    """1 client の自動提案サイクル実行 (メインエントリ)"""

def load_client_state(client_id: str) -> dict:
    """outputs/client_state/{client_id}.yaml 読込、存在しなければ空で初期化"""

def save_client_state(client_id: str, state: dict) -> None:
    """state を yaml に atomic write"""

def load_proposal_rules() -> list[dict]:
    """config/auto_proposal_rules.yaml 読込、priority 降順ソート"""

def evaluate_rules(state: dict, rules: list, history: dict, today: str) -> list[dict]:
    """trigger / prerequisite / skip_if / cooldown 評価"""

def render_and_post(rule: dict, state: dict, client_cfg: dict, dry_run: bool) -> dict:
    """テンプレート rendering + ChatWork 投稿"""

def update_state(client_id: str, posted_request: dict) -> None:
    """history に投稿記録 + state の last_request_* 更新"""

# === Private helpers ===
def _match_trigger(conditions: list, state: dict, today: str) -> bool:
    """trigger 条件評価 (AND)、day_of_month 等の特殊フィールド対応"""

def _match_prerequisite(conditions: list, state: dict) -> bool:
    """前提条件評価 (AND)"""

def _match_skip_if(conditions: list, state: dict) -> bool:
    """スキップ条件評価 (OR)"""

def _check_cooldown(rule: dict, history: dict, today: str) -> bool:
    """前回投稿日 + cooldown_days 以上経過か"""

def _evaluate_condition(value: any, rule_value: any) -> bool:
    """単一条件評価: 等価 / 比較 (>, <, in, not_in)"""

def _build_render_context(rule: dict, state: dict, client_cfg: dict) -> dict:
    """テンプレート用 context 組立"""

def _load_history(client_id: str) -> dict:
    """outputs/auto_proposal_history/{client_id}.yaml 読込"""

def _today_str(today: Optional[str] = None) -> str:
    """今日の date 文字列"""

def _days_between(date1_iso: str, date2_iso: str) -> int:
    """ISO 文字列の日数差"""

def _atomic_write_yaml(path: Path, data: dict) -> None:
    """tmp + rename パターンの安全書込"""
```

### 6.2 条件評価サポート (拡張可能)

```python
def _evaluate_condition(actual: any, expected: any) -> bool:
    """
    rule の trigger / prerequisite / skip_if の単一条件評価。
    
    expected が dict の場合は演算子付き判定:
      {">":  N}    actual > N
      {">=": N}    actual >= N
      {"<":  N}    actual < N
      {"<=": N}    actual <= N
      {"in":     [a, b, c]}
      {"not_in": [a, b, c]}
      {"!=":     N}
    
    expected が dict 以外の場合は等価判定: actual == expected
    """
    if isinstance(expected, dict):
        for op, val in expected.items():
            if op == ">":      return actual is not None and actual > val
            if op == ">=":     return actual is not None and actual >= val
            if op == "<":      return actual is not None and actual < val
            if op == "<=":     return actual is not None and actual <= val
            if op == "in":     return actual in val
            if op == "not_in": return actual not in val
            if op == "!=":     return actual != val
        return False
    return actual == expected
```

### 6.3 day_of_month 等の特殊フィールド評価

```python
def _match_trigger(conditions: list, state: dict, today: str) -> bool:
    today_date = datetime.fromisoformat(today).date()
    
    for cond in conditions:
        for field, expected in cond.items():
            # 特殊フィールド
            if field == "day_of_month":
                actual = today_date.day
            elif field == "day_of_week":
                actual = today_date.weekday()  # 0=Mon, 6=Sun
            elif field == "month":
                actual = today_date.month
            else:
                actual = state.get(field)
            
            if not _evaluate_condition(actual, expected):
                return False
    return True
```

---

## 7. テスト設計 (`tests/test_auto_proposal_engine.py` 12 ケース)

```python
"""ADR-012 自動提案エンジンのテスト"""

class TestRuleEvaluation:
    def test_trigger_simple_equal(self):
        """trigger: ecforce_access_granted: false → 状態 false なら match"""

    def test_trigger_with_operator_in(self):
        """trigger: phase: { in: ["A", "B"] } → 状態 'A' なら match"""

    def test_trigger_day_of_month(self):
        """trigger: day_of_month: 1 → today=2026-05-01 なら match"""

class TestPrerequisite:
    def test_prerequisite_blocks_when_unmet(self):
        """capi_setup_request は ecforce_access_granted=false なら発火しない"""

    def test_prerequisite_chain(self):
        """capi_setup_request → ecforce_access_granted=true & domain_verification_status=completed の両方必要"""

class TestCooldown:
    def test_cooldown_blocks_within_period(self):
        """前回投稿から 3 日後の場合、cooldown_days=7 はまだ block"""

    def test_cooldown_passes_after_period(self):
        """前回投稿から 8 日後なら cooldown 経過、再発火可能"""

class TestSkipIf:
    def test_skip_if_phase_a(self):
        """first_party_data_request は phase=A なら skip"""

class TestPriorityOrdering:
    def test_priority_descending_order(self):
        """trigger に複数 hit した場合、priority 100 > 95 > 90 の順"""

class TestDailyCap:
    def test_daily_cap_limits_to_1(self):
        """eligible 5 件あっても DAILY_CAP=1 で 1 件のみ post"""

class TestStateUpdate:
    def test_post_updates_history(self):
        """投稿後に auto_proposal_history/{client}.yaml に last_sent_at が記録される"""

    def test_post_updates_state_summary(self):
        """投稿後に client_state.last_request_sent_at + total_requests_sent が更新される"""
```

---

## 8. `scripts/run_auto_proposal.py` CLI 仕様

### 引数

```bash
venv/bin/python3 scripts/run_auto_proposal.py [OPTIONS]

Options:
  --client TEXT       特定クライアント (e.g. pilotton)
  --all              全アクティブクライアント (clients.yaml 参照)
  --dry-run          ChatWork に投稿せず、投稿予定のみ表示
  --today TEXT       シミュレーション用日付 (YYYY-MM-DD)
  --verbose          詳細ログ出力 (eligible / skipped 件数等)
  --help             ヘルプ表示
```

### 出力例 (dry-run)

```
$ venv/bin/python3 scripts/run_auto_proposal.py --client pilotton --dry-run

[INFO] auto_proposal: client=pilotton dry_run=True
[INFO] auto_proposal: client_state loaded: phase=A, capi_status=not_started, ecforce_access=false
[INFO] auto_proposal: 7 rules loaded
[INFO] auto_proposal: 評価結果:
  ✓ ecforce_access_request   (priority=100) ← eligible
  ✓ domain_verification_request (priority=90) ← eligible (prerequisite なし)
  ✗ capi_setup_request       (priority=95) prerequisite_failed: ecforce_access_granted!=true
  ✗ first_party_data_request (priority=80) prerequisite_failed + skip_if=phase=A
  ✗ adtruth_lp_questions     (priority=60) skip_if=phase!=B
  ✗ aem_events_review        (priority=40) prerequisite_failed
  ✗ monthly_report_summary_preview (priority=50) trigger_failed: day_of_month=5

[INFO] auto_proposal: 日次 cap=1、選定: ecforce_access_request
[DRY-RUN] templates/chatwork/_client_request_ecforce_access.md.j2
  → Body length: 1234 chars
  → Would post to room_id=435851481
```

### 出力例 (本番投稿)

```
$ venv/bin/python3 scripts/run_auto_proposal.py --client pilotton

[INFO] auto_proposal: ChatWork 投稿成功 room=435851481 message_id=2102XXXXXXXXX
[INFO] auto_proposal: state 更新: last_request_rule_id=ecforce_access_request, total_sent=1
[INFO] auto_proposal: history 記録: outputs/auto_proposal_history/pilotton.yaml
```

---

## 9. `scripts/setup/new_client.py` CLI 仕様

### 引数

```bash
bash scripts/setup/new_client.py [OPTIONS]

Required:
  --client TEXT             クライアント ID (e.g. acme_corp)
  --name TEXT               会社名 (e.g. "株式会社 ACME")
  --industry TEXT           beauty_d2c | ec_retail | finance | education | local_service | mobile_app
  --chatwork-room TEXT      ChatWork ルーム ID (Bot 招待済み)
  --ec-platform TEXT        ecforce | shopify | custom

Optional:
  --meta-account-id TEXT    Meta 広告アカウント ID (act_XXX)
  --google-customer-id TEXT Google Ads Customer ID
  --tiktok-advertiser-id TEXT
  --aov-jpy INT             AOV 仮値 (default: 10000)
  --ltv-ratio FLOAT         LTV/AOV 倍率仮値 (default: 3.0)
  --dry-run                 ファイル書込せず確認のみ
```

### 処理フロー

```
1. 引数バリデーション (industry が benchmarks.yaml に存在するか等)
2. config/clients.yaml に新エントリ追加 (atomic write)
3. outputs/client_state/{client_id}.yaml 初期化 (defaults)
4. outputs/auto_proposal_history/{client_id}.yaml 初期化 (空)
5. outputs/chatwork_state/{client_id}_indications.json 初期化 (空)
6. .env.example に CHATWORK_ROOM_ID_<UPPER> + META_ACCESS_TOKEN_<UPPER> 追記
7. scripts/run_auto_proposal.py --client {id} --dry-run 実行 → 投稿予定 1-2 件表示
8. ユーザ確認後、本番投稿実行 (kickoff 挨拶 + 初期提案)

成功時のサマリ出力:
  ✅ Client {client_id} 登録完了
  📁 config/clients.yaml +1 entry
  📁 outputs/client_state/{client_id}.yaml created
  📁 outputs/auto_proposal_history/{client_id}.yaml created
  📁 .env.example +2 keys (要 .env への実値書込)
  💬 ChatWork 初回投稿: kickoff_greeting + ecforce_access_request
  ⏰ 所要時間: X.X 秒
```

---

## 10. 5/5 実装着手順序 (Claude Code 側、所要 6h)

### Phase 1 (1.5h): 基盤実装

| # | タスク | 工数 | 成果物 |
|---|------|------|--------|
| 1 | `config/auto_proposal_rules.yaml` 作成 (7 ルール) | 0.3h | 150 行 yaml |
| 2 | `outputs/client_state/pilotton.yaml` 初期化 | 0.2h | 40 行 yaml |
| 3 | `engine/auto_proposal_engine.py` スケルトン (関数シグネチャ + docstring) | 0.5h | ~250 行 |
| 4 | `engine/auto_proposal_engine.py` 条件評価ロジック実装 | 0.5h | +150 行 |

### Phase 2 (2h): テンプレート + テスト

| # | タスク | 工数 | 成果物 |
|---|------|------|--------|
| 5 | `templates/chatwork/_client_request_base.md.j2` 共通枠 | 0.2h | 30 行 |
| 6 | 個別テンプレ 7 ファイル作成 | 1h | 各 30-60 行 × 7 |
| 7 | `tests/test_auto_proposal_engine.py` 12 ケース | 0.8h | ~250 行 |

### Phase 3 (1.5h): CLI + setup スクリプト

| # | タスク | 工数 | 成果物 |
|---|------|------|--------|
| 8 | `scripts/run_auto_proposal.py` 実装 | 0.5h | ~80 行 |
| 9 | `scripts/setup/new_client.py` 実装 | 0.7h | ~150 行 |
| 10 | `notifiers/chatwork_notifier.py` の Bot トークン優先ロジック追加 | 0.3h | +20 行修正 |

### Phase 4 (1h): 動作確認 + ドキュメント

| # | タスク | 工数 | 成果物 |
|---|------|------|--------|
| 11 | 全テスト実行 (352 + 12 = 364 件想定) | 0.2h | 全 PASS |
| 12 | pilotton で `--dry-run` 実行、投稿予定確認 | 0.2h | dry-run ログ |
| 13 | 山本確認後、本番投稿で 1-2 件投稿 | 0.2h | message_id 取得 |
| 14 | `docs/internal/auto_proposal_runbook.md` 運用手順書 (オプション) | 0.4h | ~100 行 |

合計工数: **約 6 時間**

---

## 11. 5/7 提案資料への影響範囲

(ADR-012 §「5/7 提案資料への影響範囲」 + 本仕様書 §「差別化文案」と整合)

提案資料 P5-P6 (ロードマップページ) に以下追記候補:

```markdown
### Phase A 完了時の自動運用範囲

✅ 5 媒体 277 ルールの毎日 09:00 自動監査
✅ ChatWork への日次指摘・解消・月次レポート自動投稿
✅ 8 種類の依頼 (ECフォース 権限 / CAPI / 1st party データ等) を自動生成
✅ 新クライアント追加 10 分

### Phase B (5/14-5/28) で追加される自動化

▶ 顧客返信パース (Claude API 補助、Phase B Week 3)
▶ AdTruth LP タグ統合 (Phase B Week 3-4)
▶ 偽陽性コスト試算による block/monitor/investigate 3 段判定
```

---

## 12. References

- ADR-005: [ChatWork ループ](../decisions/ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-009 候補: [トレードオフ設計](./tradeoff_design.md)
- ADR-011: [ChatWork 自動通知グループ](../decisions/ADR-011-chatwork-auto-notification-group.md)
- ADR-012: [自動提案エンジン (本仕様の親)](../decisions/ADR-012-auto-proposal-engine.md)
- 既存実装パターン参照:
  - `engine/indication_state.py` (state 管理)
  - `engine/indication_filter.py` (条件評価 + cooldown)
  - `templates/chatwork/_action_steps.md.j2` (Jinja2 マクロ)
