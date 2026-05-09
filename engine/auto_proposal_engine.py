"""自動提案エンジン (ADR-012 + ADR-013、5 層対応版)

Layer 構成:
- Layer A: 既存 277 ルール (config/rules/*.yaml) — 本エンジンは触らない
- Layer 0: Foundation (config/foundation/*.yaml) — 8 カテゴリ
- Layer 1: Vertical (config/verticals/*.yaml) — 9 業界
- Layer 2: EC Platform (config/ec_platforms/*.yaml) — 6 プラットフォーム
- Layer 3: Precision Category (config/precision_categories/*.yaml) — 7 カテゴリ

メインフロー:
  1. _load_all_layers()         → Layer 0-3 の全 YAML をロード
  2. _filter_by_environment()   → applies_to で環境マッチ
  3. _resolve_data_sources()    → client_state + ad_platform_api + rule_evaluation
  4. _evaluate_trigger()        → Python eval (安全 namespace)
  5. _check_prerequisite_chain()→ 既存 rule_id も参照可能
  6. _check_cooldown()          → 投稿履歴チェック
  7. _apply_severity_priority() → priority_weights.yaml + 6 グループスコア
  8. _enforce_caps()            → daily_cap_group ごとに上限
  9. _render_and_post()         → notifiers/chatwork_notifier.py 呼出
"""
from __future__ import annotations

import logging
import os
import yaml
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Any

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
LAYER_DIRS = {
    "foundation": CONFIG_DIR / "foundation",
    "vertical": CONFIG_DIR / "verticals",
    "ec_platform": CONFIG_DIR / "ec_platforms",
    "precision_category": CONFIG_DIR / "precision_categories",
}

CLIENT_STATE_DIR = ROOT / "outputs" / "client_state"
HISTORY_DIR = ROOT / "outputs" / "auto_proposal_history"
RULE_MESSAGING_PATH = CONFIG_DIR / "rule_messaging.yaml"


# ========== 5/8 v2: collect_eligible_rules (投稿せず eligible のみ返す) ==========

def collect_eligible_rules(
    client_id: str,
    today: Optional[str] = None,
    layer_filter: Optional[list[str]] = None,
) -> dict:
    """auto_proposal の評価フェーズだけを実行、投稿せず eligible_rules を返す。

    daily_todo_builder が Layer A indications と統合する目的で使う。
    cap / sort も適用済みの「selected」 (= 投稿候補) を返す。

    Returns:
        {
            "client_id": str,
            "loaded_rules_count": int,
            "environment_matched_count": int,
            "eligible_count": int,
            "selected": [<rule full dict>, ...],   # cap / sort 適用済み
            "history": dict,                        # _enforce_caps が使った history snapshot
        }
    """
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    state = load_client_state(client_id)
    client_cfg = _load_client_cfg(client_id)
    history = _load_history(client_id)

    rules = _load_all_layers(layer_filter)
    matched = _filter_by_environment(rules, client_cfg)

    eligible = []
    for rule in matched:
        data = _resolve_data_sources(rule, client_cfg, state)
        if not _evaluate_trigger(rule, data, today_str):
            continue
        if not _check_prerequisite_chain(rule, history, state):
            continue
        if _evaluate_skip_if(rule, data, today_str):
            continue
        if not _check_cooldown(rule, history, today_str):
            continue
        eligible.append(rule)

    sorted_rules = _apply_severity_priority(eligible)
    rules_index = {r.get("id"): r for r in rules}
    selected = _enforce_caps(sorted_rules, history, today_str, all_rules_index=rules_index)

    return {
        "client_id": client_id,
        "loaded_rules_count": len(rules),
        "environment_matched_count": len(matched),
        "eligible_count": len(eligible),
        "loaded_rules": rules,
        "matched_rules": matched,
        "eligible_rules": eligible,
        "selected": selected,
        "history": history,
        "client_cfg": client_cfg,
    }


def update_history_for_displayed(
    client_id: str, displayed_rule_ids: list, all_rules_index: dict, today_str: str,
) -> int:
    """統合通知が成功した後、displayed_rule_ids 全件の history を更新

    daily_todo_builder.post_daily_todo() から呼ばれる。
    Returns: 更新した件数
    """
    count = 0
    for rid in displayed_rule_ids:
        rule = all_rules_index.get(rid)
        if not rule:
            continue
        # auto_proposal 由来の rule のみ history 更新 (Layer A は indication_state 側で管理)
        if "daily_cap_group" not in rule:
            continue
        _update_history(
            client_id, rid, {"result": {"message_id": "unified_todo"}}, today_str,
            daily_cap_group=rule.get("daily_cap_group", "default"),
        )
        count += 1
    return count

# 1 日 1 まとめ投稿: 優先度 A の表示上限 (Atlassian alert fatigue best practice)
DAILY_RECOMMENDATIONS_PRIORITY_A_TOP = 3


# ========== Public API ==========

def run_auto_proposal(
    client_id: str,
    dry_run: bool = False,
    today: Optional[str] = None,
    layer_filter: Optional[list[str]] = None,
) -> dict:
    """1 client の自動提案サイクル実行

    Args:
        client_id: pilotton 等
        dry_run: True なら ChatWork 投稿せず投稿予定のみ表示
        today: シミュレーション日付 'YYYY-MM-DD'
        layer_filter: ['foundation', 'vertical'] 等で絞込 (None=全層)

    Returns:
        {
            "client_id": str,
            "loaded_rules_count": int,
            "environment_matched_count": int,
            "eligible_count": int,
            "posted_count": int,
            "skipped_count": int,
            "posted": [...]
        }
    """
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    state = load_client_state(client_id)
    client_cfg = _load_client_cfg(client_id)
    history = _load_history(client_id)

    # 1. 全 5 層をロード (Layer A は除外)
    rules = _load_all_layers(layer_filter)
    log.info(f"[{client_id}] auto_proposal: loaded {len(rules)} rules across {len(LAYER_DIRS)} layers")

    # 2. 環境マッチング
    matched = _filter_by_environment(rules, client_cfg)
    log.info(f"[{client_id}] environment_matched: {len(matched)} rules")

    # 3. data_source 解決 + trigger 評価 + prerequisite + cooldown
    eligible = []
    for rule in matched:
        data = _resolve_data_sources(rule, client_cfg, state)
        if not _evaluate_trigger(rule, data, today_str):
            continue
        if not _check_prerequisite_chain(rule, history, state):
            continue
        if _evaluate_skip_if(rule, data, today_str):
            continue
        if not _check_cooldown(rule, history, today_str):
            continue
        eligible.append(rule)

    log.info(f"[{client_id}] eligible after evaluation: {len(eligible)} rules")

    # 4. severity + 6 グループスコアでソート
    sorted_rules = _apply_severity_priority(eligible)

    # 5. daily_cap_group ごとに上限適用
    # 5/8 cap-bug-fix: history record に daily_cap_group が無い旧データに備え、
    # 全 rule index を渡して逆引きフォールバックを可能にする
    rules_index = {r.get("id"): r for r in rules}
    selected = _enforce_caps(sorted_rules, history, today_str, all_rules_index=rules_index)
    log.info(f"[{client_id}] selected after caps: {len(selected)} rules")

    # 6. 投稿 (5/8 通知 UX 改修: 1 日 1 まとめ投稿)
    # 旧実装: rule ごとに個別 ChatWork 投稿 → 連投で alert fatigue
    # 新実装: selected 全件を 1 メッセージ (_daily_recommendations.md.j2) にまとめて投稿
    # rule_messaging.yaml で performance_category / expected_effect / next_action_question
    # に変換し、優先度 A (今日確認) と B (今週中) の 2 階層で表示
    bundle_result = _render_and_post_bundle(
        selected, state, client_cfg, today_str=today_str, dry_run=dry_run,
    )

    chatwork_result = (bundle_result or {}).get("result") or {}
    is_dry_run = bool(chatwork_result.get("dry_run"))
    is_skipped = bool(chatwork_result.get("skipped"))
    is_failed = bool(bundle_result.get("error"))

    # 1 まとめ投稿のため、各カウントは「selected の総件数」または「0」
    attempted_count = len(selected)
    sent_count = 0 if (is_dry_run or is_skipped or is_failed) else attempted_count
    skipped_count = attempted_count if is_skipped else 0
    dry_run_count = attempted_count if is_dry_run else 0
    failed_count = attempted_count if is_failed else 0

    # 実送信成功時のみ history 更新。
    # 5/8 Codex 修正: 「本文に表示された rule_id」のみ更新する (displayed_rule_ids)。
    # 旧実装は selected 全件を更新していたが、本文に出ない rule が送信済み扱いに
    # なる問題があった。現実装では _render_and_post_bundle で全件本文に出る
    # ので displayed_rule_ids = selected.id だが、将来 truncate 等が入っても安全。
    if not dry_run and not is_skipped and not is_dry_run and not is_failed:
        displayed_set = set((bundle_result or {}).get("displayed_rule_ids") or [])
        for rule in selected:
            if rule.get("id") not in displayed_set:
                continue
            _update_history(
                client_id, rule["id"], bundle_result, today_str,
                daily_cap_group=rule.get("daily_cap_group", "default"),
            )

    return {
        "client_id": client_id,
        "loaded_rules_count": len(rules),
        "environment_matched_count": len(matched),
        "eligible_count": len(eligible),
        "attempted_count": attempted_count,
        "sent_count":      sent_count,
        "skipped_count":   skipped_count,
        "dry_run_count":   dry_run_count,
        "failed_count":    failed_count,
        "posted_count":    sent_count,    # 後方互換
        "posted":          [bundle_result] if bundle_result else [],  # 1 まとめなので 1 要素 list
    }


def load_client_state(client_id: str) -> dict:
    path = CLIENT_STATE_DIR / f"{client_id}.yaml"
    if not path.exists():
        return _empty_state(client_id)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or _empty_state(client_id)


def save_client_state(client_id: str, state: dict) -> None:
    CLIENT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIENT_STATE_DIR / f"{client_id}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(state, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


# ========== Layer Loading (ADR-013 D-1) ==========

def _load_all_layers(layer_filter: Optional[list[str]] = None) -> list[dict]:
    """Layer 0-3 の全 YAML を recursive ロード、rules 配列を flatten して返す"""
    all_rules: list[dict] = []
    for layer_name, layer_dir in LAYER_DIRS.items():
        if layer_filter and layer_name not in layer_filter:
            continue
        if not layer_dir.exists():
            continue
        for yaml_file in sorted(layer_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                rules = (data or {}).get("rules", [])
                for rule in rules:
                    rule.setdefault("_source_file", str(yaml_file.relative_to(ROOT)))
                all_rules.extend(rules)
            except Exception as e:
                log.warning(f"Failed to load {yaml_file}: {e}")
    return all_rules


# ========== Environment Matching (ADR-013 D-7 applies_to) ==========

def _filter_by_environment(rules: list[dict], client_cfg: dict) -> list[dict]:
    """applies_to の各フィールドでクライアント環境とマッチするルールを返す

    対応 applies_to キー:
      既存: countries / verticals / ec_platforms / ad_platforms / business_models
      ADR-015 拡張 (5/7 新規):
        tag_managers / analytics_platforms / mas / crms / cdps /
        capi_status / ab_testing_tools / chatbots

    フェイルセーフ (ADR-015 §2.4):
      tech_stack の confidence が "low" または値が "unknown" のカテゴリに依存するルールは
      スキップする (誤った指摘を避ける)。
    """
    matched = []
    company = client_cfg.get("company") or {}
    client_country = client_cfg.get("country", "JP")
    client_vertical = client_cfg.get("vertical") or company.get("industry")
    client_ad_platforms = client_cfg.get("ad_platforms") or [
        plat for plat in ["meta", "google", "tiktok"]
        if (client_cfg.get("ads") or {}).get(plat)
    ]
    client_bm = client_cfg.get("business_model", "b2c")

    tech_stack = client_cfg.get("tech_stack") or {}
    # ADR-015 §2.4 H-4 (5/8): ec_platform は tech_stack を最優先、フォールバックで client_cfg / company。
    # 他のスタックカテゴリと同列のフェイルセーフを適用する。
    ec_from_stack = _stack_value(tech_stack, "ec_platform")
    client_ec_platform = ec_from_stack or client_cfg.get("ec_platform") or company.get("ec_platform")
    ec_confidence = _stack_confidence(tech_stack, "ec_platform") if ec_from_stack else "high"
    # tech_stack 経由でない場合 (旧来 client_cfg.ec_platform) は high 扱い (5/7 互換)。
    # tech_stack で宣言されている場合のみ confidence: low/unknown でスキップ。

    stack_resolved = {
        "tag_manager":  _stack_value(tech_stack, "tag_manager"),
        "analytics":    _stack_list_or_value(tech_stack, "analytics"),
        "ma":           _stack_value(tech_stack, "ma"),
        "crm":          _stack_value(tech_stack, "crm"),
        "cdp":          _stack_value(tech_stack, "cdp"),
        "ab_testing":   _stack_value(tech_stack, "ab_testing"),
        "chatbot":      _stack_value(tech_stack, "chatbot"),
        "capi_status":  tech_stack.get("capi_status") or {},
    }
    stack_confidence = {k: _stack_confidence(tech_stack, k) for k in stack_resolved}

    for rule in rules:
        applies_to = rule.get("applies_to") or {}
        if not _match_list(applies_to.get("countries", ["all"]), client_country):
            continue
        if not _match_list(applies_to.get("verticals", ["all"]), client_vertical):
            continue
        # ec_platforms は他カテゴリ同様にフェイルセーフ統一 (H-4)
        if not _match_stack_category(
            applies_to.get("ec_platforms"), client_ec_platform, ec_confidence, rule,
        ):
            continue
        if not _match_any(applies_to.get("ad_platforms", ["all"]), client_ad_platforms):
            continue
        if not _match_list(applies_to.get("business_models", ["all"]), client_bm):
            continue

        # ADR-015 §2.4 拡張: tech_stack カテゴリ
        if not _match_stack_category(applies_to.get("tag_managers"), stack_resolved["tag_manager"], stack_confidence["tag_manager"], rule):
            continue
        if not _match_stack_category(applies_to.get("analytics_platforms"), stack_resolved["analytics"], stack_confidence["analytics"], rule):
            continue
        if not _match_stack_category(applies_to.get("mas"), stack_resolved["ma"], stack_confidence["ma"], rule):
            continue
        if not _match_stack_category(applies_to.get("crms"), stack_resolved["crm"], stack_confidence["crm"], rule):
            continue
        if not _match_stack_category(applies_to.get("cdps"), stack_resolved["cdp"], stack_confidence["cdp"], rule):
            continue
        if not _match_stack_category(applies_to.get("ab_testing_tools"), stack_resolved["ab_testing"], stack_confidence["ab_testing"], rule):
            continue
        if not _match_stack_category(applies_to.get("chatbots"), stack_resolved["chatbot"], stack_confidence["chatbot"], rule):
            continue
        if not _match_capi_status(applies_to.get("capi_status"), stack_resolved["capi_status"]):
            continue

        matched.append(rule)
    return matched


def _match_list(allowed: list, actual: Optional[str]) -> bool:
    if not allowed or "all" in allowed:
        return True
    if actual is None:
        return False
    return actual in allowed


def _match_any(allowed: list, actual_list: list) -> bool:
    if not allowed or "all" in allowed:
        return True
    return any(item in allowed for item in actual_list)


# ========== ADR-015 拡張ヘルパ ==========

def _stack_value(tech_stack: dict, key: str) -> Optional[str]:
    v = tech_stack.get(key)
    if isinstance(v, dict):
        return v.get("value")
    if isinstance(v, list):
        return v[0] if v else None
    if isinstance(v, str):
        return v
    return None


def _stack_list_or_value(tech_stack: dict, key: str) -> list:
    v = tech_stack.get(key)
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        val = v.get("value")
        return [val] if val else []
    if isinstance(v, str):
        return [v]
    return []


def _stack_confidence(tech_stack: dict, key: str) -> str:
    v = tech_stack.get(key)
    if isinstance(v, dict):
        return v.get("confidence") or "low"
    if isinstance(v, list):
        return "high" if v else "low"
    if isinstance(v, str):
        return "medium"
    return "low"


def _match_stack_category(
    allowed: Optional[list], actual_value, actual_confidence: str, rule: dict,
) -> bool:
    """tech_stack カテゴリの突合 (ADR-015 §2.4 フェイルセーフ込み)

    - applies_to に当該キー無し / [all] → 常にマッチ
    - confidence が "low" または値が unknown → 該当ルールはスキップ (= return False)
    - actual_value が list (analytics 等) → any-match
    - actual_value が str → 完全一致
    """
    if not allowed or "all" in allowed:
        return True
    # フェイルセーフ: 不確実カテゴリ依存ルールは評価しない
    if actual_confidence in ("low", "unknown") or actual_value in (None, "unknown", []):
        return False
    if isinstance(actual_value, list):
        return any(v in allowed for v in actual_value)
    return actual_value in allowed


def _match_capi_status(allowed: Optional[dict], actual: dict) -> bool:
    """capi_status の突合: applies_to.capi_status は {meta: not_configured} 等の dict 形式

    - applies_to に capi_status 指定なし → マッチ
    - 指定があれば、各 platform key について actual の同 key と一致するか確認
    - actual に対象 platform 未設定 → 不確実とみなしスキップ (False)
    """
    if not allowed:
        return True
    if not isinstance(allowed, dict) or not isinstance(actual, dict):
        return False
    for plat, expected_status in allowed.items():
        actual_status = actual.get(plat)
        if actual_status is None:
            return False
        # 単一値 or リストでの指定を許容
        if isinstance(expected_status, list):
            if actual_status not in expected_status:
                return False
        else:
            if actual_status != expected_status:
                return False
    return True


# ========== Data Source Resolution (ADR-013 D-7) ==========

def _resolve_data_sources(rule: dict, client_cfg: dict, state: dict) -> dict:
    """data_source の 3 系統 (client_state / ad_platform_api / rule_evaluation) を統合

    Returns:
        eval namespace に渡される dict:
        {
            "client_state": {...},
            "ad_platform_data": {...},
            "rule_evaluation": {...}
        }
    """
    namespace = {
        "client_state": dict(state),
        "ad_platform_data": {},
        "rule_evaluation": {},
    }
    for source_def in rule.get("data_source", []):
        source = source_def.get("source")
        if source == "client_state":
            # state は既に namespace に入っているので no-op
            continue
        elif source == "ad_platform_api":
            # 簡略実装: 既存 adapters の最新出力を参照
            # 本実装では state 内の "ad_platform_data" サブ dict を参照
            namespace["ad_platform_data"].update(state.get("ad_platform_data", {}))
        elif source == "rule_evaluation":
            # 既存 277 ルール評価結果を state.rule_evaluation から参照
            namespace["rule_evaluation"].update(state.get("rule_evaluation", {}))
    return namespace


# ========== Trigger Evaluation (ADR-013 D-7) ==========

def _evaluate_trigger(rule: dict, data: dict, today_str: str) -> bool:
    """trigger.condition を Python eval で安全評価"""
    trigger = rule.get("trigger") or {}
    condition = trigger.get("condition")
    if not condition:
        return False

    # 安全な eval namespace
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "client_state": _DotDict(data["client_state"]),
        "ad_platform_data": _DotDict(data["ad_platform_data"]),
        "rule_evaluation": _DotDict(data["rule_evaluation"]),
        "True": True,
        "False": False,
        "None": None,
    }
    try:
        return bool(eval(condition, safe_globals, safe_locals))
    except Exception as e:
        log.debug(f"trigger eval failed for {rule.get('id')}: {e}")
        return False


def _evaluate_skip_if(rule: dict, data: dict, today_str: str) -> bool:
    """skip_if 条件を評価 (True なら skip)"""
    skip_if = rule.get("skip_if")
    if not skip_if:
        return False
    if isinstance(skip_if, list):
        # リスト形式 [{phase: A}, ...] (ADR-012 互換)
        for cond in skip_if:
            for field, expected in cond.items():
                actual = data["client_state"].get(field) if hasattr(data["client_state"], "get") else getattr(data["client_state"], field, None)
                if actual == expected:
                    return True
        return False
    # 文字列形式 (eval)
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "client_state": _DotDict(data["client_state"]),
        "True": True, "False": False, "None": None,
    }
    try:
        return bool(eval(skip_if, safe_globals, safe_locals))
    except Exception:
        return False


# ========== Prerequisite & Cooldown ==========

def _check_prerequisite_chain(rule: dict, history: dict, state: dict) -> bool:
    """prerequisite に既存 rule_id (M01 等) も参照可能。
    既存 rule_id が違反状態なら prerequisite 未達 = skip。
    """
    prereq = rule.get("prerequisite")
    if not prereq:
        return True
    if isinstance(prereq, str):
        # 単一 rule_id 参照
        # state.rule_evaluation で「該当 rule が違反していない」を確認
        rule_evaluation = state.get("rule_evaluation", {})
        # 違反状態 (True) なら prerequisite 未達
        violated = rule_evaluation.get(f"{prereq}_violated", False)
        return not violated
    if isinstance(prereq, list):
        for p in prereq:
            if not _check_prerequisite_chain({"prerequisite": p}, history, state):
                return False
        return True
    return True


def _check_cooldown(rule: dict, history: dict, today_str: str) -> bool:
    """前回投稿日 + cooldown_days を超えているか"""
    rule_id = rule["id"]
    last = history.get(rule_id, {}).get("last_sent_date")
    if last is None:
        return True
    cooldown_days = int(rule.get("cooldown_days") or 0)
    try:
        last_date = datetime.fromisoformat(last).date()
        today_date = datetime.fromisoformat(today_str).date()
        return (today_date - last_date).days >= cooldown_days
    except (ValueError, TypeError):
        return True


# ========== Priority Sorting ==========

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _apply_severity_priority(rules: list[dict]) -> list[dict]:
    """severity 降順 + priority 降順 でソート"""
    return sorted(
        rules,
        key=lambda r: (
            SEVERITY_ORDER.get(r.get("severity", "medium"), 9),
            -int(r.get("priority", 0)),
            r.get("id", "")
        )
    )


# ========== Daily Cap (ADR-013 D-8) ==========

DEFAULT_CAPS = {
    "default": 3,           # 5/7 提案前: 1→3 (Phase A 内部レビュー期間中の見える化優先)
    "adr_005": 3,           # 既存 ChatWork 指摘 cap
    "adr_013_legal": 2,     # 法令系専用枠 (ADR-013 D-6) 1→2 (景表法/薬機法/特商法を分岐)
}


def _enforce_caps(
    sorted_rules: list[dict], history: dict, today_str: str,
    all_rules_index: Optional[dict] = None,
) -> list[dict]:
    """daily_cap_group ごとに上限適用 (5/8 cap-bug-fix)

    cap counter は history record の `daily_cap_group` を優先。
    旧 history (フィールド無し) は all_rules_index から rule_id → daily_cap_group を
    逆引きしてフォールバック。逆引きできなければ "default" 扱い。
    """
    selected = []
    today_count_per_group: dict[str, int] = {}
    rules_index = all_rules_index or {r.get("id"): r for r in sorted_rules}

    # 既に今日投稿済の件数を集計
    for rule_id, h in history.items():
        if h.get("last_sent_date") != today_str:
            continue
        # 1. history record に保存された daily_cap_group を優先
        grp = h.get("daily_cap_group")
        if not grp:
            # 2. フォールバック: rule 定義側から逆引き
            rule_def = rules_index.get(rule_id) or {}
            grp = rule_def.get("daily_cap_group", "default")
        today_count_per_group[grp] = today_count_per_group.get(grp, 0) + 1

    for rule in sorted_rules:
        grp = rule.get("daily_cap_group", "default")
        cap = DEFAULT_CAPS.get(grp, 1)
        used = today_count_per_group.get(grp, 0)
        if used < cap:
            selected.append(rule)
            today_count_per_group[grp] = used + 1
    return selected


# ========== Render & Post ==========

def _render_and_post(rule: dict, state: dict, client_cfg: dict, dry_run: bool = False) -> dict:
    """テンプレート rendering + ChatWork 投稿"""
    from notifiers.chatwork_notifier import ChatWorkClient
    from templates.chatwork import render

    template_name = rule.get("template", "_client_request_base.md.j2")
    if not template_name.startswith("templates/chatwork/"):
        # 単純ファイル名指定の場合は templates/chatwork/ を補完
        pass  # render() が templates/chatwork/ を looker

    company = client_cfg.get("company") or {}
    chatwork_rooms = client_cfg.get("chatwork_rooms") or {}
    room_id = chatwork_rooms.get("main")

    context = {
        "client_name": company.get("name") or client_cfg.get("client_id", "クライアント"),
        "honorific": company.get("honorific", "御中"),
        "today": datetime.now().strftime("%Y-%m-%d"),
        "deadline": _calc_deadline(rule),
        "deadline_days": rule.get("deadline_days", 14),
        "rule_id": rule["id"],
        "rule_name": rule.get("name", ""),
        "rationale": rule.get("rationale", ""),
        "priority_label": _priority_label(rule.get("severity", "medium")),
        "phase": state.get("phase", "A"),
        "ec_platform": state.get("ec_platform"),
        "legal_reference": rule.get("legal_reference"),
        "evidence_fields": {f: state.get(f) for f in rule.get("evidence_fields", [])},
    }

    body = render(template_name, context)

    chat = ChatWorkClient(room_id=room_id, dry_run=dry_run)
    result = chat.post_message(body)
    return {
        "rule_id": rule["id"],
        "result": result,
        "body_length": len(body),
        "template": template_name,
    }


# ========== Bundle Render & Post (5/8 1日1まとめ投稿改修) ==========

_RULE_MESSAGING_CACHE: Optional[dict] = None


def _load_rule_messaging() -> dict:
    """config/rule_messaging.yaml をキャッシュ付きでロード"""
    global _RULE_MESSAGING_CACHE
    if _RULE_MESSAGING_CACHE is not None:
        return _RULE_MESSAGING_CACHE
    if not RULE_MESSAGING_PATH.exists():
        _RULE_MESSAGING_CACHE = {"rules": {}, "category_labels": {}, "default": {}}
        return _RULE_MESSAGING_CACHE
    try:
        _RULE_MESSAGING_CACHE = yaml.safe_load(RULE_MESSAGING_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        _RULE_MESSAGING_CACHE = {"rules": {}, "category_labels": {}, "default": {}}
    return _RULE_MESSAGING_CACHE


# 5/8 v2 緊急修正: 旧 _build_recommendation_item を削除。
# next_action_question fallback "本項目 ({rule_id}) について現状をご共有ください。" は
# 顧客向け文言として禁止 (rule_messaging.yaml 未登録 rule は internal_unmapped_rule で
# 本文から除外される設計に統一)。
# rule_messaging からの item 生成は engine/daily_todo_builder.build_recommendation_item に
# 一元化された。本ファイル内では _render_and_post_bundle のレガシー path のために
# 直接 build_recommendation_item を呼ぶ。


def _render_and_post_bundle(
    selected_rules: list[dict], state: dict, client_cfg: dict,
    today_str: str, dry_run: bool = False,
) -> dict:
    """selected の全 rule を 1 メッセージ (_daily_recommendations.md.j2) にまとめて投稿

    5/8 Codex review 修正:
        旧実装は priority A の 4 件目以降を本文非表示にしつつ history を全件更新し、
        「本文に出ない rule が送信済み扱い」になる問題があった。
        本実装では selected 全件を必ず本文に出し (priority A 上位 3 件は詳細、
        4 件目以降は要約、priority B は要約)、displayed_rule_ids を返して
        run_auto_proposal が history 更新の正しい対象を識別できるようにする。

    Returns:
        {
            "rule_ids":           [...],   # selected の全 rule_id (旧仕様互換)
            "displayed_rule_ids": [...],   # 本文に表示された rule_id (history 更新対象)
            "result":             <chatwork.post_message 戻り値>,
            "body_length":        int,
            "items_priority_a_detailed_count": int,
            "items_priority_a_summary_count":  int,
            "items_priority_b_count":          int,
        } or {"error": "..."} (selected 空 / 投稿失敗時)
    """
    if not selected_rules:
        return {"rule_ids": [], "displayed_rule_ids": [],
                "result": {"skipped": True}, "body_length": 0,
                "items_priority_a_detailed_count": 0,
                "items_priority_a_summary_count": 0,
                "items_priority_b_count": 0}

    from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError
    from templates.chatwork import render
    # 5/8 v2: 統合通知テンプレに合わせて daily_todo_builder.build_daily_todo を使用
    from engine.daily_todo_builder import build_daily_todo

    # auto_proposal の selected_rules のみで context を構築 (Layer A indication なし)
    context = build_daily_todo(
        client_id=client_cfg.get("client_id") or "unknown",
        client_cfg=client_cfg,
        layer_a_rule_ids=[],
        eligible_rules=selected_rules,
        today_str=today_str,
        anomaly_summary=None,
    )

    chatwork_rooms = client_cfg.get("chatwork_rooms") or {}
    room_id = chatwork_rooms.get("main")

    body = render("_daily_recommendations.md.j2", context)
    rule_ids = [r.get("id") for r in selected_rules]
    displayed_rule_ids = context.get("displayed_rule_ids") or []
    items_a_detailed = context.get("items_today") or []
    items_a_summary = []      # 統合 schema では items_today / items_this_week / items_legal_note の 3 区分
    items_b = (context.get("items_this_week") or []) + (context.get("items_legal_note") or [])

    try:
        chat = ChatWorkClient(room_id=room_id, dry_run=dry_run)
        result = chat.post_message(body)
    except ChatWorkError as e:
        return {
            "rule_ids": rule_ids,
            "displayed_rule_ids": displayed_rule_ids,
            "result": {"error": str(e)},
            "error":  str(e),
            "body_length": len(body),
            "items_priority_a_detailed_count": len(items_a_detailed),
            "items_priority_a_summary_count":  len(items_a_summary),
            "items_priority_b_count":          len(items_b),
        }

    return {
        "rule_ids":           rule_ids,
        "displayed_rule_ids": displayed_rule_ids,
        "result":             result,
        "body_length":        len(body),
        "items_priority_a_detailed_count": len(items_a_detailed),
        "items_priority_a_summary_count":  len(items_a_summary),
        "items_priority_b_count":          len(items_b),
    }


def _calc_deadline(rule: dict) -> Optional[str]:
    days = rule.get("deadline_days")
    if days is None:
        return None
    from datetime import timedelta
    return (datetime.now().date() + timedelta(days=int(days))).isoformat()


def _priority_label(severity: str) -> str:
    return {"critical": "緊急", "high": "高", "medium": "中", "low": "低", "info": "情報"}.get(severity, "中")


# ========== History Management ==========

def _load_history(client_id: str) -> dict:
    path = HISTORY_DIR / f"{client_id}.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_history(client_id: str, history: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{client_id}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(history, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def _update_history(
    client_id: str, rule_id: str, result: dict, today_str: str,
    daily_cap_group: str = "default",
) -> None:
    """5/8 cap-bug-fix: daily_cap_group を history に保存し、_enforce_caps で
    cap グループ別 counter が正しく動くようにする。
    旧実装は daily_cap_group を保存していなかったため、_enforce_caps が
    全件 "default" 扱いに fallback し、別グループの cap が空いていると誤判定
    して同日 2 回目実行で追加投稿が発生していた。
    """
    history = _load_history(client_id)
    history[rule_id] = {
        "last_sent_date": today_str,
        "last_sent_at": datetime.now().isoformat(timespec="seconds"),
        "daily_cap_group": daily_cap_group,
        "result": result.get("result", {}),
    }
    _save_history(client_id, history)


# ========== Helpers ==========

def _load_client_cfg(client_id: str) -> dict:
    """config/clients.yaml からクライアント設定を取得"""
    clients_path = CONFIG_DIR / "clients.yaml"
    if not clients_path.exists():
        return {"client_id": client_id}
    try:
        data = yaml.safe_load(clients_path.read_text(encoding="utf-8")) or {}
        return (data.get("clients") or {}).get(client_id) or {"client_id": client_id}
    except Exception:
        return {"client_id": client_id}


def _empty_state(client_id: str) -> dict:
    return {
        "client_id": client_id,
        "phase": "A",
        "ec_platform": None,
        "vertical": None,
        "rule_evaluation": {},
        "ad_platform_data": {},
    }


class _DotDict(dict):
    """eval 内で client_state.foo の dot 記法アクセスを許容"""
    def __getattr__(self, key):
        if key in self:
            val = self[key]
            if isinstance(val, dict):
                return _DotDict(val)
            return val
        return None
