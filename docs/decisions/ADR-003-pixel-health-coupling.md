# ADR-003: pixel_health 連動ロジック設計

| 項目 | 値 |
|------|---|
| **Status** | Accepted |
| **Decision Date** | 2026-05-02 |
| **Authors** | Zynect Media（PoC 第1号 パイロットン担当 + Claude Code） |
| **Related ADRs** | [ADR-001](./ADR-001-three-layer-impact-display.md) / [ADR-002](./ADR-002-six-root-cause-groups.md) |

---

## Context

PoC 第1号（パイロットン、`act_566972639374407`）の Meta API 取得結果から、**5 ピクセル中 3 件（CLOOKING Pixel / CLOOKING_ピクセル / 削除）が 270 日以上発火していない長期休眠**であることが判明した。

具体的には:
- CLOOKING Pixel: 最終発火 270 日前（休眠）
- CLOOKING_ピクセル: 最終発火 366 日前（休眠、CLOOKING Pixel と重複疑い）
- MYNAILPLEX_LP01: 直近発火（アクティブ）
- アゲルキャリア_Pixel: 直近発火（アクティブ）
- 削除: 未発火（廃止予定）

この状態では計測基盤が構造的に未達であり、**他施策（学習フェーズ最適化、ターゲティング改善、CR 量産等）の効果は計測欠損のため正しくシステムに反映されない**。具体的には:
1. CAPI 実装（M02）/ ドメイン検証（M04）/ EMQ（M03）など計測整備が完了するまで、他施策の改善効果は AI 自動入札の学習に正しく反映されない
2. ADR-002 で定義した `delivery_learning_or_structure` / `targeting` 等の施策が「効いていない」と誤検出される
3. ADR-001 の3層インパクト計算で、計測未整備状態にも関わらず他施策の効果額を満額計上すると現実から乖離する

このため、ピクセル健全性の状態によって計算ロジックを切り替える「pixel_health 連動」が必要だった。

## Decision

**dormant_days >= 270 または duplicate_pixel_detected = true の場合**、3層インパクト計算で以下のオーバーライドを適用する:

### オーバーライド内容

```yaml
pixel_health_overrides:
  dormant_threshold_days: 270
  measurement_foundation_duplicate_factor_when_dormant: 0.1   # 0.2 → 0.1
  non_measurement_confidence_decay_when_dormant: 0.7
```

### 適用ルール
1. **measurement_foundation の `duplicate_factor` を 0.2 → 0.1 に切替**
   - 計測修復が最優先課題のため、measurement_foundation 内の各施策はほぼ同一の根本原因（計測修復）に収束する → 重複度をさらに高く見積る（factor 縮小）
   - 結果として最低値・現実値の measurement 部分が小さく出るが、現実を反映した数値となる
2. **非 measurement_foundation グループに confidence_decay = 0.7 を乗じる**
   - 計測未整備のため、delivery_learning / creative / budget / targeting 施策の効果は本来の 70% 程度しか顕在化しないと仮定
   - `independent` グループは減衰対象外（pixel_health に依存しない単発改善のため）
3. **pixel_health 警告をレポートに表示**
   - `<p class="note-warning">` で「ピクセル休眠 N 件、最大 X 日未発火 → 計測基盤の修復を最優先」を明示

### 検出ロジック（`detect_pixel_health()`）
クライアント設定 `clients.yaml` の `ads.meta.pixels[]` から以下優先順位で休眠日数を抽出:
1. `dormant_days` フィールド（明示指定、Task 1 で追加）
2. `last_fired_time` フィールド（Meta API 取得値）
3. `note` フィールドの正規表現 `r"最終発火\s*(\d+)日前"`
4. 名前ヒューリスティック（"削除" / "廃止"）

`duplicate` フラグも明示指定（pixel.duplicate=true）または名前正規化（CLOOKING/CLOCKING 表記ゆれ吸収）で検出。

## Alternatives Considered

| 案 | 内容 | 採用却下理由 |
|---|------|-------------|
| **α** 一律 duplicate_factor | pixel 状態に関係なく duplicate_factor=0.2 固定 | 計測未整備時の他施策効果減衰を捉えられず、ADR-001 の最低値が現実より過大になる |
| **β** 警告のみ表示 | 数値計算には影響させず、レポートで警告だけ表示 | 顧客が警告を読み飛ばすリスク。3層インパクト数値が現実から乖離したまま提示される |
| **γ** 連動ロジック（**採用**） | dormant 検知時に measurement factor=0.1 + 非 measurement decay=0.7 + 警告表示 | 数値と表示の両方で現実を反映、営業時の説得力と顧客信頼を両立 |

### しきい値の選定根拠
- **dormant_threshold_days = 270**: Meta 公式の「アクティブピクセル」定義は明確でないが、業界実務上は 90 日以上で判定するケースが多い。今回 270 日にしたのは「単純な配信停止」と「事業として停止」の境界として保守的に設定（90 日では短期休止と区別がつかない）
- **measurement factor 0.2 → 0.1**: 計測修復が他全施策の前提であることを明示する半減レート
- **non_mf_decay = 0.7**: 計測欠損時の他施策効果減衰率。30% 程度の効果しか出ないという保守的見積（要 Phase 2 実データ検証）

## Result

### パイロットン PoC 第1号での連動発動状況（2026-05-02 再生成）

```
pixel_health 検出:
  dormant_days: 366  ← しきい値 270 達成 → 連動発動
  duplicate_pixel_detected: True
  dormant_pixel_count: 3
  active_pixel_count: 2

3層インパクト（連動 ON）:
  最低値: ¥1,575,850/月    ← 連動 OFF 時 ¥2,076,276 から △24%
  現実値: ¥2,011,515/月    ← 連動 OFF 時 ¥2,076,276 から △3%
  上限値: ¥2,594,364/月    ← 変化なし（独立計算のため）

最低値内訳（連動 ON 時）:
  measurement_foundation: factor=0.1 (← 0.2 から切替), max=¥494,538, group_total=¥559,299
  delivery_learning_or_structure: factor=0.3, max=¥863,480, group_total=¥604,436 [non_mf_decay 0.7 適用]
  targeting: factor=0.4, max=¥588,736, group_total=¥412,115 [non_mf_decay 0.7 適用]
```

レポートには警告メッセージも自動表示:
> ⚠ 現状ピクセル休眠（3 件、最大 366 日未発火）が検出されているため、計測基盤の修復を最優先としています。修復後に他施策の効果が顕在化します。

### 営業時の効果
- 顧客に「計測修復が効果発現の前提」と論理的に説明可能
- 「最低 ¥1.58M」と確実な値を提示しつつ、「計測修復後は ¥2M 超を狙える」上振れ余地も同時に伝達
- 計測未整備という構造的課題を表面化させる説得力

## Tradeoffs / Risks

- **しきい値 270 日の正当性**: Phase 2 で複数クライアント実データを集めて見直し必要。短すぎると false positive、長すぎると false negative
- **non_mf_decay = 0.7 の根拠**: 現在は仮値。実運用で「計測整備前後の他施策効果差分」を蓄積して検証する Phase 2 タスクが必要
- **active_pixel_count が 0 となる罠**: detect_pixel_health の active 判定（30 日以内発火）の運用注意。今回 MYNAILPLEX/アゲルキャリア が dormant_days=0 と明示指定されたため正しく 2 件検出
- **複数クライアントへの一般化**: パイロットン特有の「3 ブランド × 5 ピクセル」運用を前提としたロジック。BANDAL（1 ピクセル運用）等での挙動確認は Phase 2 課題
- **pixel_health の自動更新**: 現状 clients.yaml の dormant_days は手動設定。pipeline.py 起動時に Meta API でリアルタイム取得し note を自動更新する仕組みが Phase 2 で必要

## Implementation

- `analyzers/ads_audit.py`: `detect_pixel_health()` 関数（`pixel.dormant_days` / `pixel.duplicate` フィールド最優先で検出）
- `engine/impact_estimator.py`:
  - `calculate_minimum_impact(actions, rules, weights, pixel_health=...)` — pixel_health 引数で連動制御
  - `calculate_realistic_impact()` — 同上
- `engine/report_generator_v3.py`: `build_v3_context()` で `detect_pixel_health()` を呼び出し、3 関数に渡す
- `config/priority_weights.yaml`: `pixel_health_overrides` セクション
- `config/clients.yaml`: 各 pixel に `dormant_days` / `duplicate` フィールド追加（Task 1）
- `templates/v3/summary.html`: `{% if pixel_dormant %}` 条件分岐で警告メッセージ表示

## References

- パイロットン Meta API 検出結果: 2026-05-02 取得（ADR-002 と同日）
- pixel_health 連動の実装: Day 5.1 v2 Task 1（コミット未確定、ローカル変更）
- 関連設計ドキュメント: `docs/decisions/meta_rules_classification.md`
- 業界実務における dormant 定義: ※ 公式定義不明、今後の調査課題
