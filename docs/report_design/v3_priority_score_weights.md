# v3 — priority_score 重み案 (3パターン)

> **Day 4 で人間が選定するための候補案**
> **作成日**: 2026-05-01
> **読者**: プロダクトオーナー / 営業責任者
> **意思決定者**: Day 4 で1パターンを選定し、`analyzers/priority_ranker.py` に反映

---

## 共通スコアリング式

```
priority_score = (severity_weight × estimated_impact_yen × confidence_weight) / max(effort_hours, 1)
```

3つの係数（severity_weight / confidence_weight / effort_normalization）の調整で、優先順位の傾向を変えられる。

---

## パターンA: severity重視（**仮デフォルト**）

「critical を最優先で並べる。重大度の差を最大化する」

```yaml
severity_weights:
  critical: 5.0
  high: 3.0
  medium: 1.5
  low: 0.5

confidence_weights:
  high: 1.0
  medium: 0.7
  low: 0.4

effort_normalization: linear  # /max(effort_hours, 1)
```

**特徴**:
- critical/high が必ず Top5 上位に来る
- 業界一般的な「重大な問題から潰す」ロジック
- 想定効果が小さくても重大度で押し上げられる

**向いている顧客**: 初回監査・健康診断目的のクライアント、リスク回避志向

---

## パターンB: confidence重視

「想定効果の確度が高いものから優先。ハズれが少ない打ち手を上位化」

```yaml
severity_weights:
  critical: 3.0
  high: 2.5
  medium: 1.5
  low: 0.5

confidence_weights:
  high: 1.5      # 高確度を強く押し上げ
  medium: 0.6
  low: 0.2       # 低確度は大幅減点

effort_normalization: linear
```

**特徴**:
- 確度 high のルールが上位に集まる
- 営業として「やればこれだけ改善する」と断言できる施策が並ぶ
- 確度 low の野心的提案は下位に押される

**向いている顧客**: 提案物の確実性を重視するクライアント、PoC段階のクライアント

---

## パターンC: effort重視（クイックウィン優先）

「小工数で大インパクトの施策を Top5 に集める。実行されやすさ重視」

```yaml
severity_weights:
  critical: 4.0
  high: 2.5
  medium: 1.5
  low: 0.5

confidence_weights:
  high: 1.0
  medium: 0.7
  low: 0.4

effort_normalization: square_root  # /sqrt(max(effort_hours, 1))
# = 工数が大きいときの分母増加を緩和し、小工数施策を強調
```

**追加重み**:
```yaml
quick_win_bonus: 1.5    # quick_win=true なら priority_score を 1.5倍
```

**特徴**:
- quick_win=true のルールが優先される（効果は出やすい・実装コストが小さい）
- 大工数だが効果大の施策（例: LP改修、CR量産プロセス確立）は Top5 から外れやすい
- 実行に移されやすく、顧客の早期成功体験につながる

**向いている顧客**: 運用代行新規受託直後、信頼関係構築期、短期成果が必要な状況

---

## 比較サマリ

| 項目 | A: severity | B: confidence | C: effort |
|------|------------|--------------|-----------|
| critical/high が上位に来る | ◎ | ○ | ◎（quick_winでさらに） |
| 確度 high が上位 | △ | ◎ | ○ |
| quick_win が上位 | △ | △ | ◎ |
| 提案の網羅性 | ◎ | △（low が大幅減点） | △（大工数施策が落ちる） |
| 営業時の断言度 | △ | ◎ | ○ |
| 顧客の早期成功体験 | △ | ○ | ◎ |

---

## Day 4 選定の観点

以下の観点で選定する:

1. **クライアント傾向**: 監査対象の多くが新規受託か既存運用改善か
2. **営業フェーズ**: 信頼関係構築期 → C / 拡張提案期 → A or B
3. **担当者属性**: 経営層が読む → A（重大度ベースで分かりやすい）/ 運用層が読む → C
4. **試運用データ**: yamamoto_demo の実データで3パターン適用してみて、Top5の妥当性を肉眼確認

---

## 追加の調整候補（Day 4 以降）

- **quick_win_bonus 値**: 1.0（無効）/ 1.3 / 1.5 / 2.0 で挙動を観察
- **principle_bonus**: 米満氏理論との接続が強いルール（P1, P2 直系等）に追加重み
- **client_objective bonus**: clients.yaml の `objective` (cpa_minimize / roas_target / cv_maximize) に応じた指標重視
- **conflict_group penalty**: 同じ conflict_group 内で複数ルール検出時の競合解決
