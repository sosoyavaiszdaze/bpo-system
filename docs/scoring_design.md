# スコアリング設計書

## 重み付けの3層構造

BPO System のスコアリングは以下の3層で重みを付ける:

1. **severity_weight**: thresholds.yaml で定義 (critical=5.0, high=3.0, medium=1.5, low=0.5)
2. **rule_weight**: 各ルールYAMLの `weight` フィールド。**全ルール 1.0 に統一**。
3. **category_weight**: 各プラットフォームYAMLの `category_weights` で定義。

### 計算式

```
effective_weight = severity_weight × rule_weight × category_weight × polarity_multiplier
S = Σ(passed × effective_weight) / Σ(effective_weight) × 100
```

### rule_weight を 1.0 に統一する理由

- severity_weight と category_weight の2層で十分な重み付けが可能
- rule_weight まで変動させると、3層の掛け算で意図しない重み偏差が発生する
- 例: critical(5.0) × weight(2.0) × category(0.3) = 3.0 vs medium(1.5) × weight(1.0) × category(0.3) = 0.45 → 6.67倍差
- rule_weight を 1.0 に固定すると: critical(5.0) × 0.3 = 1.5 vs medium(1.5) × 0.3 = 0.45 → 3.33倍差（直感的）

## polarity_multiplier

| polarity | multiplier | 説明 |
|----------|-----------|------|
| neutral | 1.0 | 通常評価 |
| preserve | 1.2 | ネガティブシグナル保持のため高評価 |
| monitor_only | 0.3 | 結果指標のため低重み |
| open | 0.5 | 状況依存で判断保留 |
| context_dependent | 0.05 (auto) / 1.0 (manual) / 0.5 (unknown) | 入札戦略による動的解決 |
| budget_first | 0.3 (budget制約あり) / 1.0 (なし) / 0.5 (不明) | 予算制約の有無で動的解決 |
| aggregate | 1.0 | 集約方向 |
| separate | 1.0 | 細分化方向 |

## ID 体系

- YAML rule_id が正 (Single Source of Truth)
- Python check_id は `config/rules/id_mapping.yaml` 経由で YAML rule_id に変換
- `engine/id_mapper.py` が変換ロジックを提供
- Phase 2 で Python 側を直接 YAML ID に書き換え、マッピングファイルを廃止予定

## グレード

| グレード | スコア |
|---------|--------|
| A | ≥ 90 |
| B | ≥ 75 |
| C | ≥ 60 |
| D | ≥ 40 |
| F | < 40 |
