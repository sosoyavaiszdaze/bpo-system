# レポート v3 — コンテンツ生成戦略（3層ハイブリッド構成）

> **対象**: Zynect 広告監査レポート v3.0 の各セクションの文章・数値生成方式
> **作成日**: 2026-05-01
> **目的**: 「どの情報をどう作るか」を3層に分けて責務分担し、再現性・コスト・品質のバランスを取る
> **読者**: 実装担当 / アーキテクト / Claude API 利用設計者

---

## 全体方針

レポートのコンテンツは **3層構成** で生成する:

```
┌────────────────────────────────────────────────────┐
│  層3: Claude API（定性推論）                        │
│  ・顧客語への翻訳                                    │
│  ・ナラティブ生成（なぜ・どう・どの順で）            │
│  ・優先順位の説明                                    │
├────────────────────────────────────────────────────┤
│  層2: 計算ロジック（数値演算）                       │
│  ・現状値と推奨値の差分計算                          │
│  ・想定削減額・改善%の試算                           │
│  ・priority_score スコアリング                       │
├────────────────────────────────────────────────────┤
│  層1: 固定値（rule定義由来）                         │
│  ・各ルールの基本期待効果（expected_impact）         │
│  ・原則タグ・ルールID                                │
│  ・severity / category                               │
└────────────────────────────────────────────────────┘
```

**設計の核**: 層1・層2 で再現性・監査可能性を確保し、層3 は **「人間にしかできない翻訳と推論」のみ** に限定する。これによりコストを抑えつつ、数値の信頼性も担保する。

---

## 層1: 固定値（rule定義由来）

### 責務
ルール定義（`config/rules/*.yaml`）に **静的に書かれた値** をそのまま使う。実行時の演算なし。

### 取り扱い項目
| 項目 | 出力先 | 例 |
|------|--------|-----|
| 原則タグ（principle_id） | アクション内 / Insight内 | "P3" |
| ルールID | アクション内 / 媒体別詳細 | "G26" |
| severity | 優先度算定の入力 | "high" |
| category | 媒体別詳細のグループ化 | "計測_トラッキング" |
| **expected_impact** （v3 新設） | 優先アクション Top5 の想定効果ベースライン | `{ cpa_change: -10, confidence: medium }` |
| redesign_note | Zynect Insights のソース文 | "計測精度向上 = 学習シグナル精度向上" |
| yonemitsu_alignment | Zynect Insights の独自視点フラグ | "概念的に問題" / "要再定義" |

### v3 の最重要追加: `expected_impact` フィールドの設計

各ルールに以下の構造で `expected_impact` を追加する。

```yaml
- id: G26
  name: 品質スコア低キーワード
  ...
  expected_impact:
    primary_metric: cpa_change_pct  # 主要改善指標
    primary_value: -10              # %で-10（10%改善）
    secondary_metric: ctr_change_pct
    secondary_value: 5
    confidence: medium              # high / medium / low
    impact_horizon_weeks: 4         # 効果が出るまでの週数
    requires_data: ["historical_cpa", "campaign_volume"]
```

#### `confidence` の判定基準

- **high**: 学術的に効果が確認されている、または社内で20件以上の実例がある
- **medium**: 業界一般のベンチマークがある（例: 「フリークエンシー対応で CPA 5-15%改善」）
- **low**: 仮説段階・媒体仕様変更直後で確証が低い

#### `confidence` をレポートに表示する理由

「想定効果」を出すことは営業上必要だが、**過度に楽観的な数値は信頼を失う**。信頼度ラベルを併記することで、顧客が施策ごとに「自社で検証してから判断する」「即実行する」を選び分けられる。

### 適用対象ルール数（試算）

- Google: 108ルール → 主要40ルール（quick_win=true 等）に `expected_impact` を付与
- Meta: 65ルール → 主要30ルールに付与
- TikTok: 46ルール → 主要20ルールに付与
- 合計: 約90ルール

未付与のルールは「効果未試算」表示とし、後方互換を保つ。

---

## 層2: 計算ロジック（数値演算）

### 責務
取得データ（CSV / API）から **再現性のある数値演算** で改善効果を試算する。Claude API は使わない。

### 主要な計算項目

#### 計算1: 月次削減額試算

```python
# 例: TikTok_Broad の赤字運用解消
current_cost = 72000        # 月次広告費
current_roas = 0.90
current_revenue = current_cost * current_roas  # 64,800
target_roas = 1.5           # 赤字脱出ライン（Zynect推奨は2.0）

# Zynect 想定: 予算50%縮小 + LP改善で ROAS を 1.5 に押し上げ
new_cost = current_cost * 0.5                  # 36,000
new_revenue = new_cost * target_roas           # 54,000
loss_reduction = (current_revenue - current_cost) - (new_revenue - new_cost)
# 旧: -7,200（赤字）/ 新: 18,000（黒字）→ 25,200円改善
```

#### 計算2: CPA改善見込み

```python
# 例: フリークエンシー上限超過の対応
current_cpa = 6048
expected_cpa_change_pct = -10  # expected_impact から取得
new_cpa = current_cpa * (1 + expected_cpa_change_pct / 100)  # 5,443
monthly_cv = total_cost / current_cpa
monthly_savings = (current_cpa - new_cpa) * monthly_cv
```

#### 計算3: 業界平均との差分

```python
industry_benchmark = load_benchmark(industry=client.industry, metric="health_score")
# industry_benchmark = 65 （SaaS 業界の中央値）
gap = industry_benchmark - current_score  # 65 - 50 = 15点
```

#### 計算4: priority_score（Top5 ランキング用）

```python
priority_score = (
    severity_weights[severity] *
    estimated_impact_yen *
    confidence_weights[confidence]
) / max(effort_hours, 1)

severity_weights = {"critical": 5.0, "high": 3.0, "medium": 1.5, "low": 0.5}
confidence_weights = {"high": 1.0, "medium": 0.7, "low": 0.4}
```

### 業界平均ベンチマークの管理

新規ファイル: `config/benchmarks.yaml`

```yaml
industries:
  saas:
    health_score_median: 65
    health_score_p75: 80   # Zynect 推奨水準
    avg_cpa: 4800
    avg_roas: 2.5
    avg_ctr_search: 3.0
    avg_frequency: 2.5
  ecommerce:
    health_score_median: 60
    health_score_p75: 78
    avg_cpa: 2800
    avg_roas: 3.5
    ...
  local_service:
    ...
```

#### ベンチマークソース
- Google: WordStream / Search Engine Land の業界平均レポート
- Meta: AdEspresso / Databox / Wordstream の Meta benchmarks
- TikTok: TikTok Business 公式レポート
- 出典・更新日を YAML に併記し、四半期ごとに更新する運用を確立

### 計算ロジックの実装場所（提案）

```
analyzers/
  benchmark_compare.py    # 業界平均との差分計算
  impact_estimator.py     # 削減額・改善%の試算
  priority_ranker.py      # priority_score の算出と Top5 抽出
```

---

## 層3: Claude API（定性推論）

### 責務
**人間にしかできない翻訳と推論** に限定する。数値生成は層2 に任せる。

### 担当する3つのタスク

#### タスク1: 顧客語への翻訳
- 専門用語の言い換え
- 「フリークエンシー」→「同一ユーザーへの広告表示回数」のような翻訳

#### タスク2: ナラティブ生成（なぜ・どう・どの順で）
- アクションの「実行手順」を3〜5ステップで具体化
- 「なぜ重要か」を10原則の文脈で記述
- エグゼクティブサマリの3行要約

#### タスク3: Zynect Insights ページの独自視点コメント
- 米満氏理論との乖離分析
- 「他社監査ではこう言われがち / Zynect はこう判断する」の対比文
- 媒体特有の独自指摘

### Claude API 呼び出し設計

#### モデル選定
- **デフォルト**: `claude-sonnet-4-6`（コスト/品質バランス）
- **Zynect Insights のみ**: `claude-opus-4-7`（独自視点の質を最優先）
- 設定箇所: `config/clients.yaml` の `defaults.anthropic_model` および `defaults.anthropic_model_premium`

#### プロンプト構造（共通テンプレート）

```python
SYSTEM_PROMPT = """
あなたは Zynect Media の広告運用コンサルタントです。
顧客向けレポートに記載する文章を生成します。

以下の制約を厳守してください:
1. 専門用語は初出時に必ず括弧書きで平易な説明を併記
   例: フリークエンシー（同一ユーザーへの広告表示回数）
2. 「重要です」「ご注意ください」など曖昧な強調は使わない
3. 数値は必ず根拠データから引用し、推測値は「見込み」と明記
4. 米満氏理論の原則に紐付ける場合は原則ID（P1〜P9 / M-α〜M-λ）を併記
5. 文体: です・ます調 / 簡潔 / 1文40字以下
"""

USER_PROMPT_FOR_ACTION = """
以下のルール検出結果から、優先アクション Top5 の1件分の文章を生成してください。

検出結果:
- ルールID: G26
- 検出: 品質スコア3以下のキーワードが12件
- カテゴリ: キーワード
- 重要度: high
- 原則: P3 結果指標非依存原則
- 想定効果（試算）: 月次CPA改善 約10%、削減額¥185,000/月（信頼度: medium）

クライアント情報:
- 業界: SaaS
- 月次広告費: ¥750,000
- 主担当: 山本太郎様

出力フォーマット (JSON):
{
  "action_name": "...",                // ① 顧客語のアクション名
  "why_important": "...",              // ② 10原則接続の重要性
  "steps": [                           // ③ 実行手順 3〜5ステップ
    {"step": 1, "actor": "[運用代行]", "what": "...", "duration": "5分"},
    ...
  ],
  "estimated_effect_text": "...",      // ④ 想定効果の説明文（数値は層2から流し込み）
  "effort_text": "..."                 // ⑤ 所要時間と難易度の説明文
}
"""
```

#### プロンプトキャッシング

- システムプロンプト + ルール定義 + 業界ベンチマークは **長期キャッシュ** （1時間 TTL）
- クライアント固有情報のみキャッシュ外で送信
- 月次レポートを20社運用するケースで、キャッシュにより約 60-70% のコスト削減見込み

#### 失敗時のフォールバック

- Claude API がタイムアウト・エラーの場合 → **層1+層2 のテンプレートベース文** で代替
- フォールバック文は事前に各ルールに `fallback_text` フィールドで用意（実装時に検討）

### Claude API コスト試算

#### 1レポートあたりの API 呼び出し（概算）

| 用途 | プロンプト入力（推定 tokens） | 出力（推定 tokens） | コール数 |
|------|------------------------|---------------------|---------|
| エグゼクティブサマリ3行 | 2,000 | 500 | 1 |
| 優先アクション Top5（1件ずつ） | 1,500 × 5 | 800 × 5 | 5 |
| 媒体別詳細の検出問題 TOP3 翻訳 | 1,200 × 3媒体 | 600 × 3媒体 | 3 |
| Zynect Insights（5件） | 3,000 × 5 | 1,200 × 5 | 5 |
| **合計** | **約 35,100 input** | **約 14,500 output** | **14** |

#### コスト見積（Sonnet 4.6 基準）

- Input: $3 / MTok × 35K = **約 $0.11**
- Output: $15 / MTok × 14.5K = **約 $0.22**
- キャッシュ無し合計: **約 $0.33 / レポート**
- プロンプトキャッシュ有効時（70%削減）: **約 $0.10 / レポート**

#### 月20社運用の月次コスト
- キャッシュ無し: $0.33 × 20 = **約 $6.6 / 月**
- キャッシュ有り: $0.10 × 20 = **約 $2.0 / 月**

Zynect Insights のみ Opus 4.7 を使う場合、追加コスト **+ 約 $0.50 / レポート**。20社で **+ $10 / 月**。

→ 全体として月額数千円規模。営業価値（差別化レポート）を考えると正当化可能。

---

## 3層の責務分担マップ

| 出力項目 | 層1 (rule) | 層2 (計算) | 層3 (Claude) |
|---------|-----------|-----------|--------------|
| エグゼクティブサマリ3行 | severity 抽出 | スコア差分・損失額算出 | 3行要約の文章化 |
| Health Score 比較（3軸） | - | 業界平均との差分計算 | - |
| 推定改善インパクト（KPI） | expected_impact ベースライン | 数値計算 | 表示文の整形のみ |
| 優先アクション ① 名前 | - | - | 顧客語化 |
| 優先アクション ② 重要性 | 原則タグ抽出 | - | 文章生成 |
| 優先アクション ③ 手順 | - | - | 3〜5ステップ生成 |
| 優先アクション ④ 想定効果 | expected_impact | 削減額計算 | 表示文整形 |
| 優先アクション ⑤ 所要時間 | impact_horizon_weeks | 工数計算 | 難易度判定文 |
| 優先アクション ⑥ ルールID | rule.id 直接 | - | - |
| 媒体別 10原則評価 | yonemitsu_alignment | カウント | 一行コメント生成 |
| 検出問題 TOP3（顧客語） | rule の name | severity 並び替え | 顧客語翻訳 |
| Zynect Insights | redesign_note ソース | - | 独自視点の文章化 |
| 付録 用語集 | - | - | （事前作成・固定） |

---

## 再現性確保策

### 課題: Claude API は同じ入力でも出力が変動する

#### 対策1: temperature 設定
- レポート生成時の `temperature=0.2`（再現性優先）
- Zynect Insights のみ `temperature=0.5`（多様性を許容）

#### 対策2: シード固定
- API リクエストに `seed` パラメータを付与し、同じ入力に対して可能な限り同じ出力を得る

#### 対策3: 出力の構造化
- すべての Claude 呼び出しは JSON フォーマット出力強制
- フリーテキストではなくフィールド単位で取得することで、後処理で差分検出可能

#### 対策4: ログ保存
- 各レポート生成時のプロンプト + 応答を `logs/claude_audit/{client_id}/{date}.json` に保存
- 後で「なぜこのアクションが提案されたか」を Zynect 側で再現確認できる

### 課題: 計算ロジックのバージョン管理

- `analyzers/impact_estimator.py` のロジック変更時はバージョン番号を `expected_impact` 計算結果に併記
- 例: `estimated_savings_yen: 185000, calc_version: "1.2"`
- 過去レポートとの数値差分を説明する根拠になる

---

## v3 コンテンツ戦略の引き継ぎ事項

1. **`expected_impact` フィールドの全ルール棚卸し**: 主要90ルールへの付与作業を Day 2-3 で実施。
2. **`config/benchmarks.yaml` の業界平均値整備**: 出典・更新日付き。SaaS / EC / 地域サービス / B2B / 教育 の5業界を初期対象。
3. **Claude API プロンプトキャッシング実装**: 既存 `claude-api` skill の指針に従い、システムプロンプト・ルール定義をキャッシュ対象に設定。
4. **Claude API のフォールバック設計**: 各ルールに `fallback_text` を追加するか、テンプレート関数で動的生成するか要設計。
5. **再現性ロギング**: `logs/claude_audit/` ディレクトリの新設と `outputs/json_save.py` 拡張。
6. **コスト監視**: Anthropic ダッシュボードでの月次コスト確認運用、または `outputs/cost_tracker.py` 新設。
