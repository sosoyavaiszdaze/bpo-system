# ADR-004: CV カウント正規化と conversion_mapping.yaml 外部化

| 項目 | 値 |
|------|---|
| **Status** | Accepted |
| **Decision Date** | 2026-05-03 |
| **Authors** | Zynect Media（Day 5.2 事故対応 + Claude Code） |
| **Related ADRs** | [ADR-001](./ADR-001-three-layer-impact-display.md) / [ADR-002](./ADR-002-six-root-cause-groups.md) / [ADR-003](./ADR-003-pixel-health-coupling.md) |

---

## Context

### 事故の経緯（2026-05-02 Day 5.2 で発覚）

PoC 第1号クライアント pilotton（株式会社パイロットン）の v3 レポート再生成中、ブランド別集計（直近30日）と PDF 全体集計（90日 lookback）の数値不整合を調査する過程で、**CV カウントの構造的バグ**が判明した。

#### 修正前の数値（誤り）
| 出力源 | 月次支出 | CV | CPA |
|-------|---------:|---:|----:|
| `pilotton_report_v3.pdf` (lookback 90 日) | ¥3,900,314 | 688 件 | ¥5,669 |
| `pilotton_report_v3.pdf` (lookback 30 日に修正後、CV ロジックは旧) | ¥1,443,021 | 320 件 | **¥4,509** |
| `pilotton_brand_breakdown.md` (level=adset, 30日) | ¥1,415,372 | 477 件 | ¥2,967 |

#### 修正後の数値（真値、Meta API `purchase` 単一カウンタで再集計）
| 出力源 | 月次支出 | CV | CPA |
|-------|---------:|---:|----:|
| `pilotton_report_v3.pdf` (Day 5.2 修正後) | ¥1,446,455 | **161 件** | **¥8,984** |
| `pilotton_brand_breakdown.md` (同修正後) | ¥1,415,372 | 159 件 | ¥8,901 |

**事故の影響**: CPA が `¥4,509 → ¥8,984` と **約 2 倍悪化**（つまり修正前は実態より良く見えていた）。営業時に「業界平均 ¥4,500 比 △34% 優秀」と誤った提案をする寸前で発覚した。

### 根本原因

`adapters/meta_adapter.py:259-274` の CV カウントロジックがハードコードされており、Meta Graph API が**同一の購入を 9 種類超のラベルで重複報告する仕様**を吸収しきれていなかった。

#### Meta API の重複報告仕様（実 API レスポンス例、`act_566972639374407` 主力 ad_set）
同一の 1 件購入が以下 9 通りのラベルで報告される（全て value=103）:
- `purchase` ← unified counter（これだけ採用すべき）
- `omni_purchase`
- `offsite_conversion.fb_pixel_purchase`
- `onsite_web_app_purchase`
- `onsite_web_purchase`
- `web_in_store_purchase`
- `web_app_in_store_purchase`
- `offsite_purchase_add_20_s_calls`
- `offsite_conversion.custom.<custom_event_id>`

これらは Pixel 計測 / CAPI 計測 / iOS14 AEM / Aggregated Events Measurement / カスタム CV 等、**異なる計測経路の同一イベントを別ラベルで重複表示**する Meta API 仕様である。

#### 修正前のハードコード定義（Day 5.2 で削除済み）
```python
# 修正前（Day 5.2 以前）
if action.get("action_type") in [
    "purchase", "offsite_conversion.fb_pixel_purchase",  # ← 同一購入を2回カウント
    "complete_registration", "lead",
]:
    camp["conversions"] += float(action.get("value", 0))
```

この `purchase` と `offsite_conversion.fb_pixel_purchase` の両方を加算することで、CV を **約 2 倍に過大計上** していた。

### 影響範囲

| 媒体 | 状態 | 確定影響 |
|------|------|---------|
| **Meta** | 🔴 確定発覚 | pilotton で CV 320→161（49% 過大計上）、修正済 |
| **Google** | 🟡 潜在リスク | `adapters/google_adapter.py` も類似のハードコード定数で実装。Google Ads API も `conversions` / `all_conversions` / `view_through_conversions` 等の重複表現が存在 |
| **TikTok** | 🟡 潜在リスク | `adapters/tiktok_adapter.py` も同様。TikTok Events API は `complete_payment` / `place_an_order` / `add_to_cart` 等の event_type が階層的で重複が起きやすい |

### なぜ今 ADR で固定化が必要か

1. **BANDAL 本格運用前の予防**: BANDAL（act_916507360736121）が現在配信実績ゼロだが、近日 Google Ads API 連携を開始予定。同種バグが再発する前に構造的対策が必要
2. **新規クライアント受託時の品質保証**: PoC 第1号 pilotton で発覚したバグの**再発防止**を契約上のコミットメントにする
3. **横展開の容易化**: ハードコードのままでは Google/TikTok 各 adapter で同じバグを並行修正する必要があり、レビューコストが増大
4. **設定レビューのガバナンス**: synonym マッピングが設定ファイル化されていれば、運用担当者がコード変更なしで監査・調整可能

---

## Decision

以下 4 点を意思決定として採択する。

### 1. action_type 正規化マッピングを `config/conversion_mapping.yaml` に外部化
全てのコンバージョン系 action_type の synonym 関係を YAML で宣言的に管理する。コードはこの YAML を読み込み、canonical（代表）ラベルに正規化したうえで集計する。

### 2. 全アダプタが共通スキーマで参照
`adapters/meta_adapter.py` / `google_adapter.py` / `tiktok_adapter.py` の 3 ファイルが同じ `conversion_mapping.yaml` を読み、各 `platforms.<media>` セクションを使用する。

### 3. ハードコード定数の廃止
`UNIFIED_CV_TYPES` / `UNIFIED_REVENUE_TYPES` 等のモジュールローカル定数は **完全削除**。コード上に CV 系 action_type 名のリテラルを残さない。

### 4. Pydantic モデルによる型検証
`engine/models.py` に `ConversionMapping` Pydantic モデルを追加し、YAML 読み込み時に必須フィールド・enum 値の検証を自動化する。スキーマ変更は型エラーで即座に検出可能にする。

### `config/conversion_mapping.yaml` のスキーマ案（具体例）

```yaml
version: 1

metadata:
  description: "Multi-platform conversion event normalization map"
  last_updated: 2026-05-03
  related_adr: ADR-004
  notes: |
    各広告媒体の API は同一の購入/リード/登録を複数の action_type ラベルで
    重複報告する仕様がある。本ファイルは synonym → canonical の正規化を
    宣言的に管理し、CV 二重計上を防ぐ。

platforms:
  meta:
    enabled: true
    api_version: "v22.0"
    conversion_types:
      purchase:
        canonical: purchase
        synonyms:
          - purchase
          - offsite_conversion.fb_pixel_purchase
          - onsite_web_app_purchase
          - onsite_web_purchase
          - omni_purchase
          - web_in_store_purchase
          - web_app_in_store_purchase
          - offsite_purchase_add_20_s_calls
        dedup_strategy: max  # synonym 間で最大値のみ採用（同一イベントの重複報告のため）
        notes: "Day 5.2 事故の根本原因。9 種類超のラベルで重複報告される"
      lead:
        canonical: lead
        synonyms:
          - lead
          - offsite_conversion.fb_pixel_lead
        dedup_strategy: max
      complete_registration:
        canonical: complete_registration
        synonyms:
          - complete_registration
          - offsite_conversion.fb_pixel_complete_registration
        dedup_strategy: max
    revenue_types:
      purchase_value:
        canonical: purchase
        synonyms:
          - purchase
        dedup_strategy: max
        notes: "action_values でも同様の dedup が必要"

  google:
    enabled: false  # Phase 2 で調査・実装、現状は空マッピングで動作
    api_version: "v17"
    conversion_types: {}
    revenue_types: {}
    notes: |
      Google Ads conversion_action_category 別 (PURCHASE / LEAD / SIGNUP /
      DOWNLOAD / SUBSCRIBE_PAID 等) の synonym マッピングは別タスクで調査。
      `conversions` / `all_conversions` / `view_through_conversions` の関係も整理要。

  tiktok:
    enabled: false
    api_version: "v1.3"
    conversion_types: {}
    revenue_types: {}
    notes: |
      TikTok Events API の event_type マッピングは別タスクで調査。
      `complete_payment` / `place_an_order` / `view_content` 等の階層関係を整理要。

# === dedup_strategy の選択肢 ===
# max:   synonym 間で最大値を採用（同一イベントの重複報告 → Meta purchase 等）
# sum:   合算（独立した複数イベント → 将来 Custom Event 用）
# first: 最初に出現した synonym のみ採用（特殊用途）
```

---

## Alternatives Considered

| 案 | 内容 | 採用却下理由 |
|---|------|-------------|
| **α** ハードコード継続 | 各 adapter ファイル内で `UNIFIED_CV_TYPES` 等の定数を保持 | Day 5.2 事故の再発リスク。新規 adapter 追加時に同じバグを再生する。設定レビューがコード差分でしか出来ず非運用者には不可視 |
| **β** YAML 外部化 + Pydantic 検証（**採用**） | `config/conversion_mapping.yaml` で宣言的に管理、Pydantic で型検証 | コード修正なしで運用調整可能、Pydantic で破壊的変更を即検知、媒体追加時の影響範囲が YAML diff で明示される |
| **γ** DB 管理（PostgreSQL の専用テーブル） | conversion_mapping を DB テーブル化、admin UI で編集 | PoC 段階では過剰。運用負荷増（DB マイグレーション、バックアップ、認証）。設定変更履歴は git log で十分追える |

---

## Result（採用後の効果と検証）

### 横展開時のバグ再発防止
新規媒体（例: LinkedIn Ads / X Ads）追加時、その媒体の synonym マッピングを YAML に追記するだけで全 adapter の CV カウントが正しくなる。コード変更不要。

### 設定レビューの容易化
synonym 一覧が YAML diff で可視化されるため、運用担当者・本人がレビュー可能（「`omni_purchase` は本当に `purchase` の synonym か?」を判定可能）。

### 媒体追加時の影響範囲予測
`platforms.<media>.enabled` フラグで段階的有効化可能。Google を `enabled: true` にする前に YAML 単体で運用シミュレーション可能。

### 数値整合性確認
- pilotton で **CV 161 件 / CPA ¥8,984**（Day 5.2 修正値）が新ロジックで再現されること
- BANDAL（CV 0 件）で 0 のまま変わらないこと
- yamamoto_demo（CSV ベース）で 124 件のまま変わらないこと（CSV 経路は本 ADR の影響範囲外）

### pilotton 業界平均比較への影響（Day 5.2 で確認済）
| 指標 | 修正前（誤） | 修正後（真値） | beauty_d2c 業界平均 | 評価 |
|------|-----------:|------------:|--------------------:|------|
| CTR | 2.36% | 2.36% | 1.80% | ✅ 業界超え（CV ロジック影響なし） |
| **CPA** | **¥4,509** ❌ | **¥8,984** ✅ | ¥4,500 | 🔴 業界平均の約 2 倍悪化 |
| ROAS | 0.0 | 0.0 | 2.80 倍 | 🔴 計測不能（別問題） |

→ PoC 提案ナラティブを「業界平均超えの優秀運用 → 更に伸ばす」から「**業界平均より悪い CPA を業界平均レベルまで引き戻す**」に転換する根拠となった。

---

## Tradeoffs / Risks

### 採用案（β）のトレードオフ

#### コスト
- **YAML 読み込みの初期化コスト**: pipeline 起動時に 1 回読み込み + Pydantic 検証で約 50ms 程度（実害なし）
- **Pydantic モデル変更時のマイグレーション必要性**: スキーマ変更時に `version: 1 → 2` のマイグレーション手順を整備する必要がある。これは ADR-004 内では扱わず、初回スキーマ変更時に migration helper を別途実装

#### リスクと緩和策
| リスク | 緩和策 |
|--------|-------|
| YAML の typo / 不正値 | Pydantic 型検証で起動時に即エラー、CI の test_conversion_mapping.py で全 synonym 形式検証 |
| 媒体 API 仕様変更で synonym 増 | `Phase 2 で四半期レビュー` 運用ルール化、追加時は ADR-004 の Result セクションに追記 |
| 既存レポートの数値が変わる | Day 5.2 で既に修正済（旧 PDF が誤）、過去レポート再生成は Migration Path で扱う |

### 「裏側の品質前提として顧客には可視化しない」方針

本 ADR が固定化する CV 正規化は、**広告監査ツールとしての最低品質基準**であり、顧客向けの「価値提案」ではない。PoC 提案資料（pptx）では:
- ❌ 「Zynect は CV 二重計上を補正します」とは書かない（他社批判の印象を与える、当然の品質基準のため）
- ✅ 「業界仕様に基づく計測精度保証」として裏側ドキュメント（ホワイトペーパー / 技術 blog）で言及する

この判断は、PoC 提案資料の "価値提案 7 項目" には含めず、"裏側の品質前提" として位置づける（→ Day 5.3 提案戦略確定の文脈）。

---

## Implementation

### 修正対象（Phase 1 = 本 ADR 範囲）

#### 1. 新規追加ファイル
- `config/conversion_mapping.yaml` — Decision セクションのスキーマ案を採用
- `engine/models.py` — `ConversionMapping`, `PlatformMapping`, `ConversionType` 等の Pydantic モデル

#### 2. 修正対象
- `adapters/meta_adapter.py:259-276` — `UNIFIED_CV_TYPES / UNIFIED_REVENUE_TYPES` 削除、conversion_mapping.yaml 参照に置換
- `adapters/google_adapter.py` — 同様の変更（現状の synonym 定義を YAML に移行、ただし enabled: false で空マッピング開始）
- `adapters/tiktok_adapter.py` — 同上

#### 3. テスト追加
- `tests/test_conversion_mapping.py`（新規）に以下 3 ケース:
  - **(a) Meta synonym dedup の単体テスト**: ダミー API レスポンス（`purchase: 100, offsite_conversion.fb_pixel_purchase: 100, onsite_web_app_purchase: 100`）で正規化後 100 になることを確認
  - **(b) pilotton 真値の回帰テスト**: 実 API レスポンス snapshot を fixture 化し、CV 161 / CPA ¥8,984 が再現されることを確認
  - **(c) YAML スキーマ検証テスト**: 不正な dedup_strategy（例: `dedup_strategy: invalid`）で Pydantic 例外が発生することを確認

### Google / TikTok アダプタへの適用

本 ADR では **Meta のみ実装完了**とし、Google / TikTok は YAML スキーマだけ用意して `enabled: false` の空マッピングで開始する。各媒体の synonym 調査は **Out of Scope**（後続 ADR または別タスクで扱う）。

理由:
- Google Ads API の conversion_action_category は深い調査が必要（コンバージョン値の `conversions` vs `all_conversions` の関係等）
- TikTok Events API の event_type 階層は公式ドキュメント情報が限定的
- pilotton は Meta のみのため、Google/TikTok の bug fix は緊急度が低い
- BANDAL の Google API 連携開始時に別 ADR で扱う方が判断材料が揃う

---

## Migration Path

### 既存 v2/v3 レポート生成への影響

#### 影響あり
- `reports/2026-05-02/pilotton_report_v3.pdf` の最新版（Day 5.2 14:34 生成）は既に修正後ロジックで生成済 → 影響なし
- `reports/2026-05-02/pilotton_brand_breakdown.md` も Day 5.2 修正後に再生成済 → 影響なし

#### 影響あり（過去日付の誤レポート）
- `reports/2026-04-XX/` 以前の pilotton レポート: CV 二重計上のまま生成された誤データ → **再生成不要**（既に顧客提示前で良かった）
- ただし内部参照されている可能性のあるレポートには「**ADR-004 適用前のデータ、参考値**」のフッター注記を追加することを推奨

### 数値整合性確認手順

ADR-004 実装後の検証手順:

1. **pilotton 再生成テスト**
   ```bash
   python pipeline.py run pilotton --report-version v3
   ```
   期待値: CV ≒ 161 / CPA ≒ ¥8,984 / 月次支出 ≒ ¥1,446,455（API 取得タイミングで ±2% 程度の変動許容）

2. **BANDAL 回帰テスト**
   ```bash
   python pipeline.py run bandal_gaming --report-version v3
   ```
   期待値: CV 0 / CPA ¥0（配信実績ゼロのため変化なし）

3. **yamamoto_demo 回帰テスト**
   ```bash
   python pipeline.py run yamamoto_demo --report-version v3
   ```
   期待値: CV 124 / CPA ¥6,048（CSV ベース、本 ADR の影響対象外）

4. **pytest 全件パス**
   ```bash
   python -m pytest tests/ -v
   ```
   期待値: 既存 21 ケース + 新規 3 ケース = 24 ケース全件パス

### ロールバック手順

万一実装に問題があった場合:

1. `git revert <commit_hash>` で実装変更を巻き戻す
2. `adapters/meta_adapter.py.bak.before_cv_dedup.20260502-143351`（Day 5.2 修正前のバックアップ）に手動復元する場合は **CV 二重計上が再発する**ことを認識のうえで実施
3. `config/conversion_mapping.yaml` を削除し、各 adapter のハードコード定数を一時的に復活させる（緊急対応）

---

## Out of Scope

本 ADR では **意図的に** 以下を扱わない。各々は将来別 ADR または別タスクで対処する:

| 項目 | 想定 ADR / タスク | 理由 |
|------|------------------|------|
| **AdTruth SDK 実接続** | 将来 ADR | 現状 `analyzers/fraud_ingest.py:_fetch_adtruth()` はスタブ。実接続は Fraud 被害が顕在化するクライアント受託時に判断 |
| **日次データ品質チェック自動化** | ADR-005 候補 | `scripts/daily_data_quality_check.py` 新設予定。CV 異常値・支出前日比・Pixel 健全性を毎日 Slack 通知 |
| **ad-truth と bpo-system の責務境界明文化** | 将来 ADR | AdTruth SDK 実接続時に併せて整理 |
| **Twenty CRM 連携** | Phase 2 | 既に部分実装あり、本 ADR の対象外 |
| **Google / TikTok の synonym 調査** | 別タスク（BANDAL Google API 連携時） | YAML スキーマだけ用意、実マッピングは別 ADR |
| **Custom Conversion Events 対応** | 将来 ADR | `offsite_conversion.custom.<event_id>` 等のカスタム CV は現状 synonym として一律無視。専用ロジック検討は将来 |

---

## References

- **事故対応ログ**: 会話履歴 2026-05-02（Day 5.2）の CV 二重計上発見と修正経緯
- **修正コミット**: `adapters/meta_adapter.py.bak.before_cv_dedup.20260502-143351` を比較対象
- **API 仕様**: Meta Marketing API Insights — Action Types ([参考](https://developers.facebook.com/docs/marketing-api/insights/parameters))
- **関連 ADR**:
  - [ADR-001 想定改善額の3層表示](./ADR-001-three-layer-impact-display.md) — 修正後の真値で 3 層計算が現実的になる
  - [ADR-002 6グループ分類](./ADR-002-six-root-cause-groups.md) — measurement_foundation グループの内部整合性向上
  - [ADR-003 pixel_health 連動](./ADR-003-pixel-health-coupling.md) — CV 真値が確定したことで pixel 連動の効果計算も正確化
- **検証データ**: `reports/2026-05-02/pilotton_results.json`（Day 5.2 修正後の真値）
