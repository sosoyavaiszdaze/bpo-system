# ECフォース API 連携 技術調査メモ (Phase B 着手前)

| 項目 | 値 |
|------|---|
| 作成日 | 2026-05-04 |
| 対象クライアント | パイロットン (pilotton)、industry=beauty_d2c |
| 背景 | 5/3 ChatWork 確認で「Shopify ではなく **ECフォース (ec-force.com)** 利用」が判明 |
| 影響範囲 | ChatWork テンプレート / 依頼リスト / 提案資料 / Phase B コード実装 |
| 関連 ADR | ADR-005 (ChatWork ループ), ADR-009 (トレードオフ設計, ADR-006 候補, AdTruth) |

---

## 1. ECフォース 提供機能サマリ (5/3 山本さん共有事実)

| カテゴリ | 機能 | 公式 API / 管理画面 |
|---------|------|--------------------|
| **データ連携** | 広告集計・顧客・受注・商品・配送・在庫の API 連携を公式提供 | API |
| **Meta CAPI** | **管理画面に「FB CV API」タブ標準搭載** (外部連携アカウント管理 配下) | 管理画面 |
| **権限管理** | **ecforce accounts** でスタッフメンバー権限管理 | 管理画面 |
| **データ出力** | 受注・顧客データの **CSV エクスポート** 機能 | 管理画面 |
| **分析** | **ecforce bi** 標準分析 (AOV / LTV / リピート率 / 継続率) | 管理画面 |
| **AI 機能** | 2025 年に **AI 自動 LTV 予測機能** 実装済 | 管理画面 |

---

## 2. Meta CAPI 公式連携機能 (FB CV API タブ)

### 設定経路 (ECフォース管理画面)

```
ECフォース管理画面ログイン
  → 「外部連携アカウント管理」メニュー
    → 「FB CV API」タブ
      → Meta Pixel ID 入力
      → Meta Business Manager で発行したアクセストークン入力
      → テストイベントで疎通確認
      → 本番有効化
```

### Zynect 側で提供する依頼内容

1. Meta Business Manager (BM) 上で **CAPI 用アクセストークン発行** (Zynect 主導)
2. 発行トークンを pilotton 担当者へ ChatWork で安全に共有 (or 管理画面の閲覧権限経由で直接設定)
3. ECフォース「FB CV API」タブで設定実行 (pilotton 担当者、Zynect 伴走支援)
4. Meta Events Manager の **「テストイベント」タブ** で Pixel + CAPI 両方のイベント受信確認
5. 48 時間後 EMQ (Event Match Quality) スコア 6.0 以上を確認

### Pixel との dedup_key (重複排除)

- ECフォース は CAPI 送信時に **event_id を自動付与** (Pixel と同一値が共有される構造)
- 結果: Meta Events Manager で「重複排除済み」ラベルが自動表示
- 実装側で event_id 連携処理は **不要** (= Day 5.2 の CV 二重計上問題は ECフォース 側で発生しない)

→ **ADR-004 (CV 正規化 + conversion_mapping.yaml) は ECフォース 連携時にも必須**:
ECフォース 自体は dedup OK だが、Meta API 取得側 (Zynect の `adapters/meta_adapter.py`) で同義語ラベル (purchase / offsite_conversion.fb_pixel_purchase 等) の二重計上対策が引き続き必要。

---

## 3. 想定される API エンドポイント (要 ECフォース ドキュメント精査)

> 注: 以下は一般的な EC プラットフォーム API 構造からの推定。Phase B Week 1 で **ECフォース 公式 API ドキュメントを取得 → 実エンドポイント確定** が必要。

### 受注データ (Orders)

| メソッド | エンドポイント (推定) | 用途 |
|---------|---------------------|------|
| GET | `/api/v1/orders` (期間/ステータス フィルタ) | LTV / リピート率算出のソース |
| GET | `/api/v1/orders/{id}` | 個別注文詳細 (CV 補正用) |
| GET | `/api/v1/orders/{id}/line_items` | AOV 算出 |

### 顧客データ (Customers)

| メソッド | エンドポイント (推定) | 用途 |
|---------|---------------------|------|
| GET | `/api/v1/customers` | Customer Audience の Source、Lookalike Seed |
| GET | `/api/v1/customers/{id}/orders` | 個別 LTV 推定 |

### 商品データ (Products)

| メソッド | エンドポイント (推定) | 用途 |
|---------|---------------------|------|
| GET | `/api/v1/products` | カタログ広告 / Advantage+ ショッピング連携 |

### 広告集計 (Ad Analytics)

| メソッド | エンドポイント (推定) | 用途 |
|---------|---------------------|------|
| GET | `/api/v1/analytics/sales` | チャネル別売上 (CPA 補正データ) |
| GET | `/api/v1/analytics/cohorts` | リピート率 / 継続率 (ecforce bi 連動) |

→ **Phase B Week 1 タスク**: 公式 API ドキュメントを取得し、本表を実値で更新。

---

## 4. ecforce bi の AOV / LTV 自動取得経路

### 現状 (5/4 時点)

- pilotton の AOV / LTV は **手動ヒアリング前提** (ADR-009 §6.3 で `clients.yaml.economics:` に仮値 ¥15,000 / 倍率 3.0 を仮置き済)
- ECフォース bi で**実値が自動算出済み**であることが判明 → ヒアリング不要、API 取得で代替可能

### Phase B Week 1-2 で実装する取得経路 (案)

```python
# adapters/ecforce_adapter.py (新規) のスケルトン
class ECForceAdapter:
    def fetch_economics(self, client_cfg) -> dict:
        """ecforce bi から AOV / LTV / リピート率を取得して clients.yaml.economics 形式で返す

        Returns:
            {
                "aov_jpy": int,
                "ltv_jpy": int,
                "ltv_aov_ratio": float,        # = ltv / aov
                "repeat_rate_pct": float,       # ecforce bi のリピート率
                "retention_rate_pct": float,    # 継続率 (90/180/365 day)
                "ltv_prediction_jpy": int,      # AI 自動 LTV 予測 (2025 機能)
                "data_source": "ecforce_bi",
                "fetched_at": ISO timestamp,
            }
        """
        # 実装は Phase B Week 1-2
        pass
```

→ ADR-009 §6 の偽陽性コスト計算式にこの実値を流し込めば、pilotton 専用の精度高い NetBenefit が算出可能。

---

## 5. 必要権限 (ECフォース スタッフアカウント)

5/7 提案後、pilotton 担当者にお願いする閲覧権限の最小セット:

| 権限カテゴリ | 必要レベル | 用途 |
|------------|-----------|------|
| 受注 | 閲覧 | Phase B AOV / LTV 集計、CV 補正 |
| 顧客 | 閲覧 | Customer Audience の同意ベース連携 (M61 関連) |
| 商品 | 閲覧 | カタログ広告の整合性確認 (将来) |
| **外部連携アカウント管理 (FB CV API)** | **閲覧 + 設定変更** | **CAPI 設定の確認・切替 (M02 ローンチに必須)** |
| ecforce bi | 閲覧 | AOV / LTV / リピート率の手動取得 (API 連携前の暫定) |
| 管理者権限 | **不要** | スタッフ専用権限で十分 |

---

## 6. Zynect 側で必要な実装変更箇所と工数見積もり

| 区分 | ファイル / 機能 | 工数 | Phase |
|------|---------------|------|-------|
| **ChatWork テンプレート** | `templates/chatwork/_action_steps.md.j2` の方法B 修正 | ✅ 完了 (本タスク) | (本タスク) |
| **ChatWork 免責文** | `templates/chatwork/_disclaimer_ai_assist.md.j2` | ✅ 完了 (本タスク) | (本タスク) |
| **テスト** | `tests/test_chatwork_templates.py` Shopify→ECフォース 4 箇所 | ✅ 完了 (本タスク) | (本タスク) |
| **新規 adapter** | `adapters/ecforce_adapter.py` 新規実装 (受注/顧客/商品/広告集計 4 メソッド) | 1.5d | Phase B W1 |
| **economics 連携** | `clients.yaml.economics` を ecforce_adapter から自動上書き | 0.3d | Phase B W1 |
| **ADR-006 拡張** | AdTruth ADR に「ECフォース 顧客は LP タグ前に CAPI 公式機能優先」と明記 | 0.2d | Phase B W2 |
| **ADR-009 §6.2 再算出** | pilotton の AOV / LTV を実値に更新 → 偽陽性コスト再算出 | 0.2d | Phase B W1 |
| **依頼リスト 9 項目目** | ChatWork 投稿用フォーマット (本タスクで提供) | ✅ 完了 (本タスク) | (本タスク) |
| **API ドキュメント精査** | ECフォース 公式 API ドキュメントから実エンドポイント確定 | 0.5d | Phase B W1 |
| **テスト追加** | `tests/test_ecforce_adapter.py` mock 6-8 件 | 0.5d | Phase B W1 |
| **総 Phase B 追加工数** | | **約 3.2d** | Phase B Week 1-2 |

---

## 7. ADR-006 (AdTruth LP タグ) との関係

ECフォース 公式の Meta CAPI 連携が利用可能 → **LP タグ実装の優先度が下がる**:

| 検討項目 | Shopify 想定時 | **ECフォース 想定時** |
|----------|--------------|------------------|
| CAPI 実装難易度 | 中 (Shopify アプリ + 設定) | **低 (FB CV API タブで完結)** |
| 自社サーバ構築 | 不要 | **不要** |
| AdTruth LP タグの優先度 | 高 (CAPI 補完が必要) | **中** (CAPI で計測精度高まれば LP タグの追加価値が相対的に減) |
| 偽陽性コスト試算 (ADR-009) | LTV 仮値 ¥45,000 | **ecforce bi 実値で再算出** |

→ AdTruth (ADR-006) のスコープは Phase B Week 3-4 で再検討、もし ECフォース CAPI で計測精度が業界平均超を達成できる場合は **LP タグ導入を後ろ倒し** することも選択肢。

---

## 8. 5/7 提案後のロードマップ更新

| 週 | 旧計画 (Shopify 想定) | 新計画 (ECフォース 実態) |
|---|------------------------|------------------------|
| W1 (5/14-5/17) | 内部レビュー終了 / pilotton kickoff / ops_alert 実装 | ↑ + **ECフォース API 調査 + ecforce_adapter スケルトン** |
| W2 (5/20-5/24) | トレードオフ設計実装 (ADR-009) | ↑ + **ecforce_adapter 実装 + AOV/LTV 自動取得** |
| W3 (5/27-5/31) | AdTruth LP タグ調査 → ADR-006 化 → MVP 実装 | **AdTruth 優先度再評価** (ECフォース CAPI で代替可能か) |
| W4 (6/3-6/7) | 月次レポート初回送信 / Phase A 振り返り | ↑ そのまま |

---

## 9. 参考リンク

- ECフォース 公式: <https://ec-force.com>
- Meta CAPI 公式ドキュメント: <https://developers.facebook.com/docs/marketing-api/conversions-api/>
- 関連 ADR:
  - [ADR-004 CV 正規化](../decisions/ADR-004-cv-normalization-and-conversion-mapping.md)
  - [ADR-005 ChatWork ループ](../decisions/ADR-005-chatwork-indication-completion-monthly-loop.md)
  - [ADR-009 候補 トレードオフ設計](../architecture/tradeoff_design.md)
- 関連修正:
  - `templates/chatwork/_action_steps.md.j2` (方法B Shopify→ECフォース)
  - `templates/chatwork/_disclaimer_ai_assist.md.j2`
  - `tests/test_chatwork_templates.py` (4 ケース更新)
  - `scripts/chatwork_smoke_test.py` (コメントのみ)

---

## 10. ECフォース 管理画面で CAPI を設定する手順 (山本さん向けクイックリファレンス)

1. **ECフォース 管理画面にログイン** (pilotton 担当者の発行する閲覧+設定変更権限のスタッフアカウント)
2. **左メニュー → 「外部連携アカウント管理」** をクリック
3. 上部タブから **「FB CV API」** を選択
4. 入力欄:
   - **Meta Pixel ID**: pilotton の Meta 広告アカウント (act_566972639374407) に紐付く Pixel ID
   - **Meta アクセストークン**: Meta Business Manager → イベントマネージャ → 該当 Pixel → 設定 → 「コンバージョン API」セクション → 「アクセストークンを生成」で発行
5. **テストイベント** ボタンで Meta Events Manager に届くか疎通確認
6. **保存** → 本番有効化
7. **48 時間後** に Meta Events Manager の「データの質」セクションで EMQ スコア 6.0 以上を確認 (M03 解消条件)

→ Pixel と CAPI の両方からイベントが届くと dedup ラベル「重複排除済み」が自動表示される。
