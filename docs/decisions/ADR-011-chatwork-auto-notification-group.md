# ADR-011: ChatWork 自動通知グループの設計

| 項目 | 値 |
|------|---|
| **Status** | Proposed (実装着手 5/5) |
| **Decision Date** | 2026-05-05 |
| **Authors** | 山本 (要件提示) / Claude Code (設計) |
| **Related ADRs** | ADR-005 (ChatWork ループ), ADR-012 (自動提案エンジン) |

---

## Context

### 現状の問題

現在 pilotton への運用通知は ChatWork ルーム rid `435851481`「パイロットン ad通知パイプライン」に投稿されているが、以下 3 つの本質的問題がある:

1. **「人間 (山本) からの DM」と区別がつかない**
   - 投稿主が山本のスタッフアカウント由来のため、Zynect の自動運用システムからの投稿だと顧客が認識できない
   - 結果として「Zynect システムが自動診断 → 自動提案している」という付加価値が伝わらない

2. **5/7 提案の差別化ポイントが訴求できない**
   - 競合代理店との差は「24h 365 日の自動診断 + 自動提案ループ」
   - これを示すには「Zynect システム」名義の通知が顧客に届いていることが必要

3. **横展開性の不足**
   - 新クライアント追加時に「ChatWork ルーム作成 + bot 招待 + 通知設定」が手動
   - 30 クライアント突破時に手作業が破綻

### 関連既存実装

- `notifiers/chatwork_notifier.py` (Day 1 実装、ChatWork API v2 クライアント)
- `scripts/daily_chatwork_check.py` (Day 3 実装、launchd 09:00 自動起動)
- 環境変数: `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID_PILOTTON` / `CHATWORK_ROOM_ID_STAGING`

---

## Decision

### D-1. ChatWork 専用 Bot アカウント方式 (Zynect Auto-Reporter)

**決定**: 山本のスタッフアカウントとは別に、**「Zynect Auto-Reporter」専用 ChatWork アカウントを発行**して bot として運用する。

理由:
1. 投稿主が「Zynect Auto-Reporter」と表示され、人間からの DM と一目で区別可能
2. 山本のアカウントが停止/退職時にも継続運用可能 (依存解消)
3. 顧客への透明性 (= 自動運用の価値訴求)

### D-2. クライアント別 ChatWork ルーム集約方針 (1 client = 1 main room)

**決定**: クライアント単位に **1 つの「main」グループルーム** を作成し、以下 4 種の自動投稿を集約する。

| 投稿カテゴリ | 既存 (Day 1-3) | 本 ADR で追加 |
|------------|---------------|--------------|
| 日次指摘通知 (daily_indication) | ✅ | 継続 |
| 完了通知 (completion_notice) | ✅ | 継続 |
| 月次レポート (monthly_report) | ✅ | 継続 |
| **企業別自動提案 (client_request_*)** | — | ✨ **新規** (ADR-012 連携) |

**集約 vs 分離の比較**:

| 案 | メリット | デメリット | 採否 |
|----|---------|-----------|------|
| (A) 単一 room 集約 | 顧客側のチャンネル管理が簡単、通知漏れなし | 投稿が多くなりノイズ感 | ✅ **採用** |
| (B) カテゴリ別分離 (daily / completion / proposal の 3 room) | カテゴリで整理しやすい | 顧客が 3 ルームを管理、招待コスト 3 倍 | ❌ |
| (C) 重要度別分離 (critical / regular の 2 room) | critical を見逃さない | 区分判定が難しい、グレーゾーン投稿が出る | ❌ |

**結論**: (A) 単一 room 集約 + 投稿先頭の `[info][title]` プレフィックスで視覚的に分類。

### D-3. ルーム命名規則

```
[本番ルーム名]   {会社名} 自動運用通知 (Zynect Media)
[ステージング]   {会社名} 自動運用通知 (Zynect Media、テスト)
```

例:
- `株式会社パイロットン 自動運用通知 (Zynect Media)`
- 既存の `パイロットン ad通知パイプライン` は移行 or 改名

### D-4. Bot 認証・権限設計

#### Bot アカウント設定

```yaml
chatwork_bot:
  account_name: "Zynect Auto-Reporter"
  email: "auto-reporter@zynectmedia.com"  # 専用メアド
  api_token_env: "CHATWORK_BOT_API_TOKEN"  # 既存の CHATWORK_API_TOKEN とは別管理
  
  permissions:
    - メッセージ投稿 (necessary)
    - ファイル添付 (月次レポート PDF 用)
    - メンション送信 (To: 機能、緊急時のみ)
    - メッセージ削除 (テスト投稿撤回用、本番では行使しない)
```

#### 既存トークンとの分離理由

- 山本の個人アカウントトークン (`CHATWORK_API_TOKEN`) は手動運用専用に温存
- 自動投稿は新トークン (`CHATWORK_BOT_API_TOKEN`) で完全分離
- 万一 bot が暴走してもスタッフアカウントは無事

### D-5. クライアント別ルーム ID 管理

#### `config/clients.yaml` 拡張

```yaml
clients:
  pilotton:
    company:
      name: 株式会社パイロットン
      industry: beauty_d2c
    chatwork_rooms:
      main: 435851481           # 既存ルームを「main」として再活用
      staging: 435851481        # 内部レビュー期間中は本番と共用、後日分離
    chatwork_bot_token_env: "CHATWORK_BOT_API_TOKEN"
    # ↑ クライアント横断で 1 トークン (将来クライアント別トークンも可能)
```

#### `.env.example` 拡張

```bash
# Zynect Auto-Reporter (専用 Bot アカウント、ADR-011)
CHATWORK_BOT_API_TOKEN=

# クライアント別ルーム ID (legacy: 既存 CHATWORK_ROOM_ID_PILOTTON は維持)
CHATWORK_ROOM_ID_PILOTTON=435851481

# 新クライアント追加時はここに追記
# CHATWORK_ROOM_ID_<CLIENT_ID_UPPERCASE>=
```

### D-6. 横展開性 (新クライアント追加手順)

新クライアント追加時の自動化フロー (詳細は ADR-012 + `scripts/setup/new_client.py`):

```
1. ChatWork Bot (Zynect Auto-Reporter) で新ルーム作成
   └ ルーム名: "{会社名} 自動運用通知 (Zynect Media)"
   └ 顧客側担当者を招待

2. bash scripts/setup/new_client.py \
       --client {client_id} \
       --chatwork-room {room_id} \
       --industry {beauty_d2c | ec_retail | etc} \
       --ec-platform {ecforce | shopify | custom}

3. スクリプトが自動実行:
   ├ config/clients.yaml にエントリ追加
   ├ outputs/chatwork_state/{client_id}_indications.json 初期化
   ├ outputs/client_state/{client_id}.yaml 初期化 (ADR-012)
   └ 初回 auto_proposal 実行 → ChatWork に「kickoff 挨拶 + 初期診断」投稿

完了時間目標: 10 分以内 (山本作業 5 分 + スクリプト実行 5 分)
```

### D-7. 投稿タイトル接頭辞ルール (視覚的分類)

ChatWork は Markdown 不可だが `[info][title]...[/title]` 構文で囲める。タイトル先頭に分類タグを付ける:

| 分類 | タイトル例 |
|------|-----------|
| 日次指摘 | `【自動診断】2026-05-05 — 本日の運用指摘 (3件)` |
| 完了通知 | `【自動診断】2026-05-05 — 指摘解消のお知らせ (M02 CAPI 実装完了)` |
| 月次レポート | `【月次レポート】2026-05 月次運用レポート` |
| **依頼系自動提案** | `【自動提案】ECフォース管理画面の閲覧権限ご依頼` |

接頭辞:
- `【自動診断】` — 検知・診断結果
- `【月次レポート】` — 月次サマリ
- `【自動提案】` — 依頼・確認事項

→ 顧客が ChatWork の「メッセージ検索」で `【自動診断】` を絞り込めば過去の指摘履歴が一覧化される。

### D-8. 内部レビュー期間 (Phase A 〜 14 日) の運用

- 既存の `[テスト]` プレフィクスは継続 (= `CHATWORK_TEST_PREFIX="[テスト] "`)
- 山本 1 名のみ pilotton ルームに参加、Bot 投稿の文面・誤検知率を観察
- 14 日経過 + 誤検知率 5% 以下確認後:
  - `CHATWORK_TEST_PREFIX=` (空に変更) で本番化
  - pilotton 担当者を kickoff day で招待
  - **ルーム名変更**: 旧「パイロットン ad通知パイプライン」 → 新「株式会社パイロットン 自動運用通知 (Zynect Media)」

---

## Alternatives Considered

### A-1. 山本のスタッフアカウントを bot として継続使用

**却下理由**:
- 「人間からの DM」と区別がつかず、自動運用の付加価値が伝わらない
- 山本退職時の運用継続性なし
- 個人 token 流出リスクが運用機能と分離されていない

### A-2. 各カテゴリ (daily / completion / monthly / proposal) で別ルーム

**却下理由**:
- 顧客が 4 ルームを管理する負担
- 招待・権限設定が 4 倍
- 「全ての通知を 1 ヶ所で見られる」体験が損なわれる

### A-3. ChatWork 以外 (Slack / Lark / 自社 LINE 公式アカウント)

**却下理由**:
- 既存資産 (notifiers/chatwork_notifier.py + 全テンプレ) が ChatWork 前提
- pilotton の業務標準が ChatWork (5/3 確認済)
- 新規プラットフォーム導入は Phase B 以降の検討事項

### A-4. ChatWork API ではなく ChatWork Webhook で受信のみ

**却下理由**:
- Webhook は「受信」のみで投稿不可、本要件 (システムから投稿) を満たさない
- 顧客が Webhook URL を Zynect 側に提供するセキュリティ手間も発生

---

## Result (実装後の確認指標)

| 指標 | 期待値 | 計測方法 |
|------|--------|---------|
| 自動投稿の認識率 | 顧客アンケートで「Zynect システムから来ている」と認識: 100% | kickoff 1 ヶ月後ヒアリング |
| 通知漏れ件数 | 0 件 (Bot トークン期限切れ等で投稿失敗ゼロ) | logs/daily-chatwork.err.log 監視 |
| 新クライアント追加所要時間 | 10 分以内 | scripts/setup/new_client.py 実測 |

---

## Tradeoffs / Risks

### T-1. ChatWork Bot アカウントの月額コスト

- Free プラン: ファイル添付 5MB/月の制限あり、bot 専用アカウントを作る場合は別契約
- **緩和策**: 当面は山本のアカウント傘下で「コンタクト」として bot 用メアド招待、月次レポート PDF サイズが膨らんだら Business プラン (10GB / 月額 ¥600) に移行

### T-2. Bot トークン管理リスク

- 専用 token を `.env` に保管、流出時の被害は「Bot 投稿の偽装」のみ (個人 DM へのアクセス不可)
- **緩和策**: 6 ヶ月ごとにトークンローテーション (既存 `scripts/setup/write_chatwork_token.sh` を流用)

### T-3. ルーム名変更時の混乱

- 既存ルーム名 (パイロットン ad通知パイプライン) を変更すると、顧客側の通知設定や ChatWork 検索履歴に影響
- **緩和策**: kickoff day に「ルーム名を統一規則に変更します」と事前アナウンス

### T-4. 単一ルーム集約による「投稿過多」リスク

- 1 日に 3-5 投稿が来る可能性 (daily 1 + completion 1-2 + 自動提案 0-2)
- **緩和策**:
  - 日次 cap 3 件 (ADR-005)
  - 自動提案の cooldown_days (ADR-012)
  - 月次レポートは 1 ファイル添付に集約

---

## Implementation Plan (5/5 〜)

| Day | タスク | 担当 |
|-----|------|------|
| 5/5 朝 | ChatWork で Zynect Auto-Reporter Bot アカウント作成 | 山本 |
| 5/5 朝 | pilotton 用 Bot トークン発行 → `scripts/setup/write_chatwork_token.sh` で .env 書込 | 山本 |
| 5/5 昼 | `config/clients.yaml` に `chatwork_rooms.main` フィールド追加 | Claude Code |
| 5/5 昼 | `notifiers/chatwork_notifier.py` で env キー切替対応 (CHATWORK_BOT_API_TOKEN を優先、なければ既存トークン) | Claude Code |
| 5/5 昼 | テスト 5 件追加 (新トークン優先、ルーム ID 解決) | Claude Code |
| 5/5 夕 | E2E: ChatWork 投稿が「Zynect Auto-Reporter」名義で届くか目視確認 | 山本 |

---

## References

- ADR-005: [ChatWork 経由の指摘・完了・月次運用ループ](./ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-012: [企業別自動提案エンジンの設計](./ADR-012-auto-proposal-engine.md)
- ChatWork API: <https://developer.chatwork.com/reference>
- 既存実装: `notifiers/chatwork_notifier.py` / `scripts/daily_chatwork_check.py` / `templates/chatwork/`
