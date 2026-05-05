# ADR-002: 6 グループ root_cause_group 分類設計

| 項目 | 値 |
|------|---|
| **Status** | Accepted |
| **Decision Date** | 2026-05-02 |
| **Authors** | Zynect Media（PoC 第1号 パイロットン担当 + Claude Code） |
| **Related ADRs** | [ADR-001](./ADR-001-three-layer-impact-display.md) / [ADR-003](./ADR-003-pixel-health-coupling.md) |

---

## Context

ADR-001 で確定した「3層インパクト表示」を機能させるためには、**ルール（施策）を根本原因（root_cause）でグループ化し、グループ内では重複度を係数で割り引く**仕組みが必要だった。Day 5（v3.0）では暫定的に **8 グループ**（measurement_foundation / learning_phase_protection / creative_diversity / negative_signals / structure_optimization / audience_targeting / ad_rank_optimization / first_party_data）を運用していたが、以下の問題があった:

1. **粒度が細かすぎる**: 8 グループあると Top5 が概ね 5 グループに分散してしまい、グループ内重複排除がほぼ機能しない（各グループ 1 件しかなく `factor` 適用機会がない）
2. **境界ルールが曖昧**: `learning_phase_protection` と `structure_optimization` は実装上ほぼ重複する（広告セット集約と学習フェーズ管理は同時に行う）。`audience_targeting` と `first_party_data` も同様
3. **重複度の差を表現できない**: 全グループで一律 `overlap_factor` を運用していたため、「計測基盤系は重複度が極めて高い」「クリエイティブ系は中程度」といった現実的な強弱を反映できなかった

PoC 提案前に上記課題を解消し、業務上意味のある単位で重複排除が機能する分類体系が必要だった。

## Decision

**6 グループ + グループ別 duplicate_factor + needs_review フラグ** の構成を採用する。

### 6 グループ定義

| グループ ID | 表示名 | 含まれる典型ルール | duplicate_factor |
|-----------|--------|------------------|-----------------:|
| `measurement_foundation` | 計測基盤 | M01-M08 / M62（CAPI / EMQ / ドメイン検証 / AEM） | **0.2** |
| `delivery_learning_or_structure` | 配信学習 + 構造設定 | M09-M15 / M44 / M48（学習脱出 / 集約 / キャンペーン目的） | **0.3** |
| `creative_optimization` | クリエイティブ最適化 | M21-M38 / M47 / M57-M67（CR量産 / 疲弊 / Hook） | **0.5** |
| `budget_allocation` | 予算配分 | M20 / M45 / M52（カタログ供給 / 入札上限 / 粒度判定） | **0.4** |
| `targeting` | ターゲティング | M50 / M51 / M53 / M54 / M61 / M69 / M70（LLA / 1Pデータ / 除外） | **0.4** |
| `independent` | 独立施策 | M49（オーバーラップ）他、他グループに依存しない単発改善 | **1.0** |

### duplicate_factor の意味
- グループ内最大インパクトの 1 件は `1.0` で採用
- 同グループ内 2 件目以降には上記 factor を乗じる
- `independent = 1.0` は完全独立扱い（重複なし、満額加算）

### needs_review フラグ
classification_confidence < 0.75 のルール、または複数グループにまたがるルール（Advantage+ / ASC / リターゲティング / 類似 / 除外が絡む境界）には `needs_review: true` を付与し、人手レビュー対象として `docs/decisions/meta_rules_classification.md` に別表で抽出する。Meta 70 ルールの自動分類で **16 件**が needs_review となった（M11/M16/M17/M18/M19/M20/M23/M42/M43/M46/M52/M55/M56/M62/M63/M64）。

## Alternatives Considered

| 案 | 内容 | 採用却下理由 |
|---|------|-------------|
| 5 グループ案 | attribution_settings を独立グループ化、計 5 グループ | アトリビューションは計測軸と密結合のため measurement_foundation に統合する方が運用上自然 |
| **8 グループ案（既存 v3.0）** | learning と structure を分離、creative と negative_signals を分離等 | 粒度過剰で重複排除がほぼ機能しない、グループ境界が曖昧 |
| 3 グループ案 | 計測 / 配信 / クリエイティブ の 3 大区分のみ | 粒度が粗すぎてグループ内 factor 設計が雑になる、施策の独立性を表現できない |
| **6 グループ + factor 別（採用）** | 計測（0.2） / 配信学習構造（0.3）/ CR（0.5） / 予算（0.4） / ターゲ（0.4） / 独立（1.0） | 業務単位とグループ粒度が一致、factor の強弱で重複度差を表現可能 |

## Result

### 自動分類結果（Meta 70 ルール、`docs/decisions/meta_rules_classification.md` 出力済み）

| グループ | ルール数 | 主なルール |
|---------|--------:|-----------|
| measurement_foundation | 9 | M01-M08, M62 |
| delivery_learning_or_structure | 15 | M09-M15, M44, M48, M65, M68 等 |
| creative_optimization | 28 | M21-M38, M47, M57-M67 等 |
| budget_allocation | 3 | M20, M45, M52 |
| targeting | 14 | M39 / M40 / M50-M56 / M61 / M69 / M70 等 |
| independent | 1 | M49（オーバーラップ） |
| **合計** | **70** | |

### confidence 分布
- high (≥0.85): **54 件**（人手分類確定 + 明示マッピング済み）
- low (<0.75, needs_review=true): **16 件**

### パイロットンでの効果（ADR-001 連動）
Top5 = M02 / M03 / M04 / M09 / M61 の場合:
- measurement_foundation 内 3 件（M02, M03, M04）→ 重複排除 factor=0.2 適用
- delivery_learning（M09）/ targeting（M61）はグループ内単独 → factor 適用なし
- 単純合算 ¥2.59M → 重複排除後 ¥2.07M（**△20%**）

## Tradeoffs / Risks

- **境界ルールの判定揺れ**: M11 (CBO/ABO) / M20 (カタログ連携) / M52 (粒度判定) など、複数グループにまたがるルールが 16 件存在。`needs_review=true` で人手レビューに回しているが、完全自動化はできない
- **factor 値の根拠**: `0.2 / 0.3 / 0.4 / 0.5 / 1.0` は AI 議論で確定したが、実運用データでの妥当性検証は今後必要（Phase 2 課題）
- **Google / TikTok への展開**: 今回は Meta 70 ルールのみ完了。Google 108 / TikTok 46 ルールの自動分類は Phase 2 で別タスク化
- **measurement_foundation の高重複度（factor=0.2）**: CAPI 実装が EMQ も AEM も底上げするため重複度が極めて高い。pixel_health 連動時はさらに 0.1 まで下がる（ADR-003）

## Implementation

- `config/priority_weights.yaml`: `rule_root_cause`（6グループへのマッピング）+ `duplicate_factors`（グループ別係数）
- `config/rules/meta_rules.yaml`: 各ルールに `root_cause_group / priority_in_group / classification_confidence / classification_rationale / needs_review` を付与
- `engine/impact_estimator.py`: `_build_rule_to_group()` ヘルパー + `calculate_minimum_impact()` / `calculate_realistic_impact()` / `aggregate_with_dedup()` で利用
- `scripts/(自動生成)`: `/tmp/classify_meta_rules.py` で 70 ルール一括分類

## References

- 自動分類結果: `docs/decisions/meta_rules_classification.md`
- AI 議論メモ: 2026-05-02 ※ 議事録未整備（要確認）
- グループ命名の経緯: ユーザー指示（attribution_settings 統合 / standalone → independent リネーム）
- Pattern C 設計: `docs/report_design/v3_priority_score_weights.md`
