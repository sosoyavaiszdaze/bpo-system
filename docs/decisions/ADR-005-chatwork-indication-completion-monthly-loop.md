# ADR-005: ChatWork 経由の指摘・完了・月次運用ループ

| 項目 | 値 |
|------|---|
| **Status** | Accepted |
| **Decision Date** | 2026-05-03 |
| **Authors** | 山本 (Zynect Media) / Claude Code (実装) |
| **Related ADRs** | ADR-001, ADR-002, ADR-003, ADR-004 |

---

## Context

Day 1〜Day 5.3 で v3 PDF レポートと提案 pptx の生成パイプラインは完成した。次の課題は
「**生成されたレポートを顧客 (パイロットン) に届け、改善が完了したかを継続観察し、
月次サマリで合算する運用ループを自動化する**」こと。

### 既存の課題

1. **PDF を作っても届けないと意味がない** — 顧客が PDF を見るタイミングは月 1 回程度に偏在し、
   日次の小さな変化（CV 急減、Pixel 異常等）に気づくのが遅れる
2. **Slack は社内専用** — 顧客とのコミュニケーションは ChatWork が業界慣習 (BPO 文脈)
3. **「直したかどうか」を BPO 側が能動確認するコストが高い** — 担当者が毎週手動で確認するのは
   30 クライアント突破時に破綻する
4. **改善効果の月次合算がない** — 個別指摘の効果は出ても、月次でいくら改善したかを
   顧客に提示する仕組みがない (営業面・契約継続の説得力に直結)
5. **改善手順は外部リンク (Notion / Drive) に置くと依存が増える** — 顧客側の閲覧権限管理、
   リンク切れリスク、スマホ閲覧性が課題

### 既存実装との関係

- `notifiers/chatwork_notifier.py`: Day 1 で実装済み (テキスト投稿、ファイル添付、idempotency)
- `templates/chatwork/`: Day 1 で 3 種テンプレート (daily_indication / completion_notice / monthly_report)
- `engine/indication_state.py`: Day 2 で実装済み (JSON 状態 DB、indication_id 安定生成)
- `engine/indication_filter.py`: Day 2 (severity / 日次 cap / cooldown)
- `engine/indication_detector.py`: Day 2 (analyzer 出力 → 統一 indication 形式)
- `analyzers/judgment_db.py` の JSON ファイルベース DB パターンを完全踏襲

---

## Decision

### D-1. ChatWork 1 ルーム運用 (Phase A)

- パイロットン専用ルーム rid `435851481` (パイロットン ad通知パイプライン) を本番ルームとして使用
- ステージングルームは作成せず、本番ルーム上で `[テスト] ` プレフィクス付き投稿で内部レビュー
- **内部レビュー期間 (最初の 2 週間)**: 山本 1 名のみが参加、誤検知率 5% 以下の確認を 3 軸で実施
  - 軸1: false-positive rate < 5% (誤検知率)
  - 軸2: 文面の自然さ (担当者目線で違和感がないか)
  - 軸3: リンク到達性 (本方針では「リンクなし」のため対象外、テンプレ展開の正しさで代替)
- 内部レビュー完了後、pilotton 担当者を**契約日ではなく kickoff day** に招待して本番化

### D-2. 改善手順は ChatWork 本文内で完結 (G タスク)

「指摘の改善手順を外部資料 (docs/guides/, Notion, Drive) に置かず、**ChatWork メッセージ本文内で完結**」を採用。

理由:
1. パイロットン担当者が指摘を見たその場で手順が読める
2. private リポジトリ / Notion / Google Drive 等の**外部依存ゼロ**
3. Jinja2 テンプレート更新だけでメンテ完結
4. スマホでも読みやすい

実装:
- `templates/chatwork/_action_steps.md.j2` を新設し、rule_id ごとに改善手順を Jinja2 マクロ化
- 主要 5 rule_id (CAPI / Pixel休眠 / ドメイン認証 / AEM / 1stパーティ) は本文に詳細手順を展開
- それ以外は「別途ご相談ください」のフォールバック表示
- 既存 alias (M01 / M02 / M04 / M09 / M61) も同じ手順にマップ

### D-3. 完了検知ロジック (3 日連続クリーン + 一時欠損ガード)

- status 遷移: `open` → `resolved_pending(clean=1)` → `clean=2` → `clean=3` → `resolved_confirmed`
- 同一 (rule_id, platform, target_id) で前回 `resolved_confirmed` から **7 日間 cooldown**
- 一時的なデータ欠損時 (`data_available=False`) は clean カウントを進めない
  → 偶発的な API 失敗で「直った」と誤判定するのを防ぐ
- `resolved_pending` 中に再検知された場合は `open` に巻き戻し、clean カウントリセット (regressed イベント)

### D-4. indication_id 安定生成

形式: `{client_id}:{rule_id}:{platform}:{target_id}:{first_detected_date}`

`first_detected_date` を含めることで、「同じ事象が解消 → 再発した場合」を**別 ID 扱い**にする。
これにより:
- 履歴の分離が明確 (1 回目の解消と 2 回目の検知を別記録として残す)
- cooldown 判定は別 ID 同士でも `latest_resolved_for(rule, platform, target)` で行う

### D-5. 通知抑制 3 軸

`engine/indication_filter.py` で以下 3 軸を適用:
1. **severity 上限**: critical + high のみ通知 (medium / low は state には残すが通知しない)
2. **日次 cap 3 件**: 1 日に通知する新規 indication は最大 3 件 (既通知件は控除)
3. **cooldown 7 日**: 同 (rule, platform, target) で前回 resolved_confirmed から 7 日未満は再通知しない

優先順: critical > high、同一 severity 内は first_detected_at 古い順 (先勝ち)

### D-6. スケジューラ統合

`integrations/scheduler.py` (APScheduler / `Asia/Tokyo`) に以下 2 ジョブ追加:
- **日次 09:00 JST**: `daily_chatwork_check`
- **月次 1日 10:00 JST**: `monthly_chatwork_report`

`.env` の `CHATWORK_TEST_PREFIX` で内部レビュー期間と本番運用を切替。
launchd plist (`~/Library/LaunchAgents/com.zynectmedia.bpo-scheduler.plist`) で常駐起動。

### D-7. 月次レポートは社内共有用として PDF 添付継続

ChatWork 本文に月次サマリ + 既存 v3 PDF を添付 (Free プラン月 5MB 制約をログ警告)。
PDF が 5MB を超える場合は Business プラン (10GB) への移行を検討。

### D-8. 自己監視 (Self-monitoring)

致命的失敗時 (`scripts/daily_chatwork_check.py` の main 例外) は `post_self_alert()` で
ChatWork に「🚨 BPO System 自己監視アラート」を投稿。
idempotency キー (`self_alert:YYYY-MM-DD:hash`) により同一エラーは 1 日 1 回まで。

---

## Alternatives Considered

### A-1. Slack 連携で代替

- 却下理由: パイロットン側が ChatWork を業務標準で使っているため、別 IM への招待は摩擦が大きい
- BPO 業界の慣習 (顧客側ツールに合わせる) を尊重

### A-2. 改善手順を docs/guides/ 配下の md ファイルで提供 (旧計画)

- 当初は `docs/guides/{capi_implementation,pixel_cleanup,domain_verification,...}.md` を作成し、
  ChatWork 本文に GitHub URL を貼る計画だった
- **却下理由**: private リポジトリでは閲覧不可、public 化はコンプラ的に NG、
  Notion / Drive 連携は外部システム依存が増える、スマホで GitHub の md は読みづらい
- 採用案 (D-2): Jinja2 マクロで本文展開すれば外部依存ゼロ、メンテも 1 ファイル更新で完結

### A-3. ステージングルームと本番ルームを分離

- 却下理由: 山本 1 名内部レビュー期間中はルーム数を増やすメリットが薄い、
  `[テスト] ` プレフィクスで識別可能であれば 1 ルームで十分
- pilotton 担当者を招待する本番化タイミングで、過去 [テスト] 投稿は手動削除可能

### A-4. ChatWork API 直叩き (テンプレートなし)

- 却下理由: 文面の品質統制 (改行・絵文字・コードブロック) が個別実装では崩れる、
  Jinja2 で集約することで a/b/c 全テンプレートを 1 箇所で更新可能

### A-5. クリーン判定を 1 日連続で確定

- 却下理由: API データの一時的欠損や日次の自然変動を「解消」と誤判定するリスクが高い
- 採用案 (D-3): 3 日連続 + data_available ガードで誤検知率を < 5% 目標

---

## Result

### 実装ファイル (Day 1-3 完了時点)

| 区分 | ファイル | 行数 | テスト件数 |
|------|---------|------|----------|
| A1 | `notifiers/__init__.py` | 5 | — |
| A2 | `notifiers/chatwork_notifier.py` | 300 | 7 (mock) |
| B1 | `templates/chatwork/__init__.py` | 50 | — |
| B2 | `templates/chatwork/daily_indication.md.j2` | 30 | (G で更新) |
| B3 | `templates/chatwork/completion_notice.md.j2` | 35 | 1 |
| B4 | `templates/chatwork/monthly_report.md.j2` | 50 | 1 |
| C1 | `engine/indication_state.py` | 280 | 5 |
| C2 | `engine/indication_filter.py` | 105 | 4 |
| C3 | `engine/indication_detector.py` | 220 | 2 |
| D1 | `scripts/daily_chatwork_check.py` | 230 | (E2E) |
| D2 | `integrations/scheduler.py` (拡張) | +60 | — |
| E1 | `engine/monthly_aggregator.py` | 200 | (E2E) |
| E2 | `scripts/monthly_chatwork_report.py` | 180 | (E2E) |
| G1 | `templates/chatwork/_action_steps.md.j2` | 175 | 13 (rule_id 別) |

合計テスト件数: 33 件 (Day 1: 12, Day 2: 14, Day 3 G: 7 増)
全 pytest: 全件 PASS

### 投稿サンプル (smoke test 実施済み)

- Step a (daily_indication): `message_id=2102698275256926208`
- Step c (completion_notice): `message_id=2102698276414558208`
- Step d (file attach, pilotton_report_v3.pdf 2.55MB): `file_id=2047859578`
- Idempotency 動作確認: 2 回目実行で全 3 件 SKIPPED ✅

---

## Tradeoffs / Risks

### T-1. ChatWork Free プラン 5MB/月 上限

- 月次レポート (PDF 2-3MB) を毎月 + 日次のテキスト投稿で枠を圧迫
- **緩和策**: Business プラン (10GB) への移行を kickoff 後に検討、それまでは月 1 回の PDF 添付のみ

### T-2. 1 ルーム運用の混雑リスク

- 日次 + 月次 + 自己監視通知が同じルームに集約される
- **緩和策**: `[テスト] ` プレフィクスで識別、本番化後は ChatWork のフィルタ機能で「自己監視」を別タブに

### T-3. cooldown 7 日の硬直性

- 本当に再発した重大事象を「cooldown 中」で抑止する可能性
- **緩和策**: severity=critical は cooldown 半減 (3 日) に Day 4+ で拡張可能、現状はシンプル維持

### T-4. clean 判定 3 日が長い

- 即座に直った指摘も 3 日待つため、解消通知のラグが最大 3 日
- **緩和策**: 顧客視点ではむしろ「確実に直ったこと」を重視するため、3 日のラグは妥協範囲
- それでも待たせたくない場合は「即時通知 (1 日 clean)」モードを将来的にオプション化可能

### T-5. 外部リンク削除によるテンプレファイル肥大化

- `_action_steps.md.j2` が 175 行と大きい
- **緩和策**: rule_id 別マクロで分割済み、追加 rule_id は別マクロを並べるだけで済む

### T-6. M01 / M02 等の旧 ID と新 ID (DQ-CAPI-MISSING 等) の二重管理

- マクロ内で alias 対応しているが、将来的に rule_id 体系を統一すべき
- **緩和策**: Phase B Week 2 で rule_id 命名規則の ADR を別途検討

---

## Implementation

### 主要ファイル

- 通知層: `notifiers/chatwork_notifier.py:1-300`
- テンプレ: `templates/chatwork/_action_steps.md.j2:1-175`
- 状態管理: `engine/indication_state.py:1-280`
- フィルタ: `engine/indication_filter.py:1-105`
- 検出: `engine/indication_detector.py:1-220`
- 月次集計: `engine/monthly_aggregator.py:1-200`
- 日次ジョブ: `scripts/daily_chatwork_check.py:1-230`
- 月次ジョブ: `scripts/monthly_chatwork_report.py:1-180`
- スケジューラ: `integrations/scheduler.py:1-280`
- セットアップ手順: `docs/operations/chatwork_scheduler_setup.md`

### Out of Scope (明示的に対象外)

ADR-005 では以下は **対象外** とする:

1. **`docs/guides/` 配下の手順書作成** — D-2 で本文化方針を採用したため不要
2. **Notion / Google Drive 連携** — 外部依存を増やさない方針
3. **AdTruth SDK 連携** — 別 ADR (Phase B) で扱う
4. **複数クライアント対応の自動スケール** — Phase B Week 2 以降
5. **顧客側からの返信解析 / NLP 自動応答** — Phase C 検討
6. **ChatWork チャンネル分離 (本番/ステージング/ログ)** — Business プラン移行後に再検討
7. **Slack ↔ ChatWork ブリッジ** — 重複通知を避けるため当面なし

---

## Migration Path

### M-1. WebSearch ベースの定期棚卸し (層3 知識ベース継続運用)

`templates/chatwork/_action_steps.md.j2` の rule_id 別手順は、Day 3 G タスクで
WebSearch を用いて 2026-05 時点の Meta 公式情報を反映済み。
Meta は仕様変更が頻繁 (例: AEM 8 イベント枠撤廃 = 2025/6) のため、以下のサイクルで継続棚卸しする。

**標準サイクル**:
半年ごと (次回 **2026 年 11 月**) に WebSearch で全 rule_id の手順最新性を再確認、
必要なら ADR-005-rev1 / rev2 として改訂版でテンプレート更新。
Meta / Google / TikTok の仕様変更 (特に Privacy / Cookie 規制系) は四半期単位で発生するため、
運用上は **3 ヶ月ごとの軽量レビュー** も推奨。

**棚卸し時の作業項目**:
1. 5 主要 rule_id × `setup 2026 / 2027 / 仕様変更` で WebSearch
2. ヒットした最新公式情報 (Meta Business Help Center / Developers.facebook.com) を確認
3. `_action_steps.md.j2` の該当マクロを更新 (層1 抽象度を維持しつつ事実関係のみ差替)
4. テスト (`tests/test_chatwork_templates.py`) で Key 文言の存在確認 → 全通過確認
5. `_disclaimer_ai_assist.md.j2` の `current_year/current_month` は globals 自動更新で対応不要
6. ADR-005 の本セクションに「最新棚卸し: YYYY-MM 実施、変更点: …」を追記

### M-2. Phase A → Phase B 移行時の確認項目

- 内部レビュー期間 (最初の 2 週間) 終了後、誤検知率 5% 以下 + 文面の自然さを確認
- pilotton 担当者を ChatWork ルーム rid 435851481 に招待 (kickoff day)
- `.env` の `CHATWORK_TEST_PREFIX=` を空にして本番化
- 過去の `[テスト]` プレフィクス投稿を ChatWork 上で削除

### M-3. 複数クライアント対応 (Phase B Week 2 以降)

- Phase A は pilotton 単一クライアントのみ
- 複数クライアント対応時は `clients.yaml` に `chatwork_room_env` フィールドを追加し、
  `daily_chatwork_check.py --client <id>` を全クライアントでループ実行
- Free プラン枠の問題で Business プラン以上必須

---

## References

- 設計議論: 内部 Slack ログ (2026-05-03 / Day 5.3 直後)
- 関連 ADR:
  - [ADR-001: 想定改善額の3層表示](./ADR-001-three-layer-impact-display.md)
  - [ADR-002: 6 グループ root_cause_group 分類](./ADR-002-six-root-cause-groups.md)
  - [ADR-003: pixel_health 連動](./ADR-003-pixel-health-coupling.md)
  - [ADR-004: CV 正規化と conversion_mapping](./ADR-004-cv-normalization-and-conversion-mapping.md)
- ChatWork API 仕様: <https://developer.chatwork.com/reference/get-rooms-room_id-messages>
- 運用手順: [`docs/operations/chatwork_scheduler_setup.md`](../operations/chatwork_scheduler_setup.md)
- スモークテスト結果: 2026-05-03 17:12 実施 (rid 435851481)
