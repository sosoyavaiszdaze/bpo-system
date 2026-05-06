# ADR-016: エントリポイントとモジュール配置の役割整理

- **Status**: Accepted (5/7 R3 整理)
- **Date**: 2026-05-07
- **Related**: ADR-005 (ChatWork 自動通知), ADR-011 (自動通知グループ), ADR-012 (auto_proposal_engine), ADR-013 (5 層ルール)

## 1. Context

これまで本リポジトリには 2 つの「本番エントリポイント候補」が並走していた:

- **`pipeline.py`** (628 行): 起源は v3 PDF レポート / Slack-Lark 通知 / CRM 連携などの手動運用フロー
- **`scripts/daily_chatwork_check.py`** (300+ 行): launchd 9:00/9:15/9:30 で起動する ChatWork 自動通知フロー

外部レビュー時に「どれを叩くのが正か」が曖昧という指摘あり。また `outputs/` ディレクトリにコードと生成物が混在 (`outputs/pdf_report_v3.py` と `outputs/pilotton/...` が同居) しており責務が不明瞭。

## 2. Decision

### 2.1 エントリポイントの正典定義

| エントリ | 用途 | 起動経路 | 主出力 |
|---|---|---|---|
| **`scripts/daily_chatwork_check.py`** | **launchd 自動運用 (本番)** | launchd plist 9:00/9:15/9:30 | ChatWork 通知 (指摘 / 完了 / auto_proposal / AdTruth) |
| **`pipeline.py`** | **手動運用 / 単発レポート生成** | コマンドライン直接実行 | PDF レポート / Slack-Lark 通知 / CRM 同期 |

- 日次の自動運用は **必ず `daily_chatwork_check.py` 経由**。pipeline.py を launchd から直接呼び出さない
- pipeline.py は「単発の深い監査 + PDF レポート生成」に特化。月次提案資料・kickoff・要望対応に使う
- 両者は **`fetch_data` / `run_ads_audit` / `run_anomaly_detection` / `run_fraud_audit` を共有** (pipeline.py が実装、daily_chatwork_check.py が import)

### 2.2 モジュール配置の正典

```
adapters/      : 媒体 API → 統一形式変換 (meta_adapter / google_adapter / tiktok_adapter)
analyzers/     : 監査ロジック (ads_audit / anomaly / fraud_audit / fraud_score / fraud_action / fraud_ingest / segment_waste / adaptive_rule_engine)
engine/        : ロジック / 状態管理 / スコアリング (15+ モジュール)
validators/    : 外部検証 (client_tech_stack_validator)
notifiers/     : 外部通知クライアント (chatwork_notifier / slack_notify / lark_notify / crm_twenty)
templates/     : Jinja2 テンプレート (chatwork / v3 PDF)
seo/           : SEO 監査
integrations/  : 外部システム連携 (scheduler 等)
config/        : YAML ルール / クライアント / 閾値
scripts/       : CLI / launchd / preflight 等のスクリプト
tests/         : pytest テスト
docs/          : ADR / 設計ドキュメント
outputs/       : 生成物のみ (PDF / 状態 / 検証履歴 / 提案サンプル)
```

### 2.3 outputs/ からのコード分離 (R3-#10)

これまで `outputs/` には Python コードと生成物が混在していた。R3 で以下を移動する:

| 旧パス | 新パス | 理由 |
|---|---|---|
| `outputs/pdf_report.py` | `engine/pdf_report.py` | レポート生成は engine の責務 |
| `outputs/pdf_report_v3.py` | `engine/pdf_report_v3.py` | 同上 |
| `outputs/slack_notify.py` | `notifiers/slack_notify.py` | 通知系は notifiers/ |
| `outputs/lark_notify.py` | `notifiers/lark_notify.py` | 同上 |
| `outputs/crm_twenty.py` | `notifiers/crm_twenty.py` | Twenty CRM 連携も外部送信なので notifiers/ |

`outputs/` は **生成物専用ディレクトリ** にする。
import 経路の書換は `pipeline.py` / `analyzers/adaptive_rule_engine.py` / `tests/test_crm_twenty.py` に必要。

### 2.4 outputs/ ディレクトリの最終構造

```
outputs/
├── chatwork_state/          # state (gitignored)
├── client_preferences/      # 運用憲章 / 判断履歴 (git tracked)
├── client_state/            # ADR-015 tech_stack 宣言値 (git tracked)
├── auto_proposal_history/   # state (gitignored)
├── proposals/               # 提案サンプル (git tracked、5/7 提案デモ等)
├── {client_id}/             # クライアント別生成物
│   ├── tech_stack_verification.yaml   # state (gitignored)
│   ├── effective_impact_*.json        # state (gitignored)
│   └── block_events.yaml              # state (gitignored)
└── (PDF / HTML 出力もここに、gitignored)
```

### 2.5 Python バージョン統一 (R3-#11)

ローカル `venv/bin/python3` = 3.9.6、`pyproject.toml.requires-python` = ">=3.9"、`tool.ruff.target-version` = "py312"、CI = 3.11/3.12 と現状バラバラ。

**統一目標**:
- `requires-python` を `">=3.12"` に変更
- ローカル venv を 3.12 で再構築 (将来作業)
- CI を 3.12 のみに絞る
- `tool.ruff.target-version` の py312 と整合

**Phase A 5/7 R3 の対応**:
- pyproject.toml `requires-python` を `">=3.12"` に変更 (宣言)
- venv 再構築は別作業 (launchd plist の python パスは変えないが、`venv/bin/python3` を 3.12 にすれば自動的に切替わる)

## 3. 影響

- 既存 import 経路 9 箇所書換 (pipeline.py 6 / analyzers 1 / tests 2)
- launchd 動作には影響なし (`scripts/daily_chatwork_check.py` の依存は engine/ 経由で完結)
- pyproject.toml の `requires-python` 変更は厳密にチェックされるパッケージング時のみ影響

## 4. 関連ファイル

- `pipeline.py` (役割明示のヘッダーコメント追加)
- `scripts/daily_chatwork_check.py` (launchd エントリと明記)
- `outputs/*.py` 5 ファイル → 移動
- `pyproject.toml` (requires-python 更新)
- `tests/test_crm_twenty.py` (import 修正)
- `analyzers/adaptive_rule_engine.py` (import 修正)
