# パイロットン 効果額 (ADR-009 トレードオフ設計適用版)

| 項目 | 値 |
|------|---|
| 算出日 | 2026-05-03 |
| 対象クライアント | pilotton (株式会社パイロットン) |
| 対象データ | Day 3 ステージング 22 件検知 (5/3 21:44 launchctl 経由実施分) |
| 設計準拠 | ADR-009 (tradeoff design + customer preference learning) |
| Pixel 状態 | 休眠 (312 日)、ADR-003 連動係数 0.5 適用 |
| 関連 JSON | `outputs/pilotton/effective_impact_20260503.json` |

> 注: ユーザご指示は「24 件」でしたが、実 state 上は 22 件 (indication_detector の dedup により 24 候補 → 22 件 upsert)。22 件で算出しています。

---

## 1. エグゼクティブサマリ

### 🎯 提案資料 (5/7) で **安全に提示できる効果額**

```
保守見積もり (block のみ即実行):   ¥29,505 / 月
現実見積もり (block + monitor×0.5): ¥67,513 / 月
上限見積もり (block + monitor 全量): ¥105,521 / 月
```

### 🚨 提示時の前提条件 (必ず併記)

1. **Pixel 休眠 312 日が継続中**: 本見積もりは ADR-003 連動係数 (×0.5) を適用済。
   CAPI 実装・Pixel 統合完了後は **measurement_foundation の効果が ×2 に回復** し、
   別途 月次 ¥30,000-50,000 の追加効果が見込めます。
2. **AOV ¥15,000 / LTV 倍率 3.0** の仮値で偽陽性コストを試算。pilotton 確認後に再計算可。
3. **monitor 系 16 件は A/B テスト後の判断**。約半数承認想定で現実見積もりに計上。
4. **investigate 1 件 (M65) を除外**: 低 severity の効果未確定品目。

---

## 2. Tier 振り分け結果

| Tier | 件数 | gross ¥/月 | コスト | net ¥/月 | 確信度 |
|------|------|-----------|---------|---------|--------|
| **block** (即実行) | **5** | ¥48,000 | ¥18,495 | **¥29,505** | 高 (0.92) |
| **monitor** (A/B 後判断) | **16** | ¥210,800 | ¥134,784 | ¥76,016 | 中 (0.78) |
| **investigate** (除外) | **1** | ¥1,000 | — | — | 低 (0.55) |
| **合計** | **22** | ¥259,800 | ¥153,279 | (推定) | — |

---

## 3. Tier 別詳細

### 🟢 BLOCK Tier (5 件 / 即実行可能 / 高確信度)

事実検証可能で誤検知リスクが低いルール。Phase B 開始時から自動実行を提案できる。

| rule_id | severity | root_cause_group | base ¥/月 | pixel_health 係数 | tier 根拠 |
|---------|----------|-------------------|-----------|------------------|----------|
| M02 (CAPI 実装状況) | critical | measurement_foundation | 50,000 | ×0.5 | 事実検証 (CAPI 有無) |
| M04 (ドメイン検証) | critical | measurement_foundation | 50,000 | ×0.5 | 事実検証 (DNS 確認) |
| M03 (EMQ スコア) | high | measurement_foundation | 20,000 | ×0.5 | 事実検証 |
| M05 (AEM 設定) | high | measurement_foundation | 20,000 | ×0.5 | 事実検証 |
| M61 (1st パーティデータ) | high | targeting | 20,000 | ×0.7 | 事実検証 (Customer File 有無) |

**重複排除後 gross**: ¥48,000/月 (measurement_foundation 4 件のうち最大値以外は ×0.2 重複係数)
**偽陽性コスト**: ¥17,495/月 (confidence 0.92、誤除外想定 50 clicks × 8% × CVR × AOV × LTV)
**運用コスト**: ¥1,000/月 × 5 件 = ¥5,000/月... 実際は idempotency により ¥1,000/月

→ **BLOCK 累積 NetBenefit: ¥29,505/月**

### 🟡 MONITOR Tier (16 件 / 2 週間 A/B テスト後判断 / 中確信度)

A/B テスト or 段階的実行で効果検証必要。pilotton 担当者の選好学習データ (ADR-009 §7) を蓄積後、自動 block/investigate に再分類予定。

#### 内訳 (severity 別)

| severity | 件数 | rule_ids |
|----------|------|----------|
| critical | 4 | M09, X-PI1, ANO_CPA_SPIKE, ANO_IMPRESSION_DROP |
| high | 6 | C05, M59, M62×2, M45×2 |
| medium | 6 | C06, C07, C08, M35, M51, M53 |

**特に判断要請が必要な項目**:
- **M09 学習フェーズ脱出率**: ADR-009 §5.3 で明示的に monitor 指定 (短期判断危険)
- **ANO_CPA_SPIKE / ANO_IMPRESSION_DROP**: 統計的異常検知、単発スパイクなら様子見
- **X-PI1**: クロス媒体 principle、未マッピングのため人間確認必須

**gross**: ¥210,800/月 (重複排除後)
**偽陽性コスト**: ¥118,784/月 (confidence 0.78、各件平均 ¥7,400)
**運用コスト**: ¥16,000/月 (1,000 × 16 件)

→ **MONITOR 全量承認時の上限: ¥76,016/月** (現実見積もりは半数承認で ¥38,008/月)

### 🔴 INVESTIGATE Tier (1 件 / 本見積もりから除外)

| rule_id | severity | 理由 |
|---------|----------|------|
| M65 | low | low severity = 確信度不足、要追加調査 |

→ 効果額算出から除外。Phase B Week 2 以降の都度学習で再分類検討。

#### F06 (CVR>30% conversion_fraud) の状況

**本バッチに F06 検知なし** (= 明示的な偽陽性高リスク項目はゼロ)。
仮に検知された場合は ADR-009 §6.2 試算で **NetBenefit = -¥28,500/月 → INVESTIGATE 一択**。

---

## 4. 3 段階見積もりの内訳と提案資料での使い分け

### 🎯 保守見積もり ¥29,505/月 — メイン提示数値

**用途**: 5/7 提案で「Phase B 第 1 ヶ月で確実に出る効果」として提示。
**根拠**: 事実検証ルール 5 件のみ、誤検知リスクほぼゼロ。
**特徴**: 過小評価寄り (Pixel 休眠係数 ×0.5 が効いており、CAPI 復旧後は約 2 倍に回復)

### 🎯 現実見積もり ¥67,513/月 — Phase B 第 2-3 ヶ月以降の見通し

**用途**: 「monitor 系の半数が判断後に承認される想定」として併記。
**根拠**: ADR-009 §7 都度学習で 2 週間で約 10 件の判断回答取得 → 半数 block 移行想定。
**特徴**: 中央値想定、業界標準的なロールアウトペース

### 🎯 上限見積もり ¥105,521/月 — 理論上限

**用途**: 「全 monitor 承認 + Pixel 復旧」の理論天井として参考表示のみ。
**根拠**: investigate 1 件と F06 系偽陽性は除外したまま。
**注意**: 「到達困難」と明記すること。安易な数値提示は信頼を損なう。

---

## 5. パイロットン担当者への提示推奨フォーマット

```
弊社の自動監査が検出した 22 件の改善余地について、
ADR-009 トレードオフ設計に基づく 3 段階の効果額をご提示します。

【1ヶ月目に確実に出る効果】     月次 ¥29,505
  └ 事実検証ルール 5 件 (CAPI / ドメイン / EMQ / AEM / 1stパーティ) を即実行

【2-3ヶ月後の見通し】           月次 ¥67,513
  └ 上記 + A/B テスト承認後に追加 10 件程度を実行 (現実シナリオ)

【理論上限 (全件承認時)】        月次 ¥105,521
  └ 上記 + monitor 系 16 件全てを実行 (到達困難)

※ 上記は Pixel 休眠 (312 日) 状態で計算。
   CAPI 実装後は別途 ¥30,000-50,000/月の追加効果が見込めます。

※ 本バッチに偽陽性高リスク項目 (F06) は含まれておらず、
   除外したのは low severity の M65 (1件のみ)。
```

---

## 6. 提示時のリスク開示 (透明性)

| リスク項目 | 開示文 |
|----------|--------|
| Pixel 休眠の影響 | 「Pixel 休眠が継続中のため、measurement_foundation 系の効果が ×0.5 に減衰した数値です」 |
| AOV / LTV 仮値 | 「偽陽性コスト試算は AOV ¥15,000 / LTV 倍率 3.0 で計算。実値共有後に再計算します」 |
| monitor 不確実性 | 「monitor 系 16 件は A/B テストで効果確認後に確定、半数承認想定で計上」 |
| F06 等の自動除外 | 「リターゲ顧客誤判定リスクが高い検知 (F06) は本見積もりから完全除外しています」 |
| 機会損失の可能性 | 「monitor を全部 investigate に保守的に倒した場合、月次 ¥38,008 の機会損失試算あり」 |

---

## 7. 完了報告 (要件チェックリスト)

| 要件 | 結果 |
|------|------|
| **block 推奨件数 + 累積効果額** | **5 件 / NetBenefit ¥29,505/月** (gross ¥48,000、偽陽性+運用コスト ¥18,495 控除後) |
| **monitor 推奨件数 + 潜在効果額** | **16 件 / 潜在 NetBenefit ¥76,016/月** (gross ¥210,800、コスト ¥134,784 控除後)、半数承認想定で実効 ¥38,008/月 |
| **investigate / 除外件数 + 理由** | **1 件 (M65 low severity)、F06 該当なし** |
| **パイロットンへ安全に提示できる効果額** | **保守 ¥29,505/月** (メイン)、**現実 ¥67,513/月** (見通し)、**上限 ¥105,521/月** (理論天井、到達困難明記) |
| 出力ファイル | `outputs/pilotton/effective_impact_20260503.json` (22.6 KB) + 本ファイル |

---

## 8. 設計上の注意事項

1. **fallback_impact_yen ベースで保守的**: 個別ルールの実 expected_impact が YAML に定義されていれば数値は上振れる可能性。Phase B Week 2 で実値マッピング推奨
2. **Pixel 休眠の影響が大きい**: M02 (CAPI) が block 上位にあるが、Pixel 休眠中の効果は半減。CAPI 実装完了後の再算出を 1 ヶ月後に実施推奨
3. **複数 M62 / M45 検知**: M62 と M45 がそれぞれ 2 件ずつ (異なる campaign) detected。集計時に group 内重複として ×0.4 適用済
4. **anomaly_critical を monitor に倒している**: ANO_CPA_SPIKE / ANO_IMPRESSION_DROP は単発スパイクで判断する性質。継続観察が必要なため block ではなく monitor

---

## 9. 次アクション

| # | アクション | 工数 | タイミング |
|---|----------|------|-----------|
| (a) | 5/7 提案資料に本サマリ §5 のフォーマットで挿入 | 0.2d | 5/4-5/6 |
| (b) | pilotton 担当者から AOV / LTV ヒアリング → 偽陽性コスト再計算 | 0.1d | kickoff 時 |
| (c) | CAPI 実装完了後 (Phase B Week 2 想定) に再算出 | 0.2d | 5/14- |
| (d) | monitor 系 16 件の都度判断要請を ChatWork で実行 (ADR-009 §7) | 継続運用 | Phase B Week 2- |

---

## References

- ADR-001: 3 層インパクト表示 (`docs/decisions/ADR-001-three-layer-impact-display.md`)
- ADR-002: 6 root_cause_group 分類 (`docs/decisions/ADR-002-six-root-cause-groups.md`)
- ADR-003: pixel_health 連動 (`docs/decisions/ADR-003-pixel-health-coupling.md`)
- ADR-009 候補: トレードオフ設計 (`docs/architecture/tradeoff_design.md`)
- 検知データ: `outputs/chatwork_state/pilotton_indications.json`
- 重み定義: `config/priority_weights.yaml`
- 算出 JSON: `outputs/pilotton/effective_impact_20260503.json`
