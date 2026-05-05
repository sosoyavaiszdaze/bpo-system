# Meta ルール分類結果（v3.1 Task F-6）

> 生成日時: 2026-05-02
> 対象: `config/rules/meta_rules.yaml` 全 70 ルール
> 6 グループ: measurement_foundation / delivery_learning_or_structure / creative_optimization / budget_allocation / targeting / independent

## 統計サマリ

### グループ別ルール数

| グループ | ルール数 |
|---------|---------:|
| `measurement_foundation` | 9 |
| `delivery_learning_or_structure` | 15 |
| `creative_optimization` | 28 |
| `budget_allocation` | 3 |
| `targeting` | 14 |
| `independent` | 1 |
| **合計** | **70** |

### confidence 分布

| 信頼度 | 件数 |
|-------|------:|
| high (≥0.85) | 54 |
| medium (0.75-0.85) | 0 |
| low (<0.75, needs_review) | 16 |

## needs_review = true のルール一覧（16 件）

これらのルールは複数グループにまたがる可能性があり、人手レビューによる確定が推奨されます。

| ID | 名前 | category | severity | 仮分類 | confidence | 判定理由 |
|----|------|----------|----------|--------|-----------:|---------|
| M11 | CBO vs ABO選択 | 予算_入札 | medium | `delivery_learning_or_structure` | 0.65 | CBO/ABO は予算配分とも捉えられる。budget_allocation との境界判定で needs_review。 |
| M16 | ポリシー違反・不承認 | 構造_設定 | high | `delivery_learning_or_structure` | 0.60 | ポリシー違反は運用衛生で構造設定とは異なるが配信可否に直結するため delivery グループに統合。要再検討。 |
| M17 | アカウント品質スコア | 構造_設定 | high | `targeting` | 0.55 | アカウント品質スコアは結果指標。creative または targeting どちらに属するか議論余地あり（needs_review）。 |
| M18 | 広告アカウント制限リスク | 構造_設定 | critical | `delivery_learning_or_structure` | 0.55 | アカウント制限リスクは事業継続性、構造とは異なる軸。independent の方が妥当な可能性（needs_review）。 |
| M19 | ビジネスマネージャー権限 | 構造_設定 | medium | `targeting` | 0.40 | BM 権限は運用衛生でターゲティングとも独立した IT セキュリティ。independent への変更要検討。 |
| M20 | カタログ連携状態 | 構造_設定 | high | `budget_allocation` | 0.65 | カタログ連携は商品供給=配信源として budget 寄りに分類したが、measurement_foundation 寄りでもある（needs_review）。 |
| M23 | プレースメント手動最適化 | 構造_設定 | medium | `targeting` | 0.65 | プレースメント手動最適化は配信面=ターゲティングに含めたが、creative_optimization 寄りでもある（needs_review）。 |
| M42 | Messenger配信のCV計測 | 計測_トラッキング | medium | `targeting` | 0.60 | Messenger CV計測は計測 vs ターゲットの境界。measurement_foundation の方が妥当な可能性（needs_review）。 |
| M43 | 自動応答フロー | 構造_設定 | medium | `creative_optimization` | 0.55 | 自動応答フローは LP 代替着地点で creative とも targeting ともつかず（needs_review）。 |
| M46 | 既存顧客予算キャップ | クリエイティブ | medium | `creative_optimization` | 0.50 | 既存顧客予算キャップは ASC 内自動化制御。creative より targeting/budget 寄りの可能性（needs_review）。 |
| M52 | 広すぎる/狭すぎる設定 | ターゲティング | high | `budget_allocation` | 0.55 | 広すぎ/狭すぎは粒度判定。targeting の方が直感的（needs_review）。 |
| M55 | 年齢/性別の過剰絞り込み | ターゲティング | medium | `delivery_learning_or_structure` | 0.60 | 年齢/性別の過剰絞り込みは targeting 寄りでもある（needs_review）。 |
| M56 | Aggregated Event Measurement | 計測_トラッキング | medium | `targeting` | 0.55 | AEM は計測欠損対応で measurement_foundation の方が妥当（needs_review）。 |
| M62 | アトリビューション設定 | 計測_トラッキング | high | `measurement_foundation` | 0.70 | アトリビューション設定は計測軸の選択。measurement に統合したが独立扱いも可（needs_review）。 |
| M63 | 配信最適化目標の妥当性 | 構造_設定 | medium | `creative_optimization` | 0.55 | 配信最適化目標は学習対象の選択。delivery_learning_or_structure 寄りでもある（needs_review）。 |
| M64 | 地域ターゲティング精度 | 構造_設定 | medium | `delivery_learning_or_structure` | 0.55 | 地域ターゲティング精度は targeting 寄りだが、構造判定として delivery に分類（needs_review）。 |

## 全ルール分類詳細

<details><summary>全 70 ルールを表示</summary>

| ID | 名前 | category | sev | group | priority | conf | review |
|----|------|----------|-----|-------|---------:|-----:|:------:|
| M20 | カタログ連携状態 | 構造_設定 | high | `budget_allocation` | 1 | 0.65 | ⚠️ |
| M45 | 入札上限の適正 | 予算_入札 | high | `budget_allocation` | 2 | 0.85 |  |
| M52 | 広すぎる/狭すぎる設定 | ターゲティング | high | `budget_allocation` | 3 | 0.55 | ⚠️ |
| M47 | クリエイティブバリエーション | クリエイティブ | high | `creative_optimization` | 1 | 0.95 |  |
| M57 | フリークエンシー疲弊 | クリエイティブ | high | `creative_optimization` | 2 | 0.95 |  |
| M58 | クリエイティブ入替日数 | クリエイティブ | high | `creative_optimization` | 3 | 0.95 |  |
| M59 | Dynamic Creative Optimization | クリエイティブ | high | `creative_optimization` | 4 | 0.85 |  |
| M60 | Advantage+クリエイティブ | 構造_設定 | medium | `creative_optimization` | 5 | 0.85 |  |
| M21 | FBフィードCTR | クリエイティブ | medium | `creative_optimization` | 6 | 0.85 |  |
| M22 | テキスト量過多 | クリエイティブ | medium | `creative_optimization` | 7 | 0.85 |  |
| M24 | 動画/画像の使い分け | クリエイティブ | low | `creative_optimization` | 8 | 0.85 |  |
| M25 | 1:1と4:5のアスペクト比 | クリエイティブ | low | `creative_optimization` | 9 | 0.85 |  |
| M26 | IGフィードのビジュアル品質 | クリエイティブ | medium | `creative_optimization` | 10 | 0.85 |  |
| M27 | エンゲージメント率 | クリエイティブ | medium | `creative_optimization` | 11 | 0.85 |  |
| M28 | UGC風素材の活用 | クリエイティブ | medium | `creative_optimization` | 12 | 0.85 |  |
| M29 | キャプション文字数 | クリエイティブ | low | `creative_optimization` | 13 | 0.85 |  |
| M30 | プロフィール誘導の整合 | クリエイティブ | low | `creative_optimization` | 14 | 0.85 |  |
| M31 | リール動画尺 | クリエイティブ | medium | `creative_optimization` | 15 | 0.85 |  |
| M32 | 縦型9:16フル画面対応 | クリエイティブ | medium | `creative_optimization` | 16 | 0.85 |  |
| M33 | サウンド有無 | クリエイティブ | medium | `creative_optimization` | 17 | 0.85 |  |
| M34 | 動画完了率 | クリエイティブ | medium | `creative_optimization` | 18 | 0.85 |  |
| M35 | UGC/Spark系素材 | クリエイティブ | medium | `creative_optimization` | 19 | 0.85 |  |
| M36 | ストーリーズフルスクリーン活用 | クリエイティブ | medium | `creative_optimization` | 20 | 0.85 |  |
| M37 | スワイプアップ/CTA訴求 | クリエイティブ | low | `creative_optimization` | 21 | 0.85 |  |
| M38 | FBストーリーズのCTR | クリエイティブ | low | `creative_optimization` | 22 | 0.85 |  |
| M41 | AN専用クリエイティブ | クリエイティブ | low | `creative_optimization` | 23 | 0.85 |  |
| M43 | 自動応答フロー | 構造_設定 | medium | `creative_optimization` | 24 | 0.55 | ⚠️ |
| M46 | 既存顧客予算キャップ | クリエイティブ | medium | `creative_optimization` | 25 | 0.50 | ⚠️ |
| M63 | 配信最適化目標の妥当性 | 構造_設定 | medium | `creative_optimization` | 26 | 0.55 | ⚠️ |
| M66 | 広告-LPメッセージ整合スコア | クリエイティブ | high | `creative_optimization` | 27 | 0.85 |  |
| M67 | 勝ち広告LP逆生成プロセス | クリエイティブ | medium | `creative_optimization` | 28 | 0.85 |  |
| M09 | 学習フェーズ脱出率 | 予算_入札 | critical | `delivery_learning_or_structure` | 1 | 0.95 |  |
| M10 | 1広告セットあたりCV数 | 予算_入札 | high | `delivery_learning_or_structure` | 2 | 0.95 |  |
| M13 | 予算変更の頻度 | 予算_入札 | high | `delivery_learning_or_structure` | 3 | 0.95 |  |
| M14 | キャンペーン目的の適正 | 構造_設定 | high | `delivery_learning_or_structure` | 4 | 0.95 |  |
| M12 | 目標費用設定 | 予算_入札 | high | `delivery_learning_or_structure` | 5 | 0.85 |  |
| M68 | 学習リセット要因イベント検出 | 予算_入札 | high | `delivery_learning_or_structure` | 6 | 0.85 |  |
| M11 | CBO vs ABO選択 | 予算_入札 | medium | `delivery_learning_or_structure` | 7 | 0.65 | ⚠️ |
| M15 | 広告セット過多 | 構造_設定 | medium | `delivery_learning_or_structure` | 8 | 0.85 |  |
| M16 | ポリシー違反・不承認 | 構造_設定 | high | `delivery_learning_or_structure` | 9 | 0.60 | ⚠️ |
| M18 | 広告アカウント制限リスク | 構造_設定 | critical | `delivery_learning_or_structure` | 10 | 0.55 | ⚠️ |
| M44 | Advantage+カタログ整合 | 構造_設定 | critical | `delivery_learning_or_structure` | 11 | 0.85 |  |
| M48 | ASC+との共存 | 構造_設定 | medium | `delivery_learning_or_structure` | 12 | 0.85 |  |
| M55 | 年齢/性別の過剰絞り込み | ターゲティング | medium | `delivery_learning_or_structure` | 13 | 0.60 | ⚠️ |
| M64 | 地域ターゲティング精度 | 構造_設定 | medium | `delivery_learning_or_structure` | 14 | 0.55 | ⚠️ |
| M65 | 支払い方法ステータス | 構造_設定 | low | `delivery_learning_or_structure` | 15 | 0.85 |  |
| M49 | オーディエンスオーバーラップ | ターゲティング | critical | `independent` | 1 | 0.85 |  |
| M01 | Pixel発火状態 | 計測_トラッキング | critical | `measurement_foundation` | 1 | 0.95 |  |
| M04 | ドメイン検証 | 計測_トラッキング | critical | `measurement_foundation` | 2 | 0.95 |  |
| M02 | CAPI実装状況 | 計測_トラッキング | critical | `measurement_foundation` | 3 | 0.95 |  |
| M05 | 優先度イベントの設定 | 計測_トラッキング | high | `measurement_foundation` | 4 | 0.95 |  |
| M03 | イベントマッチ品質 | 計測_トラッキング | high | `measurement_foundation` | 5 | 0.95 |  |
| M06 | 重複イベント排除 | 計測_トラッキング | high | `measurement_foundation` | 6 | 0.95 |  |
| M07 | カスタムコンバージョン設定 | 計測_トラッキング | medium | `measurement_foundation` | 7 | 0.95 |  |
| M08 | iOS14+の影響計測 | 計測_トラッキング | high | `measurement_foundation` | 8 | 0.95 |  |
| M62 | アトリビューション設定 | 計測_トラッキング | high | `measurement_foundation` | 9 | 0.70 | ⚠️ |
| M61 | ファーストパーティデータ活用 | ターゲティング | high | `targeting` | 1 | 0.95 |  |
| M50 | LLA(類似オーディエンス)の鮮度 | ターゲティング | medium | `targeting` | 2 | 0.85 |  |
| M51 | カスタムオーディエンス更新頻度 | ターゲティング | medium | `targeting` | 3 | 0.85 |  |
| M53 | 既存顧客除外設定 | ターゲティング | medium | `targeting` | 4 | 0.85 |  |
| M54 | Advantage詳細ターゲット+設定 | ターゲティング | medium | `targeting` | 5 | 0.85 |  |
| M69 | Advantage+文脈の除外オーディエンス | ターゲティング | medium | `targeting` | 6 | 0.85 |  |
| M70 | LLA seed の LTV Top 層集中度 | ターゲティング | medium | `targeting` | 7 | 0.85 |  |
| M39 | AN品質管理 | 構造_設定 | high | `targeting` | 8 | 0.85 |  |
| M40 | ブランドセーフティ除外 | 構造_設定 | medium | `targeting` | 9 | 0.85 |  |
| M17 | アカウント品質スコア | 構造_設定 | high | `targeting` | 10 | 0.55 | ⚠️ |
| M19 | ビジネスマネージャー権限 | 構造_設定 | medium | `targeting` | 11 | 0.40 | ⚠️ |
| M23 | プレースメント手動最適化 | 構造_設定 | medium | `targeting` | 12 | 0.65 | ⚠️ |
| M42 | Messenger配信のCV計測 | 計測_トラッキング | medium | `targeting` | 13 | 0.60 | ⚠️ |
| M56 | Aggregated Event Measurement | 計測_トラッキング | medium | `targeting` | 14 | 0.55 | ⚠️ |

</details>
