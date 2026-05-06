# ADR-017: Phase B プラットフォーム進化計画

- **Status**: Proposed (Phase A 5/7 終盤で起草、Phase B Week 2-3 で順次実装)
- **Date**: 2026-05-07
- **Related**: ADR-013 (5 層ルール), ADR-014 (fraud_score), ADR-015 (MarTech), ADR-016 (Entry Points), ADR-009 (二層判断)

## 1. Context

Phase A (5/7) 完了時点でリポジトリの規模が拡大し、外部レビューで以下のスケーラビリティ課題が指摘された:

1. `engine/auto_proposal_engine.py` 635 行など巨大モジュールが増えている
2. YAML ルールの `eval()` が運用上の地雷になり得る (preflight で当面緩和済、根本対策は別)
3. `chatwork_sent.json` ベースの idempotency / state 管理は file lock で当面対応済だが、複数クライアント運用で限界
4. ローカル venv が Python 3.9 のままで、pyproject.toml の py312 と乖離 (R3 で宣言は更新済、venv 再構築は別作業)

Phase B (5/8 〜 5/19 内部レビュー / それ以降の本格運用) でこれらに段階的対応する設計を本 ADR に集約する。

## 2. Decision

Phase B では以下の 4 つの構造改善を順次実施する。各項目は独立した PR で進められる単位に分解。

### 2.1 auto_proposal_engine 分割 (B-1)

**現状**: `engine/auto_proposal_engine.py` 635 行に loader / matcher / evaluator / dispatcher / history が同居。

**分割案**:
```
engine/auto_proposal/
├── __init__.py            # 公開 API: run_auto_proposal()
├── loader.py              # _load_all_layers, ルール YAML ロード
├── environment_filter.py  # _filter_by_environment, applies_to + tech_stack マッチング
├── trigger_eval.py        # _evaluate_trigger, _evaluate_skip_if, _DotDict
├── prerequisite.py        # _check_prerequisite_chain
├── cooldown.py            # _check_cooldown
├── caps.py                # _enforce_caps, DEFAULT_CAPS
├── priority.py            # _apply_severity_priority
├── render_dispatch.py     # _render_and_post (テンプレ render + ChatWork 投稿)
└── history.py             # _load_history, _save_history, _update_history
```

**移行手順**:
1. 既存テスト `tests/test_rule_loader_multi_layer.py` を全件 PASS で維持
2. ファイル単位で 1 PR = 1 モジュール抽出
3. 各 PR で全 457+ テスト 通過確認
4. 公開 API (`run_auto_proposal`) のシグネチャは維持

**完了条件**:
- 各サブモジュール 200 行以下
- 既存テスト 100% 維持 + サブモジュール単位の単体テスト追加

### 2.2 ルール DSL / JSONLogic 移行 (B-2)

**現状**: `trigger.condition` が Python eval で評価される。`__builtins__` は閉じているが、構文ミスが debug ログで握り潰される問題が残る (preflight で起動前検証は導入済)。

**移行案**:
- 第 1 段階: 既存 condition 文字列を **JSONLogic** AST に変換するアダプタを書く
- 第 2 段階: 新規ルールは JSONLogic ネイティブで記述
- 第 3 段階: 全ルール JSONLogic 化、Python eval 経路を撤廃

**JSONLogic 例** (現行 → 新):
```yaml
# 現行 (Python eval)
trigger:
  condition: "client_state.cv_dedupe_key_implemented == False and ad_platform_data.capi_emq_score < 6"

# 新 (JSONLogic)
trigger:
  logic:
    "and":
      - { "==": [{ "var": "client_state.cv_dedupe_key_implemented" }, false] }
      - { "<":  [{ "var": "ad_platform_data.capi_emq_score" }, 6] }
```

**メリット**:
- 構文ミスを起動時に確実に検出 (eval 例外がブラックボックス化されない)
- Python ランタイム外 (TypeScript / Go) でも同じルール評価が可能
- テスト・デバッグ容易性の向上

**実装規模**:
- アダプタ層 ~200 行
- 全 525 ルール (Layer A 277 + Layer 0-3 248) の logic 化スクリプト ~1 日
- 既存 eval 経路は Phase B 終盤 (5/19+) まで両立稼働

### 2.3 SQLite 移行 (B-3)

**現状**: 状態管理が複数の JSON / YAML ファイルに分散:
- `state/chatwork_sent.json` (sha256 idempotency)
- `outputs/chatwork_state/{client}_indications.json` (指摘の状態遷移)
- `outputs/auto_proposal_history/{client}.yaml` (auto_proposal 投稿履歴)
- `outputs/{client}/block_events.yaml` (ブロック履歴)
- `outputs/{client}/tech_stack_verification.yaml` (検証履歴)
- `outputs/client_preferences/{client}_decisions.yaml` (顧客判断ログ)

**SQLite 統合案** (`state/zynect.db`):
```sql
CREATE TABLE chatwork_sent (
  idempotency_key TEXT PRIMARY KEY,
  room_id TEXT,
  message_id TEXT,
  ts INTEGER,
  client_id TEXT
);
CREATE TABLE indications (
  indication_id TEXT PRIMARY KEY,
  client_id TEXT,
  rule_id TEXT,
  status TEXT,
  first_detected_at INTEGER,
  last_detected_at INTEGER,
  payload JSON
);
CREATE TABLE auto_proposal_history (...);
CREATE TABLE block_events (...);
CREATE TABLE tech_stack_verification (...);
CREATE TABLE customer_decisions (...);
```

**メリット**:
- ACID トランザクション (file lock 不要)
- クライアント数増加時のスケール (現 1 → 5+ で JSON では限界)
- SQL クエリで横断分析 (月次レポート / 監査ログ抽出)
- バックアップが 1 ファイル (`zynect.db`)

**移行手順**:
1. SQLite スキーマ設計 + マイグレーションスクリプト
2. 既存 JSON / YAML を SQLite に一括 import
3. リーダ層 (`engine/state_store.py` 等) を導入、JSON / SQLite 両対応
4. ライタ層も SQLite 化、JSON 互換を維持
5. 1 週間並行稼働後、JSON 経路撤廃

**実装規模**: ~3 日。但し移行前に Phase A 安定運用 (1-2 週間の連続成功) を確認してから着手。

### 2.4 venv の Python 3.12 再構築 (B-4)

**現状**: ローカル venv = 3.9.6、pyproject 宣言 = 3.12 (R3 で更新済)、CI = 3.11/3.12 (一部)。

**手順**:
```bash
deactivate
rm -rf venv
/usr/bin/python3.12 -m venv venv  # or pyenv 3.12.x
venv/bin/pip install -r requirements.lock  # 既に R1 で生成済の lock を使用
venv/bin/python3 -m pytest tests/ -q   # 全件 PASS を確認
```

**launchd plist へのインパクト**: ProgramArguments[0] が `venv/bin/python3` のため、再構築後も同じパスで自動的に 3.12 系で動く (実行ファイル名は変わらず)。

**完了条件**:
- `venv/bin/python3 --version` が `Python 3.12.x`
- pytest 全件 PASS
- launchd 自動実行 1 サイクル成功

### 2.5 構造化ログの全面活用 (B-5)

R4a で `engine/structured_logger.py` を導入済。Phase B では:
- 各ステップ (`audit_fetch` / `detect_upsert` / `auto_proposal_eval` / `adtruth_check` 等) に細粒度 context を注入
- `set_status("done")` / `"failed")` / `"skipped"` を明示的に記録
- 監視ダッシュボード (Grafana / Datadog 等) に json log を流し込み

## 3. ロードマップ (Phase B Week 番号は内部レビュー期間 5/8〜 を Week 1 とする)

| Week | 内容 |
|---|---|
| **Week 1** (5/8〜5/14) | Phase A 安定運用確認、5/7 ADR 群の補遺修正 |
| **Week 2** (5/15〜5/21) | B-1 (auto_proposal 分割) + B-4 (venv 再構築) |
| **Week 3** (5/22〜5/28) | B-3 (SQLite 移行) 一次実装 |
| **Week 4** (5/29〜6/4) | B-2 (JSONLogic アダプタ) + B-5 (構造化ログ細粒度化) |
| **Week 5+** (6/5〜) | AdTruth Meta 実 API 接続、Google/TikTok 拡張、papa-torb 取込 |

## 4. リスクと対策

| リスク | 対策 |
|---|---|
| auto_proposal 分割で既存挙動を壊す | git mv + bulk import 修正 + 全テスト通過確認を 1 PR ごとに |
| JSONLogic 移行で評価セマンティクスのズレ | 既存 eval 経路と並行稼働、出力差分を 1 週間モニタしてから切替 |
| SQLite 移行でデータロス | JSON → SQLite import 時にバックアップ (state/backup/{date}.tar.gz) |
| Python 3.12 で依存パッケージ非互換 | requirements.lock を 3.12 で再生成、CI で 3.11/3.12 両対応のまま |

## 5. 関連 ADR / イシュー

- ADR-013 (5 層ルール) — B-1 / B-2 はここの上に乗る
- ADR-014 (fraud_score) — Phase B Week 2-3 で papa-torb 取込
- ADR-015 (MarTech) — B-2 (JSONLogic 化) で applies_to も同フォーマットに
- ADR-016 (Entry Points) — B-4 (venv 再構築) は launchd 経由で透過的
- 残 Codex Review issue (M-2/M-3/M-4/M-5/M-9/M-10 + L-1〜L-4) — GitHub issue 化 + Phase B Week 1 末で着手判断

## 6. 補足: なぜ Phase A で実装しなかったか

Phase A の目標は「5/7 提案 + 5/8 launchd 本番投稿で pilotton 1 社の自動運用を成立させる」こと。リファクタや基盤刷新は本目標に直接は寄与せず、むしろ回帰リスクを増やす。Phase A は**追加 (機能拡張)** に集中し、Phase B で**整理 (構造改善)** に着手する分担。
