"""統合 daily TODO ビルダー (5/8 v2 緊急修正)

責務: Layer A の indications (anomaly / ads_audit / fraud / X-PI1 等) と
      Layer 0-3 の auto_proposal eligible rules を 1 つの統合通知
      「本日の広告成果改善TODO」にまとめる。

設計原則:
    - 1 日 1 通の統合通知に集約 (個別投稿廃止)
    - rule_messaging.yaml 未定義 rule は **顧客向けに出さない** (fallback 禁止)
      未定義は internal log に internal_unmapped_rule として記録
    - 表示順序は goal_stage 順 (measurement_recovery → cpa_diagnosis →
      delivery_diagnosis → first_party_data → legal_review)
    - 上位 3 件は「今日確認」として詳細表示、4 件目以降は「今週中」要約
    - priority A 0 件時は適切なメッセージで案内 (旧「上位0件」バグ修正)
    - 冒頭に「今日の結論」を 1〜2 行 (anomaly 由来の数値があれば差し込み)

主要関数:
    - build_daily_todo(client_id, indications, eligible_rules, anomaly_summary, ...)
      → context dict (テンプレ render 用)
    - load_messaging() → rule_messaging.yaml キャッシュロード
    - resolve_rule_message(rule_id, fallback_meta=None) → messaging 取得 (未定義は None)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
RULE_MESSAGING_PATH = ROOT / "config" / "rule_messaging.yaml"

# 上位何件を「今日確認」詳細表示するか (Atlassian alert fatigue best practice)
DETAILED_TOP_N = 3

_MESSAGING_CACHE: Optional[dict] = None


# ========== Public API ==========

def load_messaging() -> dict:
    """rule_messaging.yaml をキャッシュ付きでロード"""
    global _MESSAGING_CACHE
    if _MESSAGING_CACHE is not None:
        return _MESSAGING_CACHE
    if not RULE_MESSAGING_PATH.exists():
        _MESSAGING_CACHE = {"rules": {}, "category_labels": {}, "goal_stage_order": {}}
        return _MESSAGING_CACHE
    try:
        _MESSAGING_CACHE = yaml.safe_load(RULE_MESSAGING_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        _MESSAGING_CACHE = {"rules": {}, "category_labels": {}, "goal_stage_order": {}}
    return _MESSAGING_CACHE


def reset_cache() -> None:
    """テスト用: messaging cache をリセット"""
    global _MESSAGING_CACHE
    _MESSAGING_CACHE = None


def resolve_rule_message(rule_id: str) -> Optional[dict]:
    """rule_id → rule_messaging.yaml 定義 (未定義なら None)

    fallback 禁止: 未定義 rule は呼出側で skip + internal_unmapped_rule ログ。
    """
    msg = load_messaging()
    return (msg.get("rules") or {}).get(rule_id)


def build_recommendation_item(rule_id: str, rule_def: dict, msg_def: dict, messaging: dict) -> dict:
    """rule_messaging 定義を 1 件分の表示用 dict に整形"""
    labels = messaging.get("category_labels") or {}
    perf_keys = msg_def.get("performance_category") or []
    perf_labels = [labels.get(k, k) for k in perf_keys]
    return {
        "rule_id":             rule_id,
        "customer_title":      msg_def.get("customer_title") or rule_def.get("name", rule_id),
        "performance_category_keys":   perf_keys,
        "performance_category_labels": perf_labels,
        "priority":            msg_def.get("priority", "B"),
        "goal_stage":          msg_def.get("goal_stage", "measurement_recovery"),
        "expected_effect":     msg_def.get("expected_effect") or [],
        "today_action":        msg_def.get("today_action") or "",
        "yes_no_question":     msg_def.get("yes_no_question") or "",
        "action_options":      msg_def.get("action_options") or {"A": "対応済", "B": "未対応", "C": "確認したい"},
        "legal_note":          msg_def.get("legal_note"),
    }


def build_daily_todo(
    client_id: str,
    client_cfg: dict,
    layer_a_rule_ids: list[str],          # indication_state 由来 (rule_id list)
    eligible_rules: list[dict],           # auto_proposal_engine の eligible (rule full dicts)
    layer_a_rule_defs: Optional[dict] = None,   # rule_id → rule full dict (X-PI1 等の定義)
    anomaly_summary: Optional[dict] = None,
    today_str: Optional[str] = None,
) -> dict:
    """統合 TODO の context を構築 (テンプレ render 直前まで)

    Args:
        client_id: pilotton 等
        client_cfg: clients.yaml のクライアント設定
        layer_a_rule_ids: Layer A 由来の rule_id list (X-PI1 / ANO_* / M62 等)
        eligible_rules: Layer 0-3 の eligible_rules (full dict、daily_cap_group 等含む)
        layer_a_rule_defs: rule_id → 簡易 rule dict (name 取得用、無ければ {})
        anomaly_summary: {"cpa_change_pct": +75.6, "impression_change_pct": -68.0} 等
        today_str: 'YYYY-MM-DD'

    Returns:
        dict: テンプレ render 用 context
        {
            "client_name", "honorific", "today",
            "headline":             "今日の結論 (冒頭 1〜2 行)",
            "items_today":          [item, ...] (上位 3 件、詳細表示)
            "items_this_week":      [item, ...] (4 件目以降 + priority B、要約表示)
            "items_legal_note":     [item, ...] (法令補足セクション)
            "total_count":          int (顧客向け表示総件数 = items_today + items_this_week)
            "internal_unmapped_rules": [rule_id, ...] (未定義 rule、内部ログ用、本文には出さない)
            "headline_message":     "(headline 生成失敗時のフォールバック文言)"
        }
    """
    messaging = load_messaging()
    layer_a_rule_defs = layer_a_rule_defs or {}

    # === 1. Layer A indications を items に変換 ===
    layer_a_items: list[dict] = []
    unmapped: list[str] = []
    for rid in layer_a_rule_ids:
        msg_def = (messaging.get("rules") or {}).get(rid)
        if not msg_def:
            unmapped.append(rid)
            continue
        rule_def = layer_a_rule_defs.get(rid) or {"id": rid}
        layer_a_items.append(build_recommendation_item(rid, rule_def, msg_def, messaging))

    # === 2. auto_proposal eligible rules を items に変換 ===
    auto_items: list[dict] = []
    for r in eligible_rules:
        rid = r.get("id", "")
        msg_def = (messaging.get("rules") or {}).get(rid)
        if not msg_def:
            unmapped.append(rid)
            continue
        auto_items.append(build_recommendation_item(rid, r, msg_def, messaging))

    # === 3. 統合 + goal_stage 順ソート ===
    all_items = layer_a_items + auto_items
    goal_order = messaging.get("goal_stage_order") or {}
    priority_order = {"A": 0, "B": 1, "C": 2}

    def _sort_key(it):
        return (
            priority_order.get(it["priority"], 99),
            goal_order.get(it["goal_stage"], 99),
            it["rule_id"],
        )

    all_items.sort(key=_sort_key)

    # === 4. items_today / items_this_week / items_legal_note に分割 ===
    items_today = []
    items_this_week = []
    items_legal_note = []

    for it in all_items:
        if it["priority"] == "A" and len(items_today) < DETAILED_TOP_N:
            items_today.append(it)
        elif it["goal_stage"] == "legal_review" and it["priority"] != "A":
            items_legal_note.append(it)
        else:
            items_this_week.append(it)

    # === 5. headline (今日の結論) 生成 ===
    headline = _build_headline(items_today, anomaly_summary)

    # === 6. 内部ログ (fallback 禁止違反の検知) ===
    if unmapped:
        log.warning(
            f"[{client_id}] internal_unmapped_rule: rule_messaging.yaml 未定義 "
            f"({len(unmapped)} 件、顧客通知から除外): {', '.join(sorted(set(unmapped))[:10])}"
        )

    company = client_cfg.get("company") or {}
    return {
        "client_name":  company.get("name") or client_id,
        "honorific":    company.get("honorific", "御中"),
        "today":        today_str,
        "headline":     headline,
        "items_today":         items_today,
        "items_this_week":     items_this_week,
        "items_legal_note":    items_legal_note,
        "total_count":         len(items_today) + len(items_this_week) + len(items_legal_note),
        "displayed_rule_ids":  [i["rule_id"] for i in items_today + items_this_week + items_legal_note],
        "internal_unmapped_rules": sorted(set(unmapped)),
        "anomaly_summary":     anomaly_summary or {},
    }


# ========== Private ==========

def post_daily_todo(
    client_id: str,
    layer_a_notify_records: list[dict],   # indication_filter.filter_indications の戻り値
    audit_results: dict,                   # fetch_audit_results の戻り値 (anomaly summary 用)
    state,                                 # IndicationState (mark_indication_notified 用)
    today_str: str,
    dry_run: bool = False,
) -> dict:
    """統合通知 1 通を ChatWork に投稿 (旧 daily_indication + auto_proposal の代替)

    Returns:
        {
            "posted_indications": int,
            "auto_proposal_attempted": int,
            "auto_proposal_sent": int,
            "auto_proposal_skipped": int,
            "auto_proposal_dry_run": int,
            "auto_proposal_failed": int,
            "internal_unmapped_rules": [...],
            "errors": [...]
        }
    """
    from engine.auto_proposal_engine import collect_eligible_rules, update_history_for_displayed
    from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError
    from templates.chatwork import render

    errors: list[str] = []

    # 1. Layer A indications の rule_id を抽出
    layer_a_rule_ids = [r.get("rule_id") for r in (layer_a_notify_records or []) if r.get("rule_id")]
    layer_a_rule_defs = {r.get("rule_id"): r for r in (layer_a_notify_records or []) if r.get("rule_id")}

    # 2. auto_proposal eligible rules を取得 (投稿しない)
    auto_summary = collect_eligible_rules(client_id, today=today_str)
    selected_rules = auto_summary["selected"]
    client_cfg = auto_summary["client_cfg"]

    # 3. anomaly summary (audit_results から CPA/IMP/CV 変動を取り出し)
    anomaly_summary = _extract_anomaly_summary(audit_results)

    # 4. 統合 context 構築
    context = build_daily_todo(
        client_id=client_id,
        client_cfg=client_cfg,
        layer_a_rule_ids=layer_a_rule_ids,
        eligible_rules=selected_rules,
        layer_a_rule_defs=layer_a_rule_defs,
        anomaly_summary=anomaly_summary,
        today_str=today_str,
    )

    summary = {
        "posted_indications": 0,
        "auto_proposal_attempted": len(selected_rules),
        "auto_proposal_sent": 0,
        "auto_proposal_skipped": 0,
        "auto_proposal_dry_run": 0,
        "auto_proposal_failed": 0,
        "internal_unmapped_rules": context["internal_unmapped_rules"],
        "total_count": context["total_count"],
        "errors": errors,
    }

    # 5. 本文に出る項目が 0 件 (= 全 rule が unmapped or eligible/Layer A 0 件) なら ChatWork 静寂
    if context["total_count"] == 0:
        log.info(f"[{client_id}] 統合 TODO: 通知対象 0 件、ChatWork 投稿スキップ")
        return summary

    body = render("_daily_recommendations.md.j2", context)

    # 6. ChatWork 投稿
    chatwork_rooms = client_cfg.get("chatwork_rooms") or {}
    room_id = chatwork_rooms.get("main")
    try:
        chat = ChatWorkClient(room_id=room_id, dry_run=dry_run)
        result = chat.post_message(body)
    except ChatWorkError as e:
        errors.append(f"daily_todo_post: {e}")
        summary["auto_proposal_failed"] = len(selected_rules)
        return summary

    is_dry_run = bool(result.get("dry_run"))
    is_skipped = bool(result.get("skipped"))

    if is_dry_run:
        summary["auto_proposal_dry_run"] = len(selected_rules)
        log.info(f"[{client_id}] 統合 TODO [dry_run]: state を進めません")
        return summary

    if is_skipped:
        summary["auto_proposal_skipped"] = len(selected_rules)
        log.info(f"[{client_id}] 統合 TODO スキップ (idempotency hit)")
        return summary

    # 7. 本番送信成功時のみ state を進める
    summary["auto_proposal_sent"] = len([r for r in selected_rules
                                          if r.get("id") in context["displayed_rule_ids"]])
    summary["posted_indications"] = len([rid for rid in layer_a_rule_ids
                                          if rid in context["displayed_rule_ids"]])

    # 7a. Layer A の indication_state を進める
    for rec in (layer_a_notify_records or []):
        if rec.get("rule_id") in context["displayed_rule_ids"]:
            state.mark_indication_notified(rec["indication_id"], today=today_str)

    # 7b. auto_proposal の history を進める (displayed_rule_ids のみ)
    rules_index = {r.get("id"): r for r in selected_rules}
    update_history_for_displayed(
        client_id, context["displayed_rule_ids"], rules_index, today_str,
    )

    log.info(
        f"[{client_id}] 統合 TODO 投稿成功: indications={summary['posted_indications']} "
        f"auto_proposal={summary['auto_proposal_sent']}"
    )
    return summary


def _extract_anomaly_summary(audit_results: dict) -> dict:
    """audit_results.anomalies から CPA/IMP/CV の変動率を抽出"""
    if not audit_results:
        return {}
    anomalies = audit_results.get("anomalies") or {}
    alerts = anomalies.get("alerts") or []
    out = {}
    for a in alerts:
        atype = a.get("type", "").lower()
        msg = a.get("message", "")
        # message から %変動を簡易抽出
        # 例: "CPA +75.6%" / "インプレッション -68.0%"
        import re
        m = re.search(r"([+\-]?\d+\.?\d*)\s*%", msg)
        if not m:
            continue
        pct = float(m.group(1))
        if "cpa" in atype or "CPA" in msg:
            out.setdefault("cpa_change_pct", pct)
        elif "impression" in atype or "インプレッション" in msg or "imp" in atype:
            out.setdefault("impression_change_pct", pct)
        elif "cv" in atype or "コンバージョン" in msg:
            out.setdefault("cv_change_pct", pct)
    return out


def _build_headline(items_today: list[dict], anomaly_summary: Optional[dict]) -> str:
    """冒頭 1〜2 行の「今日の結論」を生成"""
    parts = []
    if anomaly_summary:
        cpa = anomaly_summary.get("cpa_change_pct")
        imp = anomaly_summary.get("impression_change_pct")
        cv  = anomaly_summary.get("cv_change_pct")
        anomaly_parts = []
        if cpa is not None and abs(cpa) >= 10:
            sign = "上昇" if cpa > 0 else "改善"
            anomaly_parts.append(f"CPAが{cpa:+.1f}%{sign}")
        if imp is not None and abs(imp) >= 20:
            sign = "増加" if imp > 0 else "低下"
            anomaly_parts.append(f"インプレッション{imp:+.1f}%{sign}")
        if cv is not None and abs(cv) >= 20:
            sign = "増加" if cv > 0 else "減少"
            anomaly_parts.append(f"CV{cv:+.1f}%{sign}")
        if anomaly_parts:
            parts.append("、".join(anomaly_parts) + "が観測されています。")

    if items_today:
        # 主軸のゴールを 1 件目から取得
        first_goal = items_today[0]["goal_stage"]
        goal_intro = {
            "measurement_recovery": "まずは計測不備の確認を優先してください。",
            "cpa_diagnosis":        "まずは CPA 悪化要因の切り分けを優先してください。",
            "delivery_diagnosis":   "まずは配信量低下の原因切り分けを優先してください。",
            "first_party_data":     "1st party data 整備を進めてください。",
            "legal_review":         "広告審査リスクの予防確認をお願いします。",
        }
        parts.append(goal_intro.get(first_goal, "下記の項目をご確認ください。"))
    else:
        parts.append("緊急対応はありません。今週中に確認したい項目を以下にまとめています。")

    return " ".join(parts)
