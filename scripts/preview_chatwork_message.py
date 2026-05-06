"""ChatWork 統合通知の本文プレビュー (5/8 v2)

state の cap / cooldown / 既送履歴に邪魔されず、現状で出るはずの本文を確認できる。
本番 ChatWork には一切送信せず、stdout に本文を出力する。

使い方:
    venv/bin/python3 scripts/preview_chatwork_message.py --client pilotton
    venv/bin/python3 scripts/preview_chatwork_message.py --client pilotton --today 2026-05-08
    venv/bin/python3 scripts/preview_chatwork_message.py --client pilotton --bypass-cap

オプション:
    --client       クライアント ID (default: pilotton)
    --today        シミュレーション日 (default: 今日)
    --bypass-cap   history を空 dict として扱い、cap counter を無視する
    --no-anomaly   audit_results を取得せず、anomaly_summary を None にする (高速確認用)
    --include-layer-a/--exclude-layer-a   Layer A indication を含めるか (default: include)
    --raw          [info][title]... を含む生本文を出力 (default: ON)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("preview")


def _collect_layer_a_rule_ids(client_id: str, today_str: str, exclude_layer_a: bool) -> tuple:
    """Layer A の indication 候補を取得 (audit + indication_state 経由)

    Returns: (rule_ids: list, rule_defs: dict, audit_results: dict)
    """
    if exclude_layer_a:
        return [], {}, {}

    try:
        from scripts.daily_chatwork_check import fetch_audit_results
        from engine.indication_state import IndicationState
        from engine.indication_detector import detect_and_upsert
        from engine.indication_filter import filter_indications

        audit = fetch_audit_results(client_id)
        if not audit.get("data_available"):
            log.warning(f"audit data unavailable for {client_id}")
            return [], {}, audit

        # IndicationState は in-memory のみで動かす (永続化しない)
        state = IndicationState(client_id=client_id)
        upserted, _clean = detect_and_upsert(audit, state, today=today_str)
        # filter (cap 等含むが、preview では情報量重視)
        notify_targets = filter_indications(upserted, state, today=today_str)

        rule_ids = [r.get("rule_id") for r in notify_targets if r.get("rule_id")]
        rule_defs = {r.get("rule_id"): r for r in notify_targets if r.get("rule_id")}
        return rule_ids, rule_defs, audit
    except Exception as e:
        log.warning(f"Layer A 取得失敗: {e}")
        return [], {}, {}


def preview(
    client_id: str, today_str: str,
    bypass_cap: bool = False, no_anomaly: bool = False,
    exclude_layer_a: bool = False,
) -> str:
    """統合通知の本文を生成して返す (ChatWork に送信しない)"""
    from engine.auto_proposal_engine import collect_eligible_rules
    from engine.daily_todo_builder import build_daily_todo
    from templates.chatwork import render

    # 1. Layer A indications を収集
    layer_a_rule_ids, layer_a_rule_defs, audit_results = _collect_layer_a_rule_ids(
        client_id, today_str, exclude_layer_a,
    )

    # 2. auto_proposal eligible_rules を取得
    auto_summary = collect_eligible_rules(client_id, today=today_str)
    selected_rules = auto_summary["selected"]

    # 3. bypass_cap=True なら history を空にして再収集 (cap 無視)
    if bypass_cap:
        # cap counter のみ無視 (cooldown は維持)。history を空 dict として再評価。
        from engine.auto_proposal_engine import (
            _load_all_layers, _filter_by_environment, _resolve_data_sources,
            _evaluate_trigger, _evaluate_skip_if, _check_prerequisite_chain,
            _check_cooldown, _apply_severity_priority, _enforce_caps, load_client_state,
            _load_client_cfg,
        )
        rules = _load_all_layers()
        client_cfg = _load_client_cfg(client_id)
        state = load_client_state(client_id)
        empty_history: dict = {}
        matched = _filter_by_environment(rules, client_cfg)
        eligible = []
        for rule in matched:
            data = _resolve_data_sources(rule, client_cfg, state)
            if not _evaluate_trigger(rule, data, today_str):
                continue
            if not _check_prerequisite_chain(rule, empty_history, state):
                continue
            if _evaluate_skip_if(rule, data, today_str):
                continue
            if not _check_cooldown(rule, empty_history, today_str):
                continue
            eligible.append(rule)
        sorted_rules = _apply_severity_priority(eligible)
        rules_index = {r.get("id"): r for r in rules}
        selected_rules = _enforce_caps(sorted_rules, empty_history, today_str, all_rules_index=rules_index)
        log.warning(f"--bypass-cap: history を空にして再収集 (selected: {len(selected_rules)} 件)")

    # 4. anomaly_summary を audit_results から抽出
    if no_anomaly:
        anomaly_summary = None
    else:
        from engine.daily_todo_builder import _extract_anomaly_summary
        anomaly_summary = _extract_anomaly_summary(audit_results) or {}

    # 5. context 構築 + render
    client_cfg = auto_summary["client_cfg"]
    context = build_daily_todo(
        client_id=client_id,
        client_cfg=client_cfg,
        layer_a_rule_ids=layer_a_rule_ids,
        eligible_rules=selected_rules,
        layer_a_rule_defs=layer_a_rule_defs,
        anomaly_summary=anomaly_summary,
        today_str=today_str,
    )

    # 6. metadata を stderr に、本文を stdout に
    print(
        f"=== preview metadata ===\n"
        f"client:    {client_id}\n"
        f"today:     {today_str}\n"
        f"bypass_cap: {bypass_cap}\n"
        f"layer_a count:    {len(layer_a_rule_ids)}\n"
        f"auto_proposal selected: {len(selected_rules)}\n"
        f"items_today:      {len(context['items_today'])}\n"
        f"items_this_week:  {len(context['items_this_week'])}\n"
        f"items_legal_note: {len(context['items_legal_note'])}\n"
        f"unmapped:         {len(context['internal_unmapped_rules'])}\n",
        file=sys.stderr,
    )
    if context["internal_unmapped_rules"]:
        print(
            f"  unmapped rules: {', '.join(context['internal_unmapped_rules'])}\n",
            file=sys.stderr,
        )

    if context["total_count"] == 0:
        print("=== 本文 (通知対象 0 件) ===", file=sys.stderr)
        return "(通知対象 0 件、ChatWork 投稿スキップ)"

    body = render("_daily_recommendations.md.j2", context)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatWork 統合通知 preview (本番送信なし)")
    parser.add_argument("--client", default="pilotton")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD (default: 今日)")
    parser.add_argument("--bypass-cap", action="store_true",
                        help="history を空にして cap counter を無視")
    parser.add_argument("--no-anomaly", action="store_true",
                        help="audit を取らず anomaly_summary を空にする (高速確認用)")
    parser.add_argument("--exclude-layer-a", action="store_true",
                        help="Layer A indication を含めない")
    args = parser.parse_args()

    today_str = args.today or datetime.now().strftime("%Y-%m-%d")

    body = preview(
        client_id=args.client, today_str=today_str,
        bypass_cap=args.bypass_cap, no_anomaly=args.no_anomaly,
        exclude_layer_a=args.exclude_layer_a,
    )
    print("=== 本文 ===", file=sys.stderr)
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
