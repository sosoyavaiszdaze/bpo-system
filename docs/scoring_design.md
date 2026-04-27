# Scoring Design Document

## 概要

BPO System のスコアリングは、YAML ルール定義に基づく3層重み構造で監査スコアを算出する。

```
S_total = Sum(C_pass * W_sev * W_cat * P_mul) / Sum(C_total * W_sev * W_cat * P_mul) * 100
```

- `C_pass`: 合格チェック (1 or 0)
- `C_total`: 全チェック (常に 1)
- `W_sev`: severity_weight
- `W_cat`: category_weight
- `P_mul`: polarity_multiplier


## 3層重み構造

### Layer 1: severity_weight (W_sev)

ルールの重大度に応じた重み。YAML `severity` フィールドから決定。

| severity | weight | 意味 |
|----------|--------|------|
| critical | 5.0 | 配信前提・計測基盤に影響 |
| high | 3.0 | パフォーマンスに直接影響 |
| medium | 1.5 | 改善推奨 |
| low | 0.5 | 参考情報 |

### Layer 2: category_weight (W_cat)

カテゴリごとのプラットフォーム別重み。各プラットフォームの `*_rules.yaml` に定義。

例 (Google):
```yaml
category_weights:
  構造_設定: 0.31
  クリエイティブ: 0.19
  計測_トラッキング: 0.14
  予算_入札: 0.09
  キーワード: 0.09
  フィード: 0.06
  ターゲティング: 0.06
  配信面: 0.02
  計測: 0.02
  判断ログ: 0.01
```

### Layer 3: polarity_multiplier (P_mul)

ルールの性質に応じたスコア調整係数。YAML `polarity` フィールドから決定。

| polarity | multiplier | 説明 |
|----------|-----------|------|
| neutral | 1.0 | 標準 (大多数のルール) |
| preserve | 1.2 | ネガティブシグナル保持系。違反すると学習を毀損するため重め |
| monitor_only | 0.3 | 結果指標 (例: 品質スコア)。直接改善できないため軽め |
| aggregate | 1.0 | 集約推奨系。標準と同等 |
| separate | 1.0 | 分離推奨系。標準と同等 |
| open | 0.5 | 方向性が未確定な項目。判断保留のため軽め |
| context_dependent | 動的 | 入札戦略に依存。自動入札=0.05, 手動入札=1.0, 不明=0.5 |
| budget_first | 動的 | 予算制約チェック結果に依存。予算制約あり=0.3, なし=1.0, 不明=0.5 |


## rule_weight を 1.0 に統一する理由

全ルールの YAML `weight` フィールドは 1.0 に統一されている。

### 設計判断の根拠

1. **severity_weight + category_weight で十分な差別化が可能**: critical と low の差は 10 倍 (5.0 vs 0.5)。カテゴリ重みと組み合わせると、最大で 30 倍以上の差が生まれる。

2. **個別 rule_weight の調整は主観的になりやすい**: 85 件の Google ルールに個別の重みを付けると、運用者の感覚に依存した恣意的な値になるリスクがある。

3. **polarity_multiplier が文脈依存の調整を担う**: rule_weight が担っていた「状況に応じた重み変更」は polarity_multiplier に移行済み。context_dependent や budget_first が動的に解決する。

4. **保守性**: 1.0 統一により、新ルール追加時に weight をいくつにするかの議論が不要になる。severity と category の選択だけでスコアリングが決まる。

### 有効重み (effective_weight) の計算式

```
effective_weight = severity_weight * rule_weight * category_weight * polarity_multiplier
                 = severity_weight * 1.0 * category_weight * polarity_multiplier
                 = severity_weight * category_weight * polarity_multiplier
```


## polarity_multiplier 詳細

### context_dependent の解決ロジック

3x Kill Rule (G35) などに適用。入札戦略によって意味が変わるルール。

```python
# 自動入札 (target_cpa, target_roas, max_conversions, ...) -> 0.05
#   理由: 自動入札時は止めずに編集。スコアへの影響をほぼゼロにするが、
#         評価済みとしてレポートに記録する
# 手動入札 (manual_cpc, manual_cpm) -> 1.0
#   理由: 手動入札時は停止検討が妥当
# 不明 -> 0.5
```

### budget_first の解決ロジック

予算制限による機会損失 (G13) に適用。Budget Lost 解消が先行する順序がある。

```python
# G39/G08/C13 で予算制約チェックの結果を参照
# 予算制約あり (passed=False) -> 0.3
#   理由: 予算を増やさないと他の改善が効かない
# 予算制約なし (passed=True) -> 1.0
# チェック結果なし -> 0.5
```

### prerequisite chain との関係

prerequisite が不合格の場合、polarity_multiplier とは別に effective_weight に 0.3 が乗算される。

```
blocked_effective_weight = base_effective_weight * 0.3
```

例: G02 (コンバージョンカテゴリ設定) の前提 G01 (コンバージョン重複計測) が不合格の場合、G02 のスコア貢献は通常の 30% に低減される。


## ID マッピング

### 背景

Python チェックモジュール (`checks/*.py`) が発行する check_id と、YAML ルール定義の rule_id は異なる体系を持つ。

- Python: `G01, G03, G-PM1, M-PI1, T-TC1, ...` (実装順・機能グループ順)
- YAML: `G01-G85, M01-M55, T01-T35` (体系的な通し番号)

### マッピングファイル

`config/rules/id_mapping.yaml` で Python check_id -> YAML rule_id の変換を定義。

```yaml
google:
  G01: G25      # Python G01 (命名規則) -> YAML G25 (ネーミングルール整合)
  G03: G39      # Python G03 (STAG構造) -> YAML G39 (広告グループあたりKW数)
  G07: _unmapped  # マッピング先なし (PMax+Search重複チェック)
```

### _unmapped の扱い

`_unmapped` は「YAML ルール定義に対応する rule_id がない」ことを意味する。この場合、`to_yaml_id()` は元の Python check_id をそのまま返し、YAML ルール定義にマッチしないためデフォルト重み (severity=medium, category=other) が適用される。

### マッピングモジュール

`engine/id_mapper.py` が変換を担当:

- `to_yaml_id(python_id, platform)`: Python -> YAML 変換
- `to_python_id(yaml_id, platform)`: YAML -> Python 逆変換
- `get_mapping_coverage(platform)`: カバレッジ統計
- `clear_cache()`: テスト用キャッシュクリア

Phase 2 で Python 側を直接 YAML ID に書き換え、マッピングファイルを廃止予定。


## グレード判定

| グレード | スコア範囲 |
|---------|-----------|
| A | 90 - 100 |
| B | 75 - 89 |
| C | 60 - 74 |
| D | 40 - 59 |
| F | 0 - 39 |


## クロスプラットフォームスコア

複数媒体の統合スコアは予算シェア加重平均で算出。

```
S_cross = Sum(S_platform * budget_share) / Sum(budget_share)
```

予算シェアが不明な場合は単純平均にフォールバック。
