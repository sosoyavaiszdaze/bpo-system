# オンボーディング質問リスト

新規クライアント導入時に確認する質問と、対応するIntent Override設定。

## Q1-Q5: 基本情報

| # | 質問 | 対応設定 |
|---|---|---|
| Q1 | ビジネスの種類は？ | `industry` フィールド (gaming/finance/ecommerce等) |
| Q2 | KPI・目標数値は？ | `objective`, `target_cpa`, `target_roas` |
| Q3 | 利用媒体は？ | `ads.google`, `ads.meta`, `ads.tiktok` の有効化 |
| Q4 | 対象地域は？ | G20 (地域入札) のoverride検討 |
| Q5 | 月間予算規模は？ | 業界別閾値 `industry_thresholds` の選択 |

## Q6-Q10: 運用方針

| # | 質問 | 対応rule_id | override例 |
|---|---|---|---|
| Q6 | テスト期間中のキャンペーンはあるか？ | C05, C06 | `suppress_action: skip_notification` |
| Q7 | 意図的にSearch Partnersを有効にしているか？ | G42 | `suppress_action: add_context_note` |
| Q8 | 特定地域限定配信の理由は？ | G20, G21 | `suppress_action: add_context_note` |
| Q9 | 学習フェーズ中のキャンペーンは？ | C07, G12 | `suppress_action: skip_notification` |
| Q10 | Manual CPC を意図的に使っているか？ | G11, G11c | `suppress_action: add_context_note` |

## Q11-Q15: クリエイティブ・構造

| # | 質問 | 対応rule_id | override例 |
|---|---|---|---|
| Q11 | 独自の命名規則を使っているか？ | G25 | `suppress_action: add_context_note` |
| Q12 | RSAのピン留めは意図的か？ | G38 | `suppress_action: add_context_note` |
| Q13 | SKAG構造を意図的に維持しているか？ | G39, G40 | `suppress_action: downgrade_severity` |
| Q14 | Meta DCOは意図的に無効化しているか？ | M59 | `suppress_action: add_context_note` |
| Q15 | TikTokクリエイティブの更新頻度は？ | T13, T14 | 更新間隔に応じた閾値調整 |

## Q16-Q21: 計測・不正対策

| # | 質問 | 対応rule_id | override例 |
|---|---|---|---|
| Q16 | Enhanced Conversionsは導入済みか？ | G03 | なし（未導入なら最優先で導入推奨） |
| Q17 | CAPIは実装済みか？ | M02 | なし（未実装なら導入推奨） |
| Q18 | アトリビューションモデルは？ | G08 | DDA以外の場合 `add_context_note` |
| Q19 | 不正検知の許容閾値は？ | F01-F15 | `industry_thresholds` で業界別調整 |
| Q20 | コンバージョン価値は設定済みか？ | G06 | なし（未設定ならvalue-based bidding推奨） |
| Q21 | Consent Mode v2は実装済みか？ | G81 | EU/UK向けの場合のみ必須 |

## Override 生成例

質問Q6で「テスト期間中のキャンペーンあり」の場合:

```yaml
intent_overrides:
  - rule_ids: ["C05", "C06"]
    reason: "テスト期間中のため意図的に低予算設定"
    registered_by: "運用担当"
    registered_at: "2026-04-30"
    expires_at: "2026-07-31"
    suppress_action: "skip_notification"
```

## suppress_action の種類

| アクション | 効果 |
|---|---|
| `skip_notification` | Slack通知から除外（JSONには記録） |
| `downgrade_severity` | severity を1段下げる |
| `add_context_note` | 通知に「意図的設定」の注記を追加 |
