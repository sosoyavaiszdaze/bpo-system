# ADR-012: 企業別自動提案エンジンの設計

| 項目 | 値 |
|------|---|
| **Status** | Proposed (実装着手 5/5) |
| **Decision Date** | 2026-05-05 |
| **Authors** | 山本 (要件提示) / Claude Code (設計) |
| **Related ADRs** | ADR-005 (ChatWork ループ), ADR-011 (Bot 通知グループ) |

---

## Context

### 現状の問題

現在 Zynect が pilotton (および他クライアント) に依頼する内容 (CAPI 設定 / ECフォース 権限 / 1st party データ受領 / AdTruth タグ設置 等) は **山本が手動で文面作成 → ChatWork DM 送信** している。

これには 4 つの本質的問題がある:

1. **クライアント数が増えると破綻する**:
   依頼項目 5-10 種 × クライアント 30 社 = 150-300 件の文面管理が手作業では不可能

2. **依頼タイミングの判断が職人芸**:
   「どの順番で依頼すべきか」「いつ催促すべきか」「クライアントの状態が変わったら次に何を依頼すべきか」が暗黙知

3. **クライアント別状態の追跡コストが高い**:
   pilotton の CAPI が完了したか / ECフォース 権限が来たか / 1st party CSV を受領したか — Excel/Notion で手管理

4. **5/7 提案の差別化が表現できない**:
   「24h 365 日、企業ごとに最適な提案を自動生成」という訴求が嘘になる

### 関連既存実装 + ADR

- `engine/indication_state.py` (Day 2、検知状態 DB)
- `engine/indication_filter.py` (Day 2、severity / cap / cooldown)
- `templates/chatwork/_action_steps.md.j2` (Day 3 G タスク、rule_id 別マクロ)
- ADR-009 (トレードオフ設計) の `client_preferences/{client_id}.yaml` 構想

---

## Decision

### D-1. **4 段構成パイプライン**: 診断 → ルール評価 → テンプレート選択 → 投稿

```
[1. 診断] outputs/client_state/{client_id}.yaml の現状読込
                ↓
[2. ルール評価] config/auto_proposal_rules.yaml の trigger 条件を順次評価
   ├ trigger 条件 (例: capi_setup_status == "not_started")
   ├ prerequisite チェック (例: ecforce_access_granted == true)
   ├ cooldown チェック (例: 同 rule の最終投稿から 7 日経過)
   └ 優先度ソート (priority 降順、複数 hit 時は上位 N 件のみ)
                ↓
[3. テンプレート選択] templates/chatwork/_client_request_*.md.j2 から
                ↓
[4. 投稿] notifiers/chatwork_notifier.py 経由で {client}_main ルームへ
   └ 投稿後: outputs/auto_proposal_history/{client_id}.yaml に記録
```

### D-2. クライアント状態管理 `outputs/client_state/{client_id}.yaml`

#### スキーマ

```yaml
client_id: pilotton
last_updated: 2026-05-05T20:30:00+09:00

# === 計測基盤 (measurement_foundation) ===
capi_setup_status: not_started         # not_started | in_progress | completed | verified
pixel_status: dormant                  # dormant | active | consolidated
domain_verification_status: not_started # not_started | dns_pending | completed
aem_events_configured: false           # AEM 8 イベント設定済み (※2025/6 仕様変更で形骸化)

# === EC プラットフォーム連携 ===
ec_platform: ecforce                   # ecforce | shopify | custom
ecforce_access_granted: false          # ECフォース 管理画面の閲覧権限
ecforce_fb_cv_api_configured: false    # FB CV API タブ設定済

# === 1st パーティ + Customer Audience ===
first_party_data_received: false       # CSV 受領済み
customer_audience_uploaded: false      # Meta Audiences アップロード済
lookalike_audience_created: false

# === AdTruth (LP 不正検知) ===
adtruth_tag_installed: false
adtruth_consent_banner_implemented: false
adtruth_privacy_policy_updated: false

# === 顧客側ヒアリング項目 ===
aov_jpy: null                          # ヒアリング前は null、確定後は実値
ltv_aov_ratio: null
retargeting_campaigns_active: null
brand_search_ads_running: null

# === Phase 進捗 ===
phase: A                               # A (内部レビュー) | B (本番) | C (拡張)
phase_a_started_at: 2026-05-04T09:00:00+09:00
phase_b_started_at: null

# === 自動投稿履歴サマリ ===
last_request_sent_at: null
last_request_rule_id: null
total_requests_sent: 0
```

#### 更新タイミング

- ChatWork 顧客返信を Claude API でパース (Phase B Week 2 以降)
- 山本が手動で更新 (`scripts/update_client_state.py --client pilotton --field capi_setup_status --value completed`)
- 自動検知 (Meta API のレスポンスから capi_setup_status を推定)

### D-3. 提案ルール定義 `config/auto_proposal_rules.yaml`

#### 各ルールの構造

```yaml
rules:
  - id: <unique_rule_id>
    name: <human_readable_name>
    priority: <int 0-100>          # 高いほど優先、同 priority は first_added 順
    template: <path to .md.j2>     # render する Jinja2 テンプレート
    trigger:                       # AND 条件 (全て true で発火)
      - <field>: <value>
      - <field>: <operator>: <value>   # 例: aov_jpy: ">": 0
    prerequisite:                  # AND 条件 (全て true でないと発火しない)
      - <field>: <value>
    cooldown_days: <int>           # 同 rule の前回投稿から何日待つか
    deadline_days: <int>           # 投稿時に「{deadline}日以内に」と表記する日数
    rationale: <short text>        # なぜこの依頼が必要かを 1 行
    skip_if:                       # OR 条件 (どれか 1 つ true なら skip)
      - <field>: <value>
```

#### 初期ルール 6 件 (5/5 実装着手分)

```yaml
rules:
  # === Rule 1: ECフォース 権限 (最優先、CAPI 連動の前提) ===
  - id: ecforce_access_request
    name: ECフォース 管理画面の閲覧権限ご依頼
    priority: 100
    template: templates/chatwork/_client_request_ecforce_access.md.j2
    trigger:
      - ecforce_access_granted: false
      - ec_platform: ecforce
    prerequisite:
      - phase: ["A", "B"]    # Phase A の内部レビュー終了後 or Phase B
    cooldown_days: 7
    deadline_days: 7
    rationale: "FB CV API 設定および AOV/LTV 自動取得の前提となる権限"

  # === Rule 2: CAPI 設定支援 (M02 解消条件) ===
  - id: capi_setup_request
    name: Meta CAPI 設定のご依頼
    priority: 95
    template: templates/chatwork/_client_request_capi_setup.md.j2
    trigger:
      - capi_setup_status: not_started
    prerequisite:
      - ecforce_access_granted: true   # ECフォース 権限が来ないと CAPI タブが開けない
      - domain_verification_status: completed
    cooldown_days: 5
    deadline_days: 14
    rationale: "M02 解消で月次 ¥50,000+ の効果回復、Pixel 休眠解消の起点"

  # === Rule 3: ドメイン認証 (M02 の前提) ===
  - id: domain_verification_request
    name: ドメイン認証 (DNS TXT) のご依頼
    priority: 90
    template: templates/chatwork/_client_request_domain_verification.md.j2
    trigger:
      - domain_verification_status: not_started
    prerequisite: []
    cooldown_days: 7
    deadline_days: 7
    rationale: "AEM 設定の前提条件、iOS14.5+ 配信改善の必須基盤"

  # === Rule 4: 1st パーティデータ受領 ===
  - id: first_party_data_request
    name: 顧客リスト (1st パーティ) のご共有
    priority: 80
    template: templates/chatwork/_client_request_first_party_data.md.j2
    trigger:
      - first_party_data_received: false
    prerequisite:
      - ecforce_access_granted: true   # ECフォース から CSV エクスポート可能になっている
    cooldown_days: 14
    deadline_days: 14
    rationale: "Lookalike 1-3% シードで新規 CPA -10〜15% の効果見込み"
    skip_if:
      - phase: A    # Phase A 内部レビュー期間中は依頼しない

  # === Rule 5: AdTruth LP タグ事前ヒアリング ===
  - id: adtruth_lp_questions
    name: 不正検知タグ設置のための事前ご質問
    priority: 60
    template: templates/chatwork/_client_request_adtruth_lp_questions.md.j2
    trigger:
      - adtruth_tag_installed: false
      - phase: B
    prerequisite:
      - first_party_data_received: true  # 計測基盤が整ってから
    cooldown_days: 30
    deadline_days: 21
    rationale: "Phase B Week 3-4 で実装する LP タグの法務 + 技術ヒアリング"

  # === Rule 6: AEM 設定 (旧仕様、現在は形骸化を確認するのみ) ===
  - id: aem_events_review
    name: AEM (集計イベント測定) の現状確認
    priority: 40
    template: templates/chatwork/_client_request_aem_review.md.j2
    trigger:
      - aem_events_configured: false
      - capi_setup_status: completed
    prerequisite:
      - domain_verification_status: completed
    cooldown_days: 30
    deadline_days: 14
    rationale: "2025/6 仕様変更で AEM 制限撤廃済、value 最適化 ON のみ確認"

  # === Rule 7: 月次レポート発信予告 (毎月 1 日) ===
  - id: monthly_report_summary
    name: 今月の運用サマリ送信予告
    priority: 50
    template: templates/chatwork/_client_request_monthly_summary_preview.md.j2
    trigger:
      - day_of_month: 1               # 月初のみ発火
    prerequisite:
      - phase: ["B", "C"]
    cooldown_days: 28
    deadline_days: null
    rationale: "毎月 1 日 10:00 の月次レポート (ADR-005) 投稿前の予告"
```

→ Phase B 開始時 (5/14) には Rule 1, 2, 3, 4 が同時 trigger 状態の可能性高い。priority 順に 1 日 cap 1 件で 4 日かけて投稿。

### D-4. 投稿頻度制御 (cooldown + 日次 cap)

```python
# 設計上の擬似コード
def evaluate_rules(state: dict, rules: list, history: dict, today: date) -> list:
    eligible = []
    for rule in rules:
        if not match_trigger(rule.trigger, state):
            continue
        if not match_prerequisite(rule.prerequisite, state):
            continue
        if match_skip_if(rule.skip_if, state):
            continue
        if not check_cooldown(rule, history, today):
            continue
        eligible.append(rule)
    
    # priority 降順、同点は first_added 順
    eligible.sort(key=lambda r: (-r.priority, r.id))
    
    # 日次 cap: 自動提案系は 1 日 1 件まで (ADR-005 の指摘 cap=3 と別枠)
    DAILY_CAP_PROPOSAL = 1
    return eligible[:DAILY_CAP_PROPOSAL]
```

### D-5. 投稿テンプレート設計

ファイル名: `templates/chatwork/_client_request_<rule_id>.md.j2`

#### 共通変数

```jinja2
{{ client_name }}              # 株式会社パイロットン
{{ client_short_name }}        # パイロットン
{{ today }}                    # 2026-05-05
{{ deadline }}                 # 2026-05-12 (today + deadline_days)
{{ rationale }}                # rule.rationale 文字列
{{ rule_id }}                  # capi_setup_request 等
{{ phase }}                    # A | B | C
{{ ec_platform }}              # ecforce | shopify | custom
```

#### 共通フォーマット (Zynect 自動投稿のトーン)

```jinja2
[info][title]【自動提案】{{ rule_name }}[/title]
[Zynect Auto-Reporter からの自動投稿]

{{ client_name }} 御中

弊社の自動診断システムが現在の運用状態を分析した結果、
以下の対応をお願いしたいと判断しました。

▼ ご依頼内容
　{{ request_body }}

▼ 重要度・期限
　・優先度: {{ priority_label }}
　・推奨完了期限: {{ deadline }} ({{ deadline_days }} 日以内)

▼ なぜ今これが必要か
　{{ rationale }}

{# 各テンプレート個別の本文 (具体手順) #}
{% block specific_content %}{% endblock %}

▼ ご質問・ご相談
　・本依頼に関する質問はこのスレッドにご返信ください
　・状況に変化があれば弊社運用担当 (山本) までご連絡ください

[Zynect Media 自動運用システム / {{ today }}]
[/info]
```

#### 6 ファイルの最低スケルトン (実装は 5/5)

```
templates/chatwork/
├── _client_request_ecforce_access.md.j2
├── _client_request_capi_setup.md.j2
├── _client_request_domain_verification.md.j2
├── _client_request_first_party_data.md.j2
├── _client_request_adtruth_lp_questions.md.j2
├── _client_request_aem_review.md.j2
└── _client_request_monthly_summary_preview.md.j2
```

各ファイルで `{% block specific_content %}` を実装、共通枠は `_client_request_base.md.j2` で extends 可能。

### D-6. `engine/auto_proposal_engine.py` 設計

```python
"""企業別自動提案エンジン (ADR-012)"""

from pathlib import Path
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
RULES_PATH = CONFIG_DIR / "auto_proposal_rules.yaml"
STATE_DIR = Path(__file__).resolve().parent.parent / "outputs" / "client_state"
HISTORY_DIR = Path(__file__).resolve().parent.parent / "outputs" / "auto_proposal_history"


def load_client_state(client_id: str) -> dict:
    """outputs/client_state/{client_id}.yaml を読み込み"""
    path = STATE_DIR / f"{client_id}.yaml"
    if not path.exists():
        return _empty_state(client_id)
    return yaml.safe_load(path.read_text())


def load_proposal_rules() -> list[dict]:
    """config/auto_proposal_rules.yaml を読み込み、priority 降順でソート済リストを返す"""
    data = yaml.safe_load(RULES_PATH.read_text())
    rules = data.get("rules", [])
    return sorted(rules, key=lambda r: -r.get("priority", 0))


def evaluate_rules(state: dict, rules: list, history: dict, today: str) -> list[dict]:
    """trigger / prerequisite / skip_if / cooldown を評価し、発火可能ルールを返す"""
    eligible = []
    for rule in rules:
        if not _match_trigger(rule.get("trigger", []), state, today):
            continue
        if not _match_prerequisite(rule.get("prerequisite", []), state):
            continue
        if _match_skip_if(rule.get("skip_if", []), state):
            continue
        if not _check_cooldown(rule, history, today):
            continue
        eligible.append(rule)
    return eligible


def check_prerequisites(rule: dict, state: dict) -> tuple[bool, list[str]]:
    """前提条件チェック、満たさない条件のリストも返す (デバッグ用)"""
    missing = []
    for cond in rule.get("prerequisite", []):
        for field, expected in cond.items():
            if state.get(field) != expected:
                missing.append(f"{field}=={expected} (actual: {state.get(field)})")
    return (len(missing) == 0), missing


def check_cooldown(rule: dict, history: dict, today: str) -> bool:
    """前回投稿日 + cooldown_days を超えていれば True"""
    rule_id = rule["id"]
    last = history.get(rule_id, {}).get("last_sent_at")
    if last is None:
        return True
    cooldown = int(rule.get("cooldown_days", 0))
    return _days_between(last, today) >= cooldown


def render_and_post(rule: dict, state: dict, client_cfg: dict, dry_run: bool = False) -> dict:
    """テンプレート rendering + ChatWork 投稿、結果 dict を返す"""
    from notifiers.chatwork_notifier import ChatWorkClient
    from templates.chatwork import render

    template_name = rule["template"].replace("templates/chatwork/", "")
    context = _build_context(rule, state, client_cfg)
    body = render(template_name, context)

    chat = ChatWorkClient(
        room_id=client_cfg["chatwork_rooms"]["main"],
        dry_run=dry_run,
    )
    result = chat.post_message(body)
    return {
        "rule_id": rule["id"],
        "result": result,
        "body_length": len(body),
    }


def update_state(client_id: str, posted_request: dict) -> None:
    """投稿後に history を更新、state にも last_request_sent_at を反映"""
    history_path = HISTORY_DIR / f"{client_id}.yaml"
    history = yaml.safe_load(history_path.read_text()) if history_path.exists() else {}
    history[posted_request["rule_id"]] = {
        "last_sent_at": _now_iso(),
        "result": posted_request["result"],
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(yaml.safe_dump(history, allow_unicode=True))

    # state の last_request_sent_at も更新
    state = load_client_state(client_id)
    state["last_request_sent_at"] = _now_iso()
    state["last_request_rule_id"] = posted_request["rule_id"]
    state["total_requests_sent"] = (state.get("total_requests_sent") or 0) + 1
    save_client_state(client_id, state)


def run_auto_proposal(client_id: str, dry_run: bool = False) -> dict:
    """メインエントリ — 1 クライアントの自動提案サイクル実行"""
    state = load_client_state(client_id)
    rules = load_proposal_rules()
    history = _load_history(client_id)
    today = _today()

    eligible = evaluate_rules(state, rules, history, today)
    DAILY_CAP = 1
    selected = eligible[:DAILY_CAP]

    posted = []
    for rule in selected:
        client_cfg = _load_client_cfg(client_id)
        result = render_and_post(rule, state, client_cfg, dry_run=dry_run)
        if not dry_run:
            update_state(client_id, result)
        posted.append(result)

    return {
        "client_id": client_id,
        "eligible_count": len(eligible),
        "posted_count": len(posted),
        "skipped_count": len(eligible) - len(posted),  # 日次 cap 超過分
        "posted": posted,
    }
```

### D-7. `scripts/run_auto_proposal.py` 設計

```python
"""日次自動提案ジョブ (ADR-012)

実行: venv/bin/python3 scripts/run_auto_proposal.py [--client pilotton] [--all] [--dry-run]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.auto_proposal_engine import run_auto_proposal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="クライアント ID (省略時は --all 必須)")
    parser.add_argument("--all", action="store_true", help="全アクティブクライアント実行")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.client and not args.all:
        parser.error("--client または --all が必要")

    targets = _resolve_targets(args)
    for client_id in targets:
        result = run_auto_proposal(client_id, dry_run=args.dry_run)
        print(f"[{client_id}] eligible={result['eligible_count']} posted={result['posted_count']}")
```

#### launchd 統合 (Phase B Week 2)

`scripts/launchd/com.zynect.bpo.daily-proposal.plist` (新規):
- 毎朝 09:30 JST 実行 (daily_chatwork_check の 09:00 と 30 分ずらす)
- 全アクティブクライアントを `--all` で対象化

### D-8. `scripts/setup/new_client.py` 設計

```python
"""新クライアント追加スクリプト (ADR-011 + ADR-012)

実行: bash scripts/setup/new_client.py \
        --client xxx \
        --name "株式会社XXX" \
        --industry beauty_d2c \
        --chatwork-room <room_id> \
        --ec-platform ecforce \
        --meta-account-id act_XXX
"""
import argparse
import yaml
from pathlib import Path

# 1. config/clients.yaml に新エントリ追加
# 2. outputs/client_state/{client_id}.yaml 初期化 (defaults)
# 3. outputs/auto_proposal_history/{client_id}.yaml 初期化 (空)
# 4. outputs/chatwork_state/{client_id}_indications.json 初期化 (空)
# 5. .env.example に CHATWORK_ROOM_ID_<CLIENT> + META_ACCESS_TOKEN_<CLIENT> 追記
# 6. 初回 auto_proposal を dry-run 実行 → 投稿予定の確認
# 7. 山本の確認後、本番実行で kickoff 投稿

# 完了時間目標: 山本作業 5 分 + スクリプト実行 5 分 = 10 分
```

---

## Alternatives Considered

### A-1. Claude API による自由文生成

**却下理由**:
- 文面のばらつきで顧客に不信感
- API 課金が予測不能
- ルール YAML + 固定テンプレートで十分な品質を保てる
- ただし「顧客返信のパース」は Phase B Week 3 以降で Claude を補助使用予定

### A-2. ルールエンジンに Drools / RETE を導入

**却下理由**:
- 6-30 ルール程度の規模では overkill
- Python 単純評価で十分高速 (1 client 評価 < 10ms)
- 学習曲線で開発工数増

### A-3. 状態管理を SQL DB (SQLite/PostgreSQL) で

**却下理由**:
- 既存 `outputs/chatwork_state/` が JSON ベース、整合性のため YAML/JSON 続行
- 状態 1 client = 数十フィールドで DB は overkill
- Phase C で複数クライアント並列 100+ になったら SQLite 移行検討

---

## Result (実装後の確認指標)

| 指標 | 期待値 | 計測方法 |
|------|--------|---------|
| 自動提案投稿の到達率 | 100% (Bot 投稿失敗 0 件) | logs/auto-proposal.err.log |
| 顧客返信率 | 70% 以上 (依頼から 3 日以内) | client_state.yaml の更新タイミング |
| 誤判定率 (顧客から「不要」と回答された割合) | 5% 以下 | rule.skip_if の網羅性指標 |
| 新クライアント追加所要時間 | 10 分以内 | scripts/setup/new_client.py 実測 |
| Phase B 30 client 達成時の運用工数 | 山本工数 < 1 日/週 | Phase C 移行判断材料 |

---

## Tradeoffs / Risks

### T-1. ルール定義の保守コスト

- 30 client × 8 ルール = 240 ケースの組み合わせを yaml で管理
- **緩和策**: 「クライアント横断で適用するルール」と「クライアント固有」を分離 (`auto_proposal_rules.yaml` 共通 + `clients/{id}/custom_rules.yaml` 個別)

### T-2. trigger 条件の誤検知

- 状態フィールドの不整合 (例: capi_setup_status="completed" だが Pixel は実際休眠中) で誤発火
- **緩和策**: trigger に「Meta API での裏付け確認」を組み合わせる (例: capi_setup_status=completed AND meta_api.recent_capi_events > 100)

### T-3. cooldown による依頼遅延

- 顧客対応待ちで cooldown 7 日を待つと、優先度高なのに次の依頼が滞留
- **緩和策**: cooldown は「同一 rule_id」のみ対象、別 rule は priority 順に並列発火可能

### T-4. 状態管理ファイルの破損

- yaml.safe_load 失敗時に全 rule が evaluate されないリスク
- **緩和策**: state ファイルは atomic write (`tmp + rename`)、破損時は前日バックアップから復元

### T-5. 顧客のテキスト返信解析

- ChatWork 返信を構造化データに変換するパース層が複雑
- **緩和策**: Phase B Week 1-2 はキーワードベース、Phase B Week 3 以降に Claude API フォールバック (ADR-009 §7.6 と同じパターン)

---

## 5/7 提案資料への影響範囲 (差別化文案)

### 訴求ポイント (3 つ)

1. **企業別の自動診断 → 自動提案 → 自動投稿の 3 段運用**:
   - 競合代理店: 「営業担当の経験」依存
   - Zynect: 「自動提案エンジン」が 24h 365 日稼働、抜け漏れゼロ

2. **人間は例外対応・大型判断のみ介入**:
   - Routine な依頼 (CAPI / ECフォース 権限 / 1st party 等) は完全自動
   - 山本は「想定外の数値変動」「クライアント直接相談」のみ対応
   - 結果: 1 担当者が 30 client を管理可能

3. **新クライアント追加 10 分でフル運用開始**:
   - `scripts/setup/new_client.py` で yaml + state + room 作成を一括
   - 競合代理店: 「初回ヒアリング 1 週間 + 初回提案 1 週間」
   - Zynect: kickoff day から自動診断スタート、1 ヶ月で全提案項目を投げ切る

### 提案資料記載例 (proposal_v3.md 追記候補)

```markdown
## Zynect 自動運用エンジンの差別化要素

弊社が他代理店と異なる本質的な差は、運用の自動化レイヤーです。

### 1. 24h 365 日の自動診断
- 5 媒体 277 ルールの監査を毎朝 09:00 に自動実行
- 検知された問題は 3 日連続クリーン確認で自動完了処理
- 月次レポートも 1 日 10:00 に自動生成・自動送信

### 2. 企業別の自動提案エンジン
- クライアントの状態 (CAPI 設定 / ECフォース 権限 / 1st party データ等) を
  常時監視し、次に必要な依頼を自動で生成
- 提案ルール 8 種 × クライアント数 = 全件を自動管理
- 山本 1 名が 30 クライアントを並列運用可能

### 3. 横展開性 (10 分で新クライアント運用開始)
- 新規契約クライアントは kickoff day で初期化
- ChatWork ルーム作成 + 状態初期化 + 初回提案投稿まで 10 分以内
- 競合代理店の「初回提案まで 2-3 週間」と比べ圧倒的な速度

### 競合代理店との比較表

| 項目 | 競合代理店一般 | Zynect Media |
|------|------------|------------|
| 監査の頻度 | 月 1 回 (担当者手動) | **毎日 09:00 自動** |
| 提案生成 | 営業の判断 (ばらつきあり) | **8 ルール × 状態評価で自動** |
| 完了確認 | 営業からの催促 (2-3 回) | **3 日連続クリーン自動検知** |
| 月次レポート | 営業が Excel で手作成 | **PDF 自動生成 + ChatWork 自動添付** |
| 新規開始 | 2-3 週間 | **10 分** |
| 担当者 1 名の対応上限 | 5-10 client | **30 client** |
```

---

## Implementation Plan (5/5 〜 5/14)

### 5/5 月 (1 日完結予定)

| 時刻 | 担当 | 作業 |
|------|------|------|
| 朝 | 山本 | ChatWork で Zynect Auto-Reporter Bot アカウント作成 + Bot トークン .env 書込 (ADR-011 D-1) |
| 朝〜昼 | Claude Code | `engine/auto_proposal_engine.py` 実装 (~400 行) |
| 朝〜昼 | Claude Code | `config/auto_proposal_rules.yaml` 初期 7 ルール記述 (~150 行) |
| 朝〜昼 | Claude Code | `templates/chatwork/_client_request_*.md.j2` 7 ファイル作成 (~50 行 × 7) |
| 朝〜昼 | Claude Code | `scripts/run_auto_proposal.py` 実装 (~80 行) |
| 朝〜昼 | Claude Code | `scripts/setup/new_client.py` 実装 (~150 行) |
| 朝〜昼 | Claude Code | `tests/test_auto_proposal_engine.py` 実装 (~250 行、12 ケース想定) |
| 昼 | Claude Code + 山本 | pilotton の `outputs/client_state/pilotton.yaml` 初期化 |
| 昼 | Claude Code | `--dry-run` で初回 auto_proposal 実行 → 投稿予定 7 件を確認 |
| 夕 | 山本 | dry-run 確認後、本番実行 → ChatWork rid 435851481 へ初回投稿 |

工数概算: Claude Code 実装 6h + 山本確認 1h = **計 7h**

### 5/6 火 〜 5/13 月

- 内部レビュー期間継続 (Phase A)
- pilotton への自動投稿運用開始
- 顧客返信から状態更新の手動運用 (Claude API パースは Phase B Week 3 以降)

### 5/14 火 (Phase B 開始日)

- pilotton 担当者を ChatWork ルームに招待
- ルーム名変更: `パイロットン ad通知パイプライン` → `株式会社パイロットン 自動運用通知 (Zynect Media)`
- `[テスト]` プレフィクス削除 → 本番化

---

## References

- ADR-005: [ChatWork 経由の指摘・完了・月次運用ループ](./ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-009 (Draft): [トレードオフ設計 + 顧客選好学習](../architecture/tradeoff_design.md)
- ADR-011: [ChatWork 自動通知グループの設計](./ADR-011-chatwork-auto-notification-group.md)
- 既存実装: `engine/indication_state.py` (state 管理パターンの参考)
