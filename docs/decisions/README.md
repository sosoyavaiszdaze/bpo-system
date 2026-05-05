# Architecture Decision Records (ADR)

Zynect Media Agent の重要な設計判断を記録するディレクトリです。各 ADR は **Status / Context / Decision / Alternatives / Result / Tradeoffs / Implementation / References** の構造で記述しています。

## ADR 一覧

| ID | タイトル | Status | 決定日 | 関連 |
|----|---------|:------:|--------|------|
| [ADR-001](./ADR-001-three-layer-impact-display.md) | 想定改善額の3層表示（パターンC）採用 | Accepted | 2026-05-02 | ADR-002, ADR-003 |
| [ADR-002](./ADR-002-six-root-cause-groups.md) | 6 グループ root_cause_group 分類設計 | Accepted | 2026-05-02 | ADR-001, ADR-003 |
| [ADR-003](./ADR-003-pixel-health-coupling.md) | pixel_health 連動ロジック設計 | Accepted | 2026-05-02 | ADR-001, ADR-002 |
| [ADR-004](./ADR-004-cv-normalization-and-conversion-mapping.md) | CV カウント正規化と conversion_mapping.yaml 外部化 | Accepted | 2026-05-03 | ADR-001, ADR-002, ADR-003 |
| [ADR-005](./ADR-005-chatwork-indication-completion-monthly-loop.md) | ChatWork 経由の指摘・完了・月次運用ループ | Accepted | 2026-05-03 | ADR-001, ADR-002, ADR-003, ADR-004 |

## 関連ドキュメント

- **Meta ルール分類結果**: [`./meta_rules_classification.md`](./meta_rules_classification.md) — Meta 70 ルールの 6 グループへの自動分類結果と needs_review=true の 16 件一覧
- **設計文書（v3 全体）**: [`../report_design/`](../report_design/) — 6 ファイル（v3_problem_analysis / v3_structure / v3_content_strategy / v3_terminology_dict / v3_client_config_spec / v3_priority_score_weights）
- **リリースノート**: [`../release_notes/v3.0.md`](../release_notes/v3.0.md)

## ADR の書き方

新規 ADR を追加する際は以下のテンプレートに従ってください:

```markdown
# ADR-XXX: タイトル

| 項目 | 値 |
|------|---|
| **Status** | Proposed | Accepted | Deprecated | Superseded |
| **Decision Date** | YYYY-MM-DD |
| **Authors** | （筆頭著者と関係者） |
| **Related ADRs** | （関連する ADR へのリンク） |

## Context
（背景。なぜこの判断が必要だったか）

## Decision
（決定内容。具体的な値・ロジック・命名等）

## Alternatives Considered
（採用しなかった代替案と却下理由）

## Result
（実装後の結果。数値や挙動の確認）

## Tradeoffs / Risks
（採用案のデメリットや今後のリスク）

## Implementation
（実装ファイルへのリンク）

## References
（参考資料、関連コミット、議事録）
```

## 命名規則

- ファイル名: `ADR-NNN-short-title-with-hyphens.md`（連番、英小文字）
- ADR は基本的に**追記型**で運用。後で「やっぱり違った」となった場合は新 ADR を追加し、旧 ADR の Status を `Superseded by ADR-MMM` に変更する（古い判断の経緯も残す）

## 運用方針

- 重要な技術判断（アーキテクチャ・設計トレードオフ・ライブラリ選定等）は ADR 化を推奨
- 軽微な実装決定（変数名・ローカル定数）は ADR 化不要
- ADR の Decision Date は実装日ではなく**判断確定日**を記録
- ADR は Pull Request レビューと同時にレビューする（実装より先に書くケースもある）

---

*最終更新: 2026-05-02*
