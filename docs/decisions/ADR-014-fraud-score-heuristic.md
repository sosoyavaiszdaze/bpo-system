# ADR-014: Phase A fraud_score ヒューリスティック合成式

- **Status**: Accepted (Phase A 暫定)
- **Date**: 2026-05-06
- **Deciders**: Zynect Media
- **Related**: ADR-006 (AdTruth フレームワーク), ADR-009 (二層判断 / 灰ゾーン), ADR-013 (5 層ルール)

## 1. Context

Phase A 内部レビュー期間 (2026-05-06 〜 5/19 想定) においては、papa-torb/adtruth OSS や独自カスタムタグの本格取込は不可能。一方で 5/7 pilotton 提案の場で「不正トラフィック疑義の検知 → 灰ゾーン判断 → 顧客アクション」のフローを「動いている」状態で見せる必要がある。

そこで Phase A は **媒体公式 API から取得済の指標だけを入力としたヒューリスティック合成式** で `fraud_score (0.0-1.0)` を算出し、Phase B Week 2-3 で fingerprint / isolation_forest 等の本番アルゴリズムに **段階置換** する。

## 2. Decision

### 2.1 入力指標 (campaign-level)

媒体 API から取得可能な 6 指標を入力とする。各指標は **0-1 に正規化** された個別スコアに変換し、加重平均で合成する。

| 指標 | 略称 | 取得元 (Phase A) | Phase B 置換 |
|---|---|---|---|
| Frequency 異常 | `freq_anom` | Meta `frequency` field | AdTruth fingerprint frequency |
| CTR 異常低 | `ctr_low` | Meta `ctr` field | LP click telemetry |
| CTR 異常高 | `ctr_high` | Meta `ctr` field | Click-farm fingerprint |
| CVR 異常低 (高消費時) | `cvr_low` | `conversions/clicks` | AdTruth real_cv flag |
| CPA 異常高 | `cpa_high` | Meta `cpa` field | アカウント中央値比較 |
| MFA Placement Share | `mfa_share` | (Phase A: 0 固定) | Meta Placement audit log |

### 2.2 個別スコア算出式

各指標は `[0, 1]` に正規化される。`max(0, min(1, ...))` で範囲外を切る (clamp)。

```
freq_anom    = clamp((frequency  - 3.0) / 5.0)            # freq=3 で 0、freq=8 で 1
ctr_low      = clamp((0.5 - ctr_pct) / 0.5)               # ctr 0.5% 以上で 0
ctr_high     = clamp((ctr_pct - 5.0) / 5.0)               # ctr 5% 未満で 0、10% で 1
cvr_low      = clamp((1.0 - (cvr_pct / cvr_baseline)))    # baseline 比 100% で 0
                                                           # ただし cost < 30000 円なら 0 (低消費は無視)
cpa_high     = clamp((cpa - cpa_baseline * 1.5) / (cpa_baseline * 2))  # 1.5x で 0、3.5x で 1
                                                                       # cpa=0 (cv 0) は除外
mfa_share    = 0.0                                        # Phase A 固定、Phase B で置換
```

`cvr_baseline` / `cpa_baseline` はクライアントの直近 30 日中央値を別途算出。Phase A は `clients.yaml` の業界平均でフォールバック (beauty_d2c の場合 cvr_baseline=2.0%, cpa_baseline=¥6,000)。

### 2.3 加重合成

```
fraud_score = (
    0.25 * freq_anom    +
    0.20 * cvr_low      +
    0.20 * ctr_low      +
    0.15 * cpa_high     +
    0.15 * ctr_high     +
    0.05 * mfa_share
)
```

合計係数 = 1.00。Phase A では `mfa_share` が常に 0 のため、実質 6 指標 → 5 指標で 0.95 の最大値となる。これは **Phase A の最大不正検知強度の上限を 0.95** に抑える設計上の意図でもある (Phase B で OSS 本格取込時に 1.0 まで使えるようにする)。

### 2.4 4 象限分類の閾値

```python
fraud_threshold     = 0.60   # fraud_score ≥ 0.60 → 「不正高」
cv_rate_threshold   = 5.0    # cvr_pct    ≥ 5.0  → 「CV 高」
```

| 象限 | fraud | CV | アクション (ADR-009 準拠) |
|---|---|---|---|
| **Black** (黒) | 高 (≥ 0.60) | 低 (< 5%) | 即ブロック候補 (Phase A は推奨提示のみ) |
| **Gray** (灰) | 高 (≥ 0.60) | 高 (≥ 5%) | ADR-009 二層判断、ChatWork 報告必須 |
| **White** (白) | 低 (< 0.60) | 高 (≥ 5%) | 優良トラフィック保護 |
| **Unknown** (無) | 低 (< 0.60) | 低 (< 5%) | 判断保留、低優先 |

### 2.5 出力スキーマ

```python
{
  "client_id": "pilotton",
  "media": "meta",
  "computed_at": "2026-05-06T13:00:00+09:00",
  "fraud_threshold": 0.60,
  "cv_rate_threshold": 5.0,
  "samples": [
    {
      "campaign": "...",
      "fraud_score": 0.72,
      "cv_rate_pct": 1.3,
      "ad_cost": 1112124,
      "cv_count": 14,
      "components": {
        "freq_anom": 0.4,
        "ctr_low":   0.0,
        "ctr_high":  0.5,
        "cvr_low":   0.7,
        "cpa_high":  0.3,
        "mfa_share": 0.0
      },
      "quadrant": "black"   # または gray / white / unknown
    },
    ...
  ],
  "summary": {
    "total_samples": 3,
    "black_count": 1,
    "gray_count":  1,
    "white_count": 1,
    "unknown_count": 0
  }
}
```

## 3. Phase B 置換ポイント

Phase B Week 2-3 で以下を順次置換する。**式自体の構造は維持**し、個別スコア算出関数を OSS 出力に差し替える。

| 個別スコア | Phase A | Phase B |
|---|---|---|
| `freq_anom` | Meta frequency | AdTruth fingerprint frequency (デバイス別) |
| `ctr_low` | 媒体 ctr | LP server-side click telemetry |
| `ctr_high` | 媒体 ctr | Click-farm fingerprint (papa-torb 派生) |
| `cvr_low` | 媒体 conversions/clicks | AdTruth real_cv flag (CV 品質スコア) |
| `cpa_high` | 媒体 cpa | アカウント中央値 (90 日 rolling) |
| `mfa_share` | 0 固定 | Meta Placement Insights audit log |

加重係数も Phase B で再調整可能 (`config/fraud_score_weights.yaml` に外出し予定)。本 ADR の式は **Phase A 暫定値**。

`compute_fraud_score()` 関数のシグネチャ (`samples_in: list[dict] -> samples_out: list[dict]`) は維持され、内部の各 `_compute_<component>()` ヘルパだけが置換される。

## 4. テスト方針

- 単体: 既知の入力 (freq=4, ctr=0.3, cvr=0.5%, cpa=¥18000) に対して期待 score (≈0.55) を返すこと
- 4 象限分類の境界値テスト (fraud=0.59 vs 0.60, cvr=4.99 vs 5.0)
- 既知の pilotton 5/6 データで 3 キャンペーン → 4 象限の何れかにそれぞれ分類されること
- 既存 397 テスト維持

## 5. 既知の限界 (Phase A 暫定の制約)

- **fraud_score は媒体公式指標の合成にすぎない**: 真の fingerprint / device-id ベース fraud は未検出
- **新規キャンペーンで baseline 不在**: 業界平均フォールバックで暫定値が出るが精度は低い
- **CV 数が極端に少ないキャンペーンは cvr_low が暴れる**: 30,000 円未満の低消費キャンペーンは `cvr_low = 0` で抑制
- **mfa_share = 0 のため白側の精度劣化**: Phase B で取込必須

これらは ChatWork 通知の **末尾 disclaimer** で明示し、顧客判断のミスリードを避ける。

## 6. 関連実装

- `analyzers/fraud_score.py` (新規、本 ADR 準拠)
- `engine/threshold_optimizer.py` (4 象限分類は既存、入力に本算出結果を渡す)
- `engine/cv_preservation_monitor.py` (ブロック候補発生時に block_events.yaml に記録)
- `templates/chatwork/_grey_zone_decision_meta.md.j2` (gray 象限を顧客に報告)
