# ADR-009: 顧客選好トレードオフ — 二層判断フレーム

| 項目 | 値 |
|------|---|
| **Status** | **Accepted** (2026-05-05、Phase A 内部レビュー期間に運用開始) |
| **Decision Date** | 2026-05-05 |
| **Authors** | 山本 / Claude Code |
| **Related ADRs** | ADR-005 (ChatWork ループ), ADR-006 Draft (AdTruth), ADR-011 (Bot 通知), ADR-012 (自動提案), ADR-013 (5 層ルール体系) |
| **Supersedes Draft** | `docs/architecture/tradeoff_design.md` (旧 32 KB Draft、本 ADR で公式化) |

---

## §1. 背景と問題提起

### 既存 11 軸トレードオフの拡張

ADR-002 で確立した 11 軸 (TO-01〜TO-11) は **広告運用上の戦術判断** をカバーしている:
- TO-01 構造の粒度 / TO-02 学習シグナル / TO-04 評価対象 / TO-09 IS Lost / TO-10 時間軸 等

しかし以下の領域は **既存 11 軸の射程外**:
- AdTruth (ADR-006 Draft) で検知した不正トラフィックを「どこまで止めるか」
- ブロック実行で **CV 発生中のセグメント** を巻き込んだ場合の運用判断
- 媒体別 (Meta/Google/TikTok) のブロック手段差を吸収する一貫した意思決定

→ **TO-12「不正ブロック実行 vs CV 保全」** を新設 (本 ADR で正式採用)

### 灰ゾーン判断の構造的課題

不正検知 (fraud_score) と CV 発生率は独立変数のため、4 象限が成立する:

```
                fraud_score 高
                     │
         (1) 黒        │   (2) 灰
         不正確実      │   不正高 + CV 高
         即ブロック    │   ※判断困難な領域
                     │
   ──────────────────┼──────────────────
                     │
         (3) 無        │   (4) 白
         不正低 + CV低│   優良トラフィック
         判断保留      │   保護必須
                     │
                fraud_score 低
              (CV 発生率は縦軸右が高)
```

**(2) 灰ゾーン** = 不正シグナル高 × CV 発生率高 = **最も判断が難しい領域**。
- 完全ブロック → CV を巻き込み顧客満足度低下
- 放置 → 広告費の無駄消化継続
- どちらが最適かは **顧客の方針次第** (CV 重視 or CPA 重視 or バランス)

→ **「顧客の選好」を構造化して反映する仕組みが必要** = 本 ADR の主目的

### AdTruth (ADR-006 Draft) との連動

ADR-006 で AdTruth (LP 配置型不正検知) を Phase B Week 2-3 で実装予定。
AdTruth 本実装時に **「ブロックを実行するかどうか」** の判断機構が必要:
- TO-12 で軸を定義
- 二層判断フレーム (本 ADR §2) で実装
- AdTruth と接続するエンジン群 (recommendation / blocker / threshold / cv_preservation)

---

## §2. 二層判断フレーム

### 第一層: 運用憲章 (Operating Charter)

顧客と **kickoff day で初期合意** する運用方針。`outputs/client_preferences/{client_id}.yaml` に保存。

#### スキーマ

```yaml
client_id: pilotton
operating_charter:
  primary_kpi: cv_max | cpa_min | roas_max | balanced
                                          # 主要 KPI、判断軸の最上位
  learning_phase_tolerance: conservative | aggressive | uniform
                                          # 学習フェーズへの介入度
  cv_loss_tolerance_pct: 5 | 10 | 15 | 20 # ブロック実行時の許容 CV 損失率
  decision_frequency: per_event | weekly_batch | monthly_review
                                          # 灰ゾーン判断の確認頻度
  delegation_scope: black_only | grey_threshold | full_review
                                          # Zynect への委任範囲
                                          # black_only: 黒のみ自動ブロック、灰は都度確認
                                          # grey_threshold: 一定閾値以下は委任
                                          # full_review: 全件人間確認
  charter_version: "1.0"
  established_at: 2026-05-14T10:00:00+09:00
  next_review_at: 2026-08-14                # 四半期更新
```

#### 更新タイミング

- **kickoff day で初版合意** (charter_version 1.0)
- **四半期ごとの定例レビュー** で改訂 (1.1 / 2.0 等)
- 緊急変更要請時は 1 営業日内対応

### 第二層: 都度確認 + Zynect 推奨

灰ゾーン検知時に ChatWork に投稿する判断要請メッセージ。**運用憲章を必ず引用**して推奨を生成。

#### Zynect 推奨ロジック

```python
def generate_recommendation(rule_context, charter, decision_history):
    # 1. 運用憲章の primary_kpi に基づく初期スコア
    base_action = {
        "cv_max":   "monitor_with_close_watch",
        "cpa_min":  "block_aggressive",
        "roas_max": "block_with_cv_preservation",
        "balanced": "investigate",
    }[charter.primary_kpi]
    
    # 2. cv_loss_tolerance_pct と現状 CV を突合
    if estimated_cv_loss_pct > charter.cv_loss_tolerance_pct:
        base_action = downgrade(base_action)  # より保守的に
    
    # 3. 過去判断 5 件の傾向を反映
    similar = find_similar_past_decisions(rule_context, top_n=5)
    if similar.unanimous_action:
        base_action = align_with_past(base_action, similar)
    
    # 4. 学習フェーズへの影響評価
    if ml_status.in_learning_phase:
        base_action = preserve_learning_signal(base_action)
    
    return Recommendation(
        action=base_action,
        rationale=f"運用憲章 primary_kpi={charter.primary_kpi} + 過去判断 5 件中 N 件が同方向 + 学習フェーズ {ml_status.phase}",
        expected_outcomes=calc_outcomes_per_choice(...),
        similar_past_decisions=similar,
        confidence=calc_confidence(...)
    )
```

---

## §3. 一貫性担保 3 仕組み

### 3-1. 判断ログ全件記録

`outputs/client_preferences/{client_id}_decisions.yaml`:

```yaml
client_id: pilotton
decisions:
  - decision_id: D-20260515-001
    timestamp: 2026-05-15T14:30:00+09:00
    rule_id: F-AF-03    # ADR-013 の rule_id
    grey_zone_data:
      fraud_score: 0.85
      cv_rate_pct: 12
      affected_cv_count_30d: 23
      affected_ad_cost_jpy: 150000
      placement: "facebook_feed"
    zynect_recommendation: monitor_with_close_watch
    customer_decision: monitor_with_close_watch  # or block / investigate / custom
    consistency_with_charter: true
    consistency_with_past: true
    notes: "顧客より「あと2週間様子見」、推奨どおり"
```

### 3-2. 月次整合性レポート

`engine/recommendation_engine.check_consistency()` が毎月 1 日に算出:
- **charter_consistency_pct**: 運用憲章 primary_kpi と判断結果の整合率
- **past_consistency_pct**: 過去同種判断と今月判断の整合率
- 整合率が前月比 -10% 超なら能動的に「中間レビュー推奨」を ChatWork 投稿

### 3-3. 四半期憲章レビュー

`templates/chatwork/_quarterly_charter_review.md.j2` で投稿:
- 過去 3 ヶ月の判断件数 + 内訳
- 整合率と KPI 達成状況
- ズレが大きい項目の分析
- 憲章更新案 (A: 維持 / B: KPI 変更 / C: tolerance 変更 / D: カスタム)

---

## §4. 媒体別ブロック仕様 (TO-12 連携)

| 媒体 | 灰ゾーン判定単位 | 主要ブロック手段 | IP 除外 |
|------|------------|------------|--------|
| **Meta** | Pixel + CAPI 経由のユーザシグナル | Custom Audience exclusion (CAPI hash) + Audience Network 除外 + プレースメント除外 | ❌ 非対応 |
| **Google** | クリック ID + IP + キーワード | IP 除外 (最大 500 件) + キーワード除外 + プレースメント除外 + オーディエンス除外 | ✅ 500 件まで |
| **TikTok** | Custom Audience + プレースメント | Custom Audience exclusion + プレースメント除外 | ❌ 非対応 |
| **共通 (LP)** | AdTruth タグから直接判定 | cloaking page 表示 (媒体非依存・最終防衛) | (LP 側) |

→ `engine/adtruth_blocker.py` の `BaseBlocker` 派生クラスで媒体別実装。

---

## §5. 既存 ADR との関係

| ADR | 関係 | 統合方針 |
|-----|------|---------|
| ADR-005 (ChatWork ループ) | 灰ゾーン都度確認の投稿経路 | 既存 `daily_chatwork_check.py` に判断要請ハンドラ追加 |
| ADR-006 Draft (AdTruth) | 不正検知の入力源 | Phase B Week 2-3 で AdTruth → 本 ADR の灰ゾーン判定へ接続 |
| ADR-011 (Bot 通知) | 投稿主体 (Zynect Auto-Reporter Bot) | そのまま流用、追加実装なし |
| ADR-012 (自動提案エンジン) | 都度判断要請の自動生成 | 提案ルール `auto_proposal_rules.yaml` に灰ゾーン用ルール追加可能 |
| ADR-013 (5 層ルール体系) | 灰ゾーン検知ルール (F-AF-* 等) の発火源 | Foundation 層 ad_fraud_screening + Precision 層 audience がトリガー |

---

## §6. 実装ロードマップ

### Phase A (現状 〜 5/13、本 ADR 着手)
- ✅ TO-12 軸を `tradeoff_axes.yaml` に追加
- ✅ 4 エンジン (recommendation / adtruth_blocker / threshold_optimizer / cv_preservation_monitor) の **インターフェース + ロジック骨格** 実装
- ✅ ChatWork テンプレート 5 種作成
- ✅ pilotton 運用憲章ファイル (tbd 値で初期化)

### Phase B Week 2-3 (5/20-5/31)
- AdTruth (ADR-006) MVP 実装と本 ADR の灰ゾーン判定接続
- 媒体 API 実呼び出し (Custom Audience exclusion / Google IP 除外 等)
- 学習データ蓄積 (10-20 件で第一次選好推定)

### Phase C (Phase B 完了後)
- ML ベースの選好推定 (回帰モデル)
- 判断 SLA (応答時間) の自動計測
- 整合率トレンド分析 + 自動提案

---

## §7. 都度学習データ + 顧客選好フォーマット拡張

### 学習進捗による confidence 動的調整

```yaml
# outputs/client_preferences/pilotton.yaml
ml_learning_status:
  decision_count: 7              # これまでの判断件数
  confidence_band_pct: 35        # 7 件 → 35% (50 - 5 × 7、§7.5 の式)
  last_estimation_at: null       # 10 件超で初回推定実行
```

### サンプル数連動の confidence

| 累積判断件数 | confidence_band_pct | 状態 |
|------------|---------------------|------|
| 0 | 50% | 推奨は「保守的」(運用憲章のみ) |
| 4 | 30% | 過去判断の傾向反映開始 |
| 10 | **20%** | **定常状態** |
| 20+ | 15% | ML ベース推定の信頼性向上 |

### 自然言語応答パーサ (Phase B Week 3+)

ChatWork 返信を Claude API で構造化:
1. **キーワードベース** (実装済予定): 「block」「停止」「除外」 → block / 「様子見」「保留」 → monitor
2. **Claude API フォールバック**: キーワード不一致時、`engine/claude_insights.py` の prompt 拡張
3. 構造化結果を `decisions.yaml` に追記

---

## Alternatives Considered

### A-1. 単一判断ルール (運用憲章のみ、都度確認なし)

**却下**: 運用憲章で全ケース尽くせない、想定外の灰ゾーンで顧客信頼を損なう

### A-2. 都度確認のみ (運用憲章なし)

**却下**: 毎回フルレビューは顧客負担過大、判断の一貫性が失われる

### A-3. ML 完全自動化 (人間判断除外)

**却下**: Phase A では学習データ不足、誤判定で広告費損失リスク。Phase C 候補

---

## Result (実装後の確認指標)

| 指標 | 期待値 | 計測方法 |
|------|--------|---------|
| 運用憲章合意率 | 100% (全 client kickoff day で合意) | charter file の established_at |
| 灰ゾーン判断応答時間 (SLA) | 平均 24h 以内 | decisions.yaml の timestamp 差分 |
| 整合率 (charter_consistency_pct) | 70% 以上 (Phase A)、85% 以上 (Phase B) | 月次レポート |
| 顧客 NPS | (Phase B 開始後計測) | 別ヒアリング |

---

## Tradeoffs / Risks

### T-1. 運用憲章の硬直性
四半期更新だと環境変化に追随できない可能性 → 緊急変更フロー (1 営業日対応) で緩和

### T-2. 推奨ロジックの誤判定
Phase A の少数サンプルで信頼性低い → confidence_band 50% から段階的に narrowing

### T-3. ChatWork 返信の解釈ばらつき
自由文返信のパース精度 → Phase B Week 3 で Claude API フォールバック

### T-4. 媒体間の整合
Meta はブロック効くが Google で漏れる等 → adtruth_blocker.py の media-specific 実装で吸収

---

## References

- `docs/architecture/tradeoff_design.md` (旧 Draft、本 ADR で正式化)
- ADR-005: [ChatWork ループ](./ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-006 Draft: AdTruth (Phase B Week 2-3 実装予定)
- ADR-011: [ChatWork Bot 通知](./ADR-011-chatwork-auto-notification-group.md)
- ADR-012: [自動提案エンジン](./ADR-012-auto-proposal-engine.md)
- ADR-013: [5 層ルール体系](./ADR-013-multi-layer-rule-design.md)
- 11 軸 + TO-12: `config/rules/tradeoff_axes.yaml`
- 実装ファイル:
  - `engine/recommendation_engine.py` (Zynect 推奨生成)
  - `engine/adtruth_blocker.py` (媒体別ブロック)
  - `engine/threshold_optimizer.py` (動的閾値最適化)
  - `engine/cv_preservation_monitor.py` (CV 保全監視)
- テンプレート:
  - `templates/chatwork/_grey_zone_decision_meta.md.j2` (Meta 用、中身)
  - `templates/chatwork/_legal_indication_yakkihou.md.j2`
  - `templates/chatwork/_legal_indication_keihyouhou.md.j2`
  - `templates/chatwork/_legal_indication_tokushouhou.md.j2`
  - `templates/chatwork/_quarterly_charter_review.md.j2`
  - `templates/chatwork/monthly_report.md.j2` (拡張)
