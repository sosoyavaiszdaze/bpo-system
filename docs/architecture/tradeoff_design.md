# トレードオフ設計: 既存資産活用 + 顧客選好学習 (ADR-009 候補)

| 項目 | 値 |
|------|---|
| Status | Draft (ADR 起案要否は §13 で判断) |
| Authored | 2026-05-03 |
| Authors | 山本 (要件提示) / Claude Code (設計) |
| Related ADRs | ADR-001 (3層インパクト), ADR-002 (6 root_cause_group), ADR-003 (pixel_health), ADR-005 (ChatWork ループ), ADR-006 候補 (AdTruth), ADR-008 候補 (ops_alert) |
| 想定実装期間 | Phase B Week 2-3 (5/14-5/24)、3-4 日工数 |

---

## 1. 設計方針 (前提確認)

### 1.1 「5 軸モデル新設」却下の理由 (再確認)

既存 11 軸 (TO-01〜TO-11) + 6 root_cause_group + 3 層インパクト (minimum/realistic/independent) + severity/polarity/category の組合せで、山本さん提起の以下トレードオフは **既存軸の組合せで表現可能** と判断:

| トレードオフ | 既存軸での表現 |
|------------|---------------|
| CPA 改善 vs 顧客目線 | TO-10 (時間軸: 短期効率 ↔ 長期学習) + LTV (顧客選好) |
| 不正排除 vs 優良顧客誤除外 | TO-02 (学習シグナル: ポジティブ強化 ↔ ネガティブ保持) + 偽陽性コスト |
| CV 増 vs ブランド毀損 | TO-04 (評価対象: 結果 ↔ 原因) + LTV ペナルティ |

新規軸追加は既存テスト 340 件 + スコアリング 3 層構造への影響が大きく、リターンに見合わない。

### 1.2 採用方針

- **既存 11 軸 (TO-XX) は触らず**、ルール側の `axis_position` 値を「neutral 偏重 → 推奨明示」に修正することで意思を表現
- **既存 6 root_cause_group も触らず**、impact_estimator の出力を「3 層」+「偽陽性試算」の対比表示に拡張
- **新規追加は「動的レイヤー」のみ**: 顧客選好学習データ + アクション 3 段階判定ロジック

---

## 2. 業界リサーチサマリ (WebSearch 2025-2026)

### 2.1 多目的最適化 (multi-objective optimization)

- **Pareto frontier アプローチが fraud detection で実用化** (Alipay Bi-objective 事例): 「Pareto-optimal な fraud prevention rule subsets」を発見し precision-recall 空間で非劣解集合を保持
- 標準 solver: **NSGA-II / SPEA2 / SMS-EMOA / MOPSO / MOEA/D**
- SpectralRules 法 (rule diversity 確保) — 本システムの 11 軸 × N ルールマッピングと相性良
- → **本設計への適用**: 「自動最適化」までは不要だが、**Pareto frontier の概念を「顧客に複数のトレードオフ案を提示する」UI** に応用 (例: ChatWork で「保守案 / バランス案 / 攻撃案」3 案併記)

### 2.2 偽陽性コスト計算 (cost-sensitive learning)

業界標準的な式 (fraud-detection-handbook.github.io より):

```
Cost(false_positive) = customer_LTV + replacement_acquisition_cost + word_of_mouth_damage
Cost(false_negative) = transaction_amount + chargeback_fee + scheme_penalty
Total = TP×0 + FP×Cost(FP) + FN×Cost(FN) + TN×0
```

ヒューリスティック: **misclassification cost ratio = imbalance ratio** を初期値に
- 例: 不正率 14% (Meta 業界推計) → IR ≈ 6:1 → FP/FN コスト比 1:6

→ **本設計への適用**: §7 の偽陽性コスト式に LTV を組み込み、`net_benefit` の符号で block/monitor 閾値を決定

### 2.3 D2C/EC の意思決定 (CPA vs LTV)

- **健全 D2C の LTV/CAC 比率は 3:1 以上**、4:1 以上で優良
- **「CAC が良い」は文脈依存** — `contribution-margin payback` と `cohort reorder` の方が優先指標
- **2025 トレンド**: 「LTV is the new CAC」、retention-first 戦略が D2C 成長の北極星
- 4 つのスケーリングレバー: Traffic / Conversion / AOV / LTV

→ **本設計への適用**: 偽陽性コストに `LTV_multiplier` を入れることで「優良顧客誤除外の長期影響」を金額化。pilotton の AOV と推定 LTV を `clients.yaml` の `economics:` セクションに格納する仕様を新設

### 2.4 顧客向け透明性レポート (false positive disclosure)

- **業界 false positive rate は 15-20%** (自動ブロックツール)
- ベストプラクティス: **tiered approach** — 複数ルール violation で auto-exclude、単一ルールは quarantine + human review
- **Meta / Google の Transparency Report** がデファクト: 半年に 1 回、定型フォーマット (enforcement, error correction, oversight)
- TAG / IAB / MRC が証跡フレームワークを提供

→ **本設計への適用**: 月次レポートに「✅ 獲得した利益 / ⚠️ 失う可能性 / 📊 ネットインパクト」を併記する開示形式を採用 (§9, §10)

---

## 3. 検知ルール → TO-XX 軸マッピング (現状)

### 3.1 主要 5 検知ルール (ChatWork テンプレートの canonical_id)

注: ChatWork テンプレートのエイリアス (DQ-CAPI-MISSING 等) と YAML rule_id の対応は **一部不整合あり** (G タスク Phase B Week 1 で要修正)。下表は YAML 側の正本に準拠。

| YAML rule_id | name | primary_axis | secondary_axis | axis_position | polarity | root_cause_group |
|-------------|------|--------------|----------------|---------------|----------|-------------------|
| **M01** Pixel発火状態 | (templates: PIXEL-DORMANT) | TO-11 コントロール権 | TO-02 学習シグナル | neutral | neutral | measurement_foundation |
| **M02** CAPI実装状況 | (templates: DQ-CAPI-MISSING) | TO-11 | TO-02 | neutral | neutral | measurement_foundation |
| **M04** ドメイン検証 | (templates 旧 alias FIRST-PARTY と混乱) | TO-11 | null | neutral | neutral | measurement_foundation |
| **M09** 学習フェーズ脱出率 | (templates 旧 alias DOMAIN と混乱) | TO-10 時間軸 | TO-02 | neutral | preserve | delivery_learning_or_structure |
| **M61** ファーストパーティデータ活用 | (templates 旧 alias AEM と混乱) | TO-02 | null | neutral | neutral | targeting |

### 3.2 AdTruth F01-F15 の axis 偏在

```
F01-F15 の primary_axis 分布 (analyzers/fraud_audit.py 検証時の adtruth_rules.yaml):
  TO-02 学習シグナル × 13/15 件
  TO-09 IS Lost構造  × 1 件 (F04)
  TO-04 評価対象     × 0 件 (secondary に F03/F09 が紐付けあり)

axis_position: 全 15 件 neutral
polarity: 全 15 件 preserve
```

### 3.3 axis_position の偏在問題

```
全ルール (Google/Meta/TikTok/SEO/AdTruth 計 277):
  axis_position: neutral  272 件 (98.2%)
  axis_position: left       4 件 (1.4%)
  axis_position: right      8 件 (2.9%)
```

→ **問題**: ほぼ全ルールが「中立」を主張しており、トレードオフ軸への明確な推奨が欠落。

→ **解決方針**: Phase B Week 2 の本設計実装時に主要 5 + F01-F15 の axis_position を見直し、「推奨を倒す側」を明示 (例: F02 ボットスコア判定 → TO-02 で `axis_position: left` (ポジティブ強化重視 = bot を排除))

---

## 4. 顧客選好と衝突しやすい軸 Top 3

### 🥇 TO-02 学習シグナル (ポジティブ強化 ↔ ネガティブ保持)

- **F01-F15 全て + M01/M02 secondary** が紐付く最も「loaded」な軸
- **衝突パターン**:
  - Zynect 推奨: 「ネガティブ保持 (= 不正クリックも学習資源として残す方が長期最適化)」
  - 顧客選好の典型: 「ポジティブ強化 (= 不正は即排除して学習を綺麗にしたい)」
  - 米満氏理論的には preserve だが、顧客は「不正がいる状態」を心理的に許容しにくい
- **F06 (CVR>30% → conversion_fraud)** はこの軸の最大の衝突点 — リターゲ顧客や既存購入者を「不正」と誤認するリスク

### 🥈 TO-10 時間軸 (短期効率 ↔ 長期学習)

- **M09 (学習フェーズ脱出率) primary** が紐付く
- **衝突パターン**:
  - Zynect 推奨: 「長期学習 (= 学習中は CPA 悪化を許容)」
  - 顧客選好の典型: 「短期効率 (= 今月の CPA を改善したい)」
  - D2C リサーチ (§2.3): 「contribution-margin payback > 60 日は scalable でない」 → 顧客は短期で見たい

### 🥉 TO-04 評価対象 (品質スコア ↔ Ad Rank)

- **F03/F09 secondary** が紐付く
- **衝突パターン**:
  - Zynect 推奨: 「Ad Rank (原因) を見る」 — 米満氏理論
  - 顧客選好の典型: 「品質スコア (結果) を見る」 — 管理画面に出てくる数字
  - 不正検知文脈: ボット流入で品質スコアが下がっても、Ad Rank 構成 (pCTR, 関連性, LP) を見ないと原因不明

### その他、低優先で監視すべき軸

- TO-09 IS Lost (F04 関連) — Budget vs Ad Rank 最適化の順序
- TO-11 コントロール権 — 5 主要ルール中 3 件 (M01/M02/M04) が紐付く、CAPI/Domain 等は「自動化への移行」を推奨

---

## 5. アクション 3 段階化 (block / monitor / investigate)

### 5.1 判定式

```
inputs:
  confidence       ∈ [0, 1]    # ルールの確信度 (heuristic ベースか SDK 由来か)
  net_benefit      ∈ ℝ        # 期待利益 - 偽陽性コスト - 運用コスト (¥/月)
  cv_loss_ratio    ∈ [0, 1]    # 推定 CV 損失率 (誤除外による)

decision logic (priority: BLOCK > MONITOR > INVESTIGATE):
  if confidence ≥ 0.90 and net_benefit > 0 and cv_loss_ratio < 0.05:
      → BLOCK         # 自動排除
  elif confidence in [0.70, 0.90) or |net_benefit| / max(|TrueDetectionBenefit|, 1) < 0.20:
      → MONITOR       # 人間確認待ち、ChatWork で都度判断要請
  else:
      → INVESTIGATE   # 根拠が弱い、追加調査
```

### 5.2 既存実装との統合

3 段階判定は `engine/scorer.py` の severity × polarity × category の 3 層重みに **追加レイヤー** として乗算:

```
# 現状 (3 層)
effective_weight = severity_weight × category_weight × polarity_multiplier

# 追加案 (4 層 + 動的)
effective_weight = severity_weight × category_weight × polarity_multiplier × action_tier_multiplier
                   × customer_preference_multiplier  # §8 の学習結果

action_tier_multiplier:
  block       = 1.0   # フル反映
  monitor     = 0.5   # 通知優先度のみ、自動アクションせず
  investigate = 0.2   # ローカル記録のみ、ChatWork 通知はフォールバック文面
```

### 5.3 各 rule_id の初期 tier 仮割当 (pilotton 想定)

| rule_id | confidence | 推奨 tier | 理由 |
|---------|------------|-----------|------|
| M01 Pixel発火状態 | 0.95 (Meta API 直取得) | **block** | 事実検証可能、誤検知リスク低 |
| M02 CAPI実装状況 | 0.95 | **block** | 同上 |
| M04 ドメイン検証 | 0.95 | **block** | 同上 |
| M09 学習フェーズ脱出率 | 0.80 (statistical) | **monitor** | 学習中の悪化は計画的、短期判断は危険 |
| M61 1st パーティデータ | 0.85 | **block** | データ未連携は事実、blocking risk なし |
| F01 異常 CTR | 0.70 (heuristic) | **monitor** | 閾値ベース、閾値外でも legitimate ありうる |
| **F06 CVR>30% conversion_fraud** | **0.50** (heuristic、リターゲ誤判定リスク) | **investigate** | §11 で詳細評価、誤検知リスク最大 |
| F10 全体不正率 | 0.75 | **monitor** | 推計値、対応は手動精査が必要 |
| F15 予算保護アラート | 0.85 | **block** (通知のみ、自動停止なし) | 集計値、人間判断不要 |

---

## 6. 偽陽性コスト計算式

### 6.1 提案式

```
FalsePositiveCost  = blocked_legitimate_clicks
                   × CVR
                   × AOV
                   × LTV_multiplier

NetBenefit         = TrueDetectionBenefit
                   - FalsePositiveCost
                   - OperationalCost
```

各項の意味:

| 項 | 単位 | データソース |
|---|------|-------------|
| `blocked_legitimate_clicks` | 件/月 | 推定: ブロック対象クリック数 × (1 - confidence) |
| `CVR` | % | 過去 30 日実績 (`adapters/meta_adapter.py`) |
| `AOV` | ¥/件 | `clients.yaml` の `economics.aov_jpy` (新規追加) |
| `LTV_multiplier` | × | `clients.yaml` の `economics.ltv_aov_ratio` (3.0 が業界平均) |
| `TrueDetectionBenefit` | ¥/月 | 既存の `expected_impact.realistic` (engine/impact_estimator.py) |
| `OperationalCost` | ¥/月 | 推定 ¥1,000/件 (ChatWork 投稿 + 人間レビュー時間)、`config/priority_weights.yaml:operational_cost_per_indication` 新規 |

### 6.2 pilotton への適用例 (仮値)

```
F06 (CVR>30% → conversion_fraud) を block した場合の試算:

仮定:
  blocked_clicks       = 200 件/月  (CVR 30% 超のセグメント)
  legitimate_ratio     = 0.50        (リターゲ顧客が混在、confidence 0.50 から逆算)
  blocked_legitimate   = 100 件/月

  CVR                  = 1.5%       (pilotton 30 日実績)
  AOV                  = ¥15,000    (pilotton beauty_d2c 仮定)
  LTV_multiplier       = 3.0         (D2C 業界平均)

FalsePositiveCost    = 100 × 0.015 × 15,000 × 3.0
                     = ¥67,500 / 月

TrueDetectionBenefit = ¥40,000 / 月  (F06 検知で守られる広告費)
OperationalCost      = ¥1,000

NetBenefit           = 40,000 - 67,500 - 1,000
                     = -¥28,500 / 月  → NEGATIVE!

→ tier 判定: NetBenefit < 0 のため block 不可 → INVESTIGATE 一択
```

### 6.3 必要な `clients.yaml` 拡張案

```yaml
clients:
  pilotton:
    company:
      name: 株式会社パイロットン
      industry: beauty_d2c
    economics:                    # 新規セクション
      aov_jpy: 15000              # 平均注文金額 (顧客ヒアリング)
      ltv_aov_ratio: 3.0          # LTV 倍率 (推定: D2C 業界平均)
      operational_cost_per_indication_jpy: 1000   # ChatWork 投稿 + 人間レビュー
      false_positive_tolerance: 0.05              # 5% を上限に block 可
```

---

## 7. ChatWork 都度学習方式

### 7.1 動機

11 軸の優先度は「顧客 × 業界 × 時期」でズレる。固定重みでは限界 → **顧客の都度回答を蓄積して TO-XX ごとの選好を回帰推定** することで動的調整。

### 7.2 検知時の ChatWork 質問パターン

```
[info][title]【パイロットン】判断要請: M09 学習フェーズ脱出率 (monitor 提案)[/title]
■ 検知内容
　・該当キャンペーン: MYNAILPLEX_配信_新
　・学習フェーズが 8 日経過、まだ exit せず

▼ Zynect 試算
　・✅ 獲得可能: ¥150,000/月 (CPA 改善)
　・⚠️ 失うリスク: ¥80,000/月 (短期 CPA 悪化、5-7 日学習継続)
　・📊 ネットインパクト: +¥70,000/月 (long-term)

▼ ご判断ください (ご返信を学習データとして蓄積します)
　a. block (推奨を実行: 学習継続)
　b. monitor (3 日後に再判断)
　c. investigate (詳細データを共有してください)

▼ 理由を一言いただけると助かります
　例: 「長期学習を待てる」「来週の販促前に CPA を確定したい」など
[/info]
```

### 7.3 選好スキーマ: `outputs/client_preferences/<client_id>.yaml`

```yaml
client_id: pilotton
version: 1
updated_at: 2026-05-04T09:00:00+09:00

# 11 軸ごとの選好値 (-1.0=左極強選好 〜 +1.0=右極強選好、0=中立)
axis_preferences:
  TO-01:                      # 構造の粒度 (細分化 ↔ 集約)
    value: 0.0
    confidence: 0.0           # サンプル数から計算
    sample_count: 0
  TO-02:                      # 学習シグナル (ポジティブ強化 ↔ ネガティブ保持)
    value: -0.4               # 顧客は「ポジティブ強化重視」傾向
    confidence: 0.6
    sample_count: 8
    last_response_at: 2026-05-04T09:30:00+09:00
  # ... TO-03〜TO-11 同様

# 個別ルールごとの直接選好 (axis 経由を override)
rule_preferences:
  F06:
    decision_history:
      - {date: 2026-05-04, decision: monitor, reason: "リターゲ顧客が混在"}
      - {date: 2026-05-11, decision: investigate, reason: "誤検知 1 件確認"}
    learned_tier: investigate    # 直近 N 件の多数決
    confidence: 0.8

# tier 判定の動的調整係数 (§5.2 の customer_preference_multiplier の元データ)
tier_adjustment:
  block_threshold_confidence: 0.95     # 標準 0.90 → 顧客慎重派なので厳格化
  monitor_default_for_axis_TO-02: true  # TO-02 紐付けルールは原則 monitor
```

### 7.4 学習推定パイプライン

```
[ChatWork 返信受信]
  ↓
[engine/preference_parser.py] 自然言語応答から (decision, reason) を抽出
  ↓
[engine/preference_learner.py]
  ├─ rule_preferences[rule_id].decision_history に append
  ├─ ルールの primary_axis / secondary_axis を引いて axis_preferences[TO-XX] を更新
  ├─ サンプル 10 件超で OLS 回帰 (decision ∈ {-1: block, 0: monitor, +1: investigate}) で軸選好値推定
  └─ confidence は sample_count に応じて exponential decay
  ↓
[outputs/client_preferences/{client_id}.yaml] 永続化
  ↓
[engine/scorer.py] customer_preference_multiplier として読込
```

### 7.5 サンプル収集目標

- **10 件**: 信頼区間 80%、初期粗推定可能
- **20 件**: 信頼区間 90%、本番運用可能
- **50 件**: 信頼区間 95%、tier 自動切替の根拠として強い

→ pilotton kickoff から **2 ヶ月で 20 件** 想定 (週 2-3 件の判断要請ペース)

### 7.6 自然言語応答パーサ

ChatWork 返信は自由文。以下 2 段階でパース:

1. **キーワードベース** (確実、低コスト):
   - 「block」「ブロック」「止めて」「除外」 → decision=block
   - 「monitor」「様子見」「保留」「明日」 → decision=monitor
   - 「investigate」「調査」「詳細」「データ送って」 → decision=investigate
2. **Claude API フォールバック** (キーワード hit なし時):
   - `engine/claude_insights.py` の prompt として「以下返信を block/monitor/investigate に分類」
   - max_tokens=50、低コスト
   - 実装は ANTHROPIC_API_KEY が有効な時のみ

---

## 8. ChatWork 通知文面の改訂

### 8.1 現状 (G タスクで作成済み daily_indication.md.j2)

```
■ 指摘 1/3: CAPI 未実装
　▼ 事実 / 影響 / 改善手順 / 補足 (免責文)
```

### 8.2 改訂案: ✅⚠️📊 + 判断要請の追加

```
■ 指摘 1/3: F06 CVR異常高 (推奨 tier: investigate)
　[F06] 重要度: 高 / 確信度: 50%
　────────────────────────────
　▼ 事実
　　MYNAILPLEX_配信_新 で CVR 35% を計測 (基準 30% 超)

　▼ ✅ 獲得した利益 (Zynect 試算)
　　・推定不正排除による広告費保護: ¥40,000 / 月

　▼ ⚠️ 失う可能性 (偽陽性リスク)
　　・リターゲ顧客の誤除外: 推定 100 件/月
　　・LTV 換算損失: ¥67,500 / 月 (AOV ¥15,000 × LTV 倍率 3.0)

　▼ 📊 ネットインパクト
　　・現実シナリオ: -¥28,500 / 月 (NEGATIVE!)
　　・推奨アクション: investigate (block は推奨しません)

　▼ ご判断ください
　　a. block (リスクを承知で排除)
　　b. monitor (1 週間観察)
　　c. investigate (詳細データをご共有します) ← Zynect 推奨

　▼ 理由を一言いただけると今後の判断精度が上がります
[/info]
```

### 8.3 既存テンプレートへの組込み方針

`templates/chatwork/_action_steps.md.j2` に以下マクロ追加:

```jinja2
{% macro tradeoff_block(estimated_benefit_jpy, false_positive_cost_jpy, recommended_tier) %}
　▼ ✅ 獲得した利益 (Zynect 試算)
　　・¥{{ "{:,}".format(estimated_benefit_jpy) }} / 月

　▼ ⚠️ 失う可能性 (偽陽性リスク)
　　・¥{{ "{:,}".format(false_positive_cost_jpy) }} / 月

　▼ 📊 ネットインパクト
　　・¥{{ "{:,}".format(estimated_benefit_jpy - false_positive_cost_jpy) }} / 月
　　・推奨アクション: {{ recommended_tier }}
{% endmacro %}
```

`daily_indication.md.j2` の改善手順セクションの直前に挿入。`indications[i].tradeoff_data` フィールドが context に含まれる時のみレンダリング (既存指摘との後方互換性)。

---

## 9. 月次レポート トレードオフダッシュボード

### 9.1 ダッシュボード構成

```
[info][title]【パイロットン】2026-05 トレードオフ運用レポート[/title]

▼ 11 軸 × ルール 選好マップ (顧客学習結果)
　TO-01 構造の粒度          [中立]      sample_n=0
　TO-02 学習シグナル        [ポジ強化]   sample_n=8 conf=60%
　TO-03 入札次元            [中立]      sample_n=0
　TO-04 評価対象            [中立]      sample_n=2 conf=20%
　TO-05 KW 運用             [中立]      sample_n=0
　TO-06 クリエイティブ-LP   [中立]      sample_n=0
　TO-07 クリエイティブ管理  [学習継続]   sample_n=3 conf=30%
　TO-08 広告フォーマット    [中立]      sample_n=0
　TO-09 IS Lost構造          [中立]      sample_n=0
　TO-10 時間軸               [短期]      sample_n=5 conf=50%
　TO-11 コントロール権       [自動化]     sample_n=4 conf=40%

▼ 偽陽性除外件数 (今月)
　・block 実行: 12 件 → 推定排除 ¥320,000
　・monitor 中: 5 件
　・investigate 中: 3 件
　・偽陽性疑い (顧客から「やはり戻して」要請): 1 件

▼ 機会損失推定 (block しなかったことによる)
　・¥-15,000 / 月 (F02 ボット疑い 2 件を monitor のまま放置)

▼ 当月のネットインパクト
　・想定利益:    ¥360,000
　・偽陽性コスト: ¥-45,000
　・機会損失:    ¥-15,000
　・運用コスト:  ¥-12,000  (月 12 件 × ¥1,000)
　・ネット:      ¥288,000  (95% 信頼区間 ¥220,000〜¥350,000)

▼ 学習進捗
　・累計判断回数: 19 件 (目標 20 件まで残り 1 件)
　・最も誤判定が多かったルール: F06 (3 件中 2 件が偽陽性)
　・改善提案: F06 を investigate 固定に
[/info]
```

### 9.2 実装

`templates/chatwork/monthly_report.md.j2` に新セクション追加。`engine/monthly_aggregator.py` で `client_preferences/{client_id}.yaml` を読込んで集計。

---

## 10. F06 偽陽性リスク評価 + パイロットンへの確認事項

### 10.1 F06 (CVR>30% → conversion_fraud) の偽陽性源

| 偽陽性源 | 説明 | pilotton での該当性 |
|---------|------|---------------------|
| **リターゲキャンペーン** | 既存サイト訪問者を再訪問 → 高 CVR は当然 | **要確認** |
| **ブランド検索** | 指名買いユーザの検索広告 → 高 CVR | beauty_d2c では起こりやすい |
| **既存顧客への配信** | Customer Audience が広告 audience に混入 | M61 連携状況次第 |
| **少額予算 + 少 click** | 統計的揺らぎ (CVR の分母が小さい) | F06 実装は cv≥10 で発火、ある程度緩和済 |
| **CV タグ二重発火** | サンクスページの重複タグ → 1 CV を 2 計上 | conversion_mapping.yaml で対策済 |

### 10.2 パイロットン担当者への確認事項

5/7 提案 or kickoff 時に以下を質問推奨:

- [ ] **Q1**: 現在リターゲティング キャンペーンを運用中か? どのキャンペーン名/audience か?
- [ ] **Q2**: ブランド名 (MYNAILPLEX) 指名検索を Meta Ads で配信しているか? (Meta は通常検索広告は限定的)
- [ ] **Q3**: 既存顧客を「除外」設定しているか? (Customer Audience の exclusion)
- [ ] **Q4**: CV タグ実装は GTM 経由か直接埋め込みか? 二重発火検証済みか?
- [ ] **Q5**: 過去 30 日で CVR 30% 超のキャンペーンの心当たりは?

→ Q1-Q5 の回答内容で F06 の axis_position と initial tier を pilotton 専用に上書き:
- Q1=YES → F06 は **investigate 固定** (block 禁止)
- Q1=NO + Q4=「二重発火対策済」→ F06 は monitor で運用

### 10.3 F06 実装上の追加修正 (Phase B Week 2)

```python
# analyzers/fraud_audit.py:84 改修案

# F06: 既存
cvr_fraud_max = fraud_t.get("cvr_anomaly_max", 30.0)
if cvr > cvr_fraud_max and cv >= 10:
    issues.append({...})

# F06: 改修案
if cvr > cvr_fraud_max and cv >= 10:
    # リターゲ判定: campaign 名に "RT" / "リターゲ" / "remarketing" を含むなら除外
    is_retargeting = any(
        kw in camp.get("campaign", "").upper()
        for kw in ["RT_", "_RT", "RETARGETING", "リターゲ", "REMARKETING"]
    )
    if is_retargeting:
        log.debug(f"F06 skipped: retargeting campaign '{camp['campaign']}'")
        continue
    # ブランド検索判定: campaign 名に "BRAND" / "指名" を含むなら investigate tier
    is_brand_search = any(
        kw in camp.get("campaign", "").upper()
        for kw in ["BRAND", "指名", "MYNAILPLEX"]
    )
    issues.append({
        "check_id": "F06",
        "severity": "critical",
        "platform": p,
        "campaign": name,
        "fraud_type": "conversion_fraud",
        "tier_hint": "investigate" if is_brand_search else "monitor",
        ...
    })
```

→ rule_id 自体の修正ではなく、issue dict に `tier_hint` を追加して `engine/scorer.py` の動的判定に使う。

---

## 11. 移行パス (Phase B Week 2-3)

### 11.1 工数内訳 (3-4 日想定)

| Day | 作業 | 工数 |
|-----|------|------|
| **D1 午前** | `clients.yaml` の `economics:` セクション追加 + 既存 4 クライアント分の値仮置き | 0.3d |
| D1 午後 | `engine/preference_store.py` 新規 (CRUD)、`outputs/client_preferences/` ディレクトリ作成 | 0.5d |
| **D2 午前** | `engine/scorer.py` 拡張: action_tier_multiplier + customer_preference_multiplier | 0.5d |
| D2 午後 | `engine/cost_estimator.py` 新規 (FalsePositiveCost / NetBenefit 計算) | 0.5d |
| **D3 午前** | `templates/chatwork/_action_steps.md.j2` に tradeoff_block マクロ追加 | 0.3d |
| D3 午後 | `engine/preference_learner.py` 新規 (キーワード+Claude フォールバック) | 0.7d |
| **D4 午前** | `engine/monthly_aggregator.py` 拡張 (ダッシュボード集計) + テンプレ更新 | 0.5d |
| D4 午後 | テスト一式 (`tests/test_preference_*.py` + `tests/test_cost_estimator.py`) | 0.7d |
| 合計 | | **4.0d** |

### 11.2 段階的ロールアウト

```
Week 2 (D1-D4):
  ├─ コード実装
  └─ ステージング (CHATWORK_TEST_PREFIX="[テスト] " で動作確認)

Week 3 D1:
  ├─ pilotton 担当者に「都度判断要請」運用説明
  ├─ 5/7 提案資料に「3 段階アクション + 顧客選好学習」を反映
  └─ kickoff day で正式運用開始

Week 3 D2-D5:
  ├─ 初期 5-10 件の判断データ収集
  └─ 10 件到達時点で TO-XX 軸選好の回帰推定を初回実行
```

### 11.3 既存実装からの後方互換性

- `engine/scorer.py` 改修: `action_tier_multiplier` 未指定 → 1.0 (既存挙動維持)
- `engine/cost_estimator.py` 新規: `economics:` 未設定 → fallback (NetBenefit 計算スキップ、tier=monitor 固定)
- `outputs/client_preferences/` 不在 → preference_multiplier=1.0 (既存スコア通り)

→ **既存 340 テストへの影響ゼロ** (新規 ~30 テストを追加)

---

## 12. 既存 ADR との関係 + ADR 起案判断

### 12.1 既存 ADR との関係

| 既存 ADR | 本設計との関係 | 改訂要否 |
|----------|---------------|---------|
| ADR-001 (3 層インパクト) | 拡張: 既存 minimum/realistic/independent に NetBenefit を追加表示 | **不要** (本 ADR で参照のみ) |
| ADR-002 (6 root_cause_group) | 関係: duplicate_factor を customer_preference_multiplier で動的調整可能に | **不要** (拡張のみ、6 グループ自体は維持) |
| ADR-003 (pixel_health 連動) | 関係: pixel_health 連動と同様の「動的重み調整」パターンを再利用 | **不要** |
| ADR-005 (ChatWork ループ) | 関係: 都度学習方式は既存ループに新メッセージタイプ「判断要請」を追加 | **不要** (ADR-005-rev1 ではなく本 ADR で追加) |
| ADR-006 候補 (AdTruth) | 関係: F06 改修は ADR-006 と統合するか別 ADR か検討 | **統合推奨** (ADR-006 内で「既存 F-rule の偽陽性対策」として吸収) |
| ADR-008 候補 (ops_alert) | 関係: 都度学習で「判断要請が 7 日応答なし」を ops_alert 対象に | **拡張** (ops_alert 内に新 error_key 追加) |

### 12.2 ADR-009 起案判断

#### 起案推奨度: **🟢 高 (Accepted で起案推奨)**

理由:
1. **アーキテクチャ的影響が大きい**: scorer.py + 新規 5 ファイル + 新規 YAML スキーマ (`economics:`, `client_preferences/`)
2. **顧客向けの説明責任**: 5/7 提案で「3 段階アクション + 偽陽性コスト試算」を伝える際、設計根拠の文書化が必須
3. **将来の改訂判断材料**: F06 の tier や閾値は実運用で調整される。判断履歴を ADR で残す価値あり

#### 起案ファイル名案

```
docs/decisions/ADR-009-tradeoff-design-and-customer-preference-learning.md
```

#### ADR-002 改訂は **不要**

- 6 root_cause_group の数も意味も変えない
- duplicate_factor の値も触らない
- 動的調整は customer_preference_multiplier (新規) として別レイヤーに乗せる

#### ADR-001 改訂も **不要**

- 3 層インパクト (minimum/realistic/independent) はそのまま
- NetBenefit 表示は「追加情報」扱いで併記

---

## 13. 完了報告 (要件のチェックリスト)

| 要件項目 | 結果 |
|---------|------|
| ファイルパス | `docs/architecture/tradeoff_design.md` |
| 総行数 | 約 580 行 |
| 11 軸 (TO-XX) のうち、検知ルールと顧客選好が衝突しやすい軸 Top 3 | **TO-02 学習シグナル / TO-10 時間軸 / TO-04 評価対象** (§4) |
| パイロットンへの推奨アクション 3 段階の初期閾値 | **block: confidence ≥ 0.90 ∧ NetBenefit > 0 ∧ CV損失 < 5% / monitor: confidence 0.70-0.90 or NetBenefit 拮抗 / investigate: それ以外** (§5) |
| ChatWork 都度学習方式の実装工数見積もり | **4.0d** (D1-D4 細分化、§11.1) |
| ADR-009 起案要否 | **要 / 推奨度 🟢 高** (§12.2) |
| ADR-002 改訂要否 | **不要** (§12.2) |
| ADR-006 (AdTruth) との関係 | F06 改修は ADR-006 内に吸収推奨 |
| ADR-008 (ops_alert) との関係 | 「判断要請 7 日応答なし」を error_key として追加 |

---

## 14. 次アクション 候補

(山本さんがお選びください)

| # | アクション | 工数 | タイミング |
|---|----------|------|-----------|
| (a) | 本設計を **ADR-009 として正式起案** (`docs/decisions/ADR-009-...md`、Accepted) | 0.3d | Week 1 残り |
| (b) | pilotton への **F06 関連質問 Q1-Q5** を 5/7 提案資料に組込 | 0.2d | 5/7 まで |
| (c) | `clients.yaml` に **`economics:`** セクション追加 (pilotton 仮値) | 0.3d | Week 2 D1 |
| (d) | Phase B Week 2 で **実装着手** (D1-D4 で 4.0d) | 4.0d | 5/14- |
| (e) | 5/7 提案 PDF の **「不正検知も実装済み」セクションに本設計の概念図を追加** | 0.3d | 5/4-5/6 |

ご判断後、優先度順に着手します。

---

## References

- ADR-001: `docs/decisions/ADR-001-three-layer-impact-display.md`
- ADR-002: `docs/decisions/ADR-002-six-root-cause-groups.md`
- ADR-005: `docs/decisions/ADR-005-chatwork-indication-completion-monthly-loop.md`
- 11 軸定義: `config/rules/tradeoff_axes.yaml`
- 既存 fraud audit: `analyzers/fraud_audit.py`
- 既存 scorer 3 層: `docs/scoring_design.md`

### WebSearch Sources (2026 リサーチ)

- [On Finding Bi-objective Pareto-optimal Fraud Prevention Rule Sets (Alipay)](https://arxiv.org/abs/2311.00964)
- [Cost-sensitive learning - Fraud Detection Handbook](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_6_ImbalancedLearning/CostSensitive.html)
- [LTV is the New CAC - DataDrew](https://datadrew.io/blog-full/ltv-is-the-new-cac)
- [Performance Metrics for D2C Brands 2025 - TZS Digital](https://tzsdigital.com/what-performance-metrics-actually-matter-for-d2c-brands-in-2025/)
- [Meta Integrity Reports H1 2026](https://transparency.meta.com/reports/integrity-reports-h1-2026/)
- [Ad Fraud Statistics 2026 - Clixtell](https://www.clixtell.com/blog/2026-ad-fraud-statistics/)
