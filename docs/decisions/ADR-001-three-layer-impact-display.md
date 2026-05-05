# ADR-001: 想定改善額の3層表示（パターンC）採用

| 項目 | 値 |
|------|---|
| **Status** | Accepted |
| **Decision Date** | 2026-05-02 |
| **Authors** | Zynect Media（PoC 第1号 パイロットン担当 + Claude Code） |
| **Related ADRs** | [ADR-002](./ADR-002-six-root-cause-groups.md) / [ADR-003](./ADR-003-pixel-health-coupling.md) |

---

## Context

Day 5.1 で生成したパイロットン PoC 第1号レポート（`reports/2026-05-02/pilotton_report_v3.pdf`）において、Top5 想定改善額の合計値 **¥2,578,102/月** が顧客提示時に過大評価との指摘があった。検出された Top5 アクションは M02 (CAPI実装) / M03 (EMQ) / M04 (ドメイン検証) / M09 (学習フェーズ脱出) / M61 (1Pデータ活用) であり、**M02・M03・M04 は本質的に同一の根本原因（計測基盤の整備）**を解決する施策である。これらを個別に積み上げて単純合算すると、実装後の実効改善額を 1.5〜2 倍ほど誤って報告する構造があった。

営業時に過大な数値を提示することは:
1. **顧客信頼の毀損** — 実測値が下回ったときに「言ってたほど効かない」と評価される
2. **意思決定の歪み** — 顧客がリソース配分を過剰投入する判断を下しやすい
3. **Zynect の独自視点（米満氏理論）の説得力低下** — 改善額の根拠が雑に見える

を招く。一方で、改善余地の上限を完全に隠すと「楽観的な可能性が見えない」ため受注インセンティブが弱まる。確度と上振れ余地の両方を提示する設計が必要だった。

## Decision

**最低値を主表示、現実値を参考表示、上限値を小表示とする3層構成**を採用する。

```
┌─ 確実に見込める改善額（最低値）   ¥1.58M/月  ← 主表示（緑、大文字）
│  pixel_health 連動・グループ重複排除込み
│
├─ 相互依存を考慮した現実的試算     ¥2.01M/月  ← 参考表示
│  最大値 + 同グループ2位以下 × duplicate_factor
│
└─ 各施策が独立に最大効果を発揮した場合  ¥2.59M/月  ← 小表示（注記付き）
   重複領域があるため実際にはこの値に到達しない可能性が高い
```

### 計算ロジック
- **最低値（confident）**: グループ最大値 + 残り × duplicate_factor、かつ pixel 休眠時は measurement_foundation を 0.1、非 measurement に decay 0.7 を乗じる
- **現実値（realistic）**: 同上ロジックだが non_mf_decay は適用しない
- **上限値（independent）**: 全件単純合算（重複考慮なし）

### KPI 投影への適用
レポート内の KPI 投影テーブル（月次広告費 / CV / CPA 削減見込み）には、**最低値ベース**の数値を採用する。営業時に「最低でもこれだけ改善する」と確実な数字を示すため。

## Alternatives Considered

| 案 | 内容 | 採用却下理由 |
|---|------|-------------|
| **α** 単純合算のみ | Top5 の estimated_savings_yen をそのまま合計 | 過大評価。Day 5.1 で確認済みの誤りパターンの再現になる |
| **β** 一律 0.5 係数 | 全件に対して合計を 50% に減衰 | グループ別の重複度差を反映できず、「なぜ50%か」の根拠説明ができない |
| **γ** 3層表示（**採用**） | 最低値・現実値・上限値の3軸併記 | 確度と上振れ余地を両立。営業時に説明しやすい |
| **δ** 最低値のみ | 最も保守的な値1つだけ表示 | 上振れ余地が見えず、受注インセンティブが弱まる |

## Result

PoC 第1号（パイロットン、2026-05-02 生成）における実数値:
- 最低値（確実）: **¥1,575,850/月**
- 現実値: **¥2,011,515/月**
- 上限値: **¥2,594,364/月**

レポートでは緑帯の `layer-confident` ブロックで最低値を最大文字サイズで表示し、現実値・上限値はサイズを段階的に小さく配置することで視線誘導を最低値に集中させた（`templates/v3/_styles.html` の `.amount-large/.amount-medium/.amount-small`）。

KPI 投影テーブルも最低値ベース（¥1.58M/月）に切り替え、営業時に「最低でもこれだけ改善する」と確実な数字を提示できる状態。

## Tradeoffs / Risks

- **複雑化**: 1つの数値ではなく3つの数値を提示するため、顧客（特に経営層）が「結局いくらなの？」と混乱する可能性。緩和策として「主表示=最低値」のレイアウトで視線誘導を制御
- **下振れリスクの誤伝**: 最低値も達成できないケースが起こりえる（CV計測完全停止等）。これは ADR-003 の pixel_health 連動で部分的に補正
- **計算ロジックの不透明化**: グループ別 duplicate_factor の妥当性は `priority_weights.yaml` の値に依存。レビューが必要

## Implementation

- `engine/impact_estimator.py`:
  - `calculate_minimum_impact()` — 最低値、pixel_health 連動
  - `calculate_realistic_impact()` — 現実値
  - `calculate_independent_impact()` — 上限値
- `templates/v3/summary.html`: `<section class="impact-three-layer">` 3層レイアウト
- `templates/v3/_styles.html`: `.layer-confident / .layer-realistic / .layer-upper` CSS

## References

- 設計議論メモ: 2026-05-01 〜 2026-05-02 ※ 要確認（議事録未作成）
- Day 5.1 完了報告: 会話履歴内（コミット 5a5c6bc 以降）
- Pattern C 設計: `docs/report_design/v3_priority_score_weights.md`
