"""統合 daily TODO ビルダー (5/8 v2 緊急修正 + 5/8 v3 順序ロジック明文化)

責務: Layer A の indications (anomaly / ads_audit / fraud / X-PI1 等) と
      Layer 0-3 の auto_proposal eligible rules を 1 つの統合通知
      「本日の広告成果改善TODO」にまとめる。

設計原則:
    - 1 日 1 通の統合通知に集約 (個別投稿廃止)
    - rule_messaging.yaml 未定義 rule は **顧客向けに出さない** (fallback 禁止)
    - rule_messaging 未定義は internal log に internal_unmapped_rule として記録
    - 上位 3 件は「今日確認」として詳細表示、4 件目以降は「今週中」要約
    - priority A 0 件時は適切なメッセージで案内
    - 冒頭に「今日の結論」を 1〜2 行 (anomaly 由来の数値があれば差し込み)


==============================================================================
表示順スコアの計算 (5/8 v3 明文化)
==============================================================================
各 item の表示順は、以下 5 軸を加算した整数スコア (sort_score) で決まる。
**スコアが小さいほど上位**。同スコアは rule_id 辞書順でタイブレーク。

  sort_score =
      priority_w           # A=0,  B=100, C=200
    + goal_stage_w         # measurement_recovery=1, ..., legal_review=5
    + severity_w           # critical=-30, high=-10, medium=0, low=+10
    + perf_impact_w        # 実データ異常 (CPA 急騰 / 配信量急減 / CV 欠損) と
                           # 相性の良い goal_stage の rule は -50
    + today_action_w       # today_action 文字列を持つ rule は -5
    + already_notified_p   # 過去通知済 rule は +200 (fallback 抑止)

設計意図:
  - **priority_w が間隔 100** で、他軸は 1〜50 の範囲。priority A は基本上位だが、
    「priority B でも CPA 急騰相性 (-50) + critical (-30) + measurement_recovery (1)」
    のように合計が低ければ priority A を抜くこともある。
    これは「実データ異常がある日は法律より計測/切り分けが必ず上に来る」要件
    を満たす設計。
  - **perf_impact_w は anomaly_summary に依存**:
      cpa_change_pct > +30%  → goal_stage in {measurement_recovery, cpa_diagnosis}
                                の rule に -50
      impression_change_pct < -30% → goal_stage in {delivery_diagnosis,
                                       measurement_recovery} の rule に -50
      cv_change_pct < -30%   → goal_stage in {measurement_recovery, cpa_diagnosis}
                                の rule に -50
  - **goal_stage_order** (rule_messaging.yaml で定義):
      measurement_recovery=1 → cpa_diagnosis=2 → delivery_diagnosis=3 →
      first_party_data=4 → legal_review=5
  - **legal_review** 系は priority B + perf_impact 該当なしなら必ず最下層 (補足)

各 item には sort_score / sort_breakdown が記録され、preview スクリプトで
順序の根拠を確認できる (debug 出力)。

主要関数:
    - build_daily_todo(client_id, indications, eligible_rules, anomaly_summary, ...)
      → context dict (テンプレ render 用、sort_score 付き)
    - load_messaging() → rule_messaging.yaml キャッシュロード
    - resolve_rule_message(rule_id) → messaging 取得 (未定義は None)
    - compute_sort_score(item, anomaly_summary, goal_order) → 単一スコア + breakdown
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


def build_recommendation_item(
    rule_id: str, rule_def: dict, msg_def: dict, messaging: dict,
    actual_monthly_spend: Optional[float] = None,
) -> dict:
    """rule_messaging 定義を 1 件分の表示用 dict に整形

    5/7 P1 fix: actual_monthly_spend を渡せば lift_rate ベースで動的算出。
    """
    labels = messaging.get("category_labels") or {}
    perf_keys = msg_def.get("performance_category") or []
    perf_labels = [labels.get(k, k) for k in perf_keys]
    # severity は rule_def (Layer A indication record / Layer 0-3 rule yaml) から拾う
    severity = (rule_def.get("severity") or msg_def.get("severity") or "medium").lower()
    # 5/8 v3 ADR-001 + 5/7 P1: 3 層効果 (lift_rate × 実績 spend or 固定値フォールバック)
    impact_three_layer = _build_impact_three_layer(
        msg_def.get("impact_estimate"), perf_labels,
        actual_monthly_spend=actual_monthly_spend,
    )
    return {
        "rule_id":             rule_id,
        "customer_title":      msg_def.get("customer_title") or rule_def.get("name", rule_id),
        "performance_category_keys":   perf_keys,
        "performance_category_labels": perf_labels,
        "priority":            msg_def.get("priority", "B"),
        "goal_stage":          msg_def.get("goal_stage", "measurement_recovery"),
        "severity":            severity,                   # 5/8 v3: ソート軸用
        "expected_effect":     msg_def.get("expected_effect") or [],
        "today_action":        msg_def.get("today_action") or "",
        "yes_no_question":     msg_def.get("yes_no_question") or "",
        "action_options":      msg_def.get("action_options") or {"A": "対応済", "B": "未対応", "C": "確認したい"},
        "legal_note":          msg_def.get("legal_note"),
        "impact_three_layer":  impact_three_layer,
    }


def _build_impact_three_layer(
    impact_estimate: Optional[dict], perf_labels: list,
    actual_monthly_spend: Optional[float] = None,
) -> dict:
    """ADR-001 三層効果の表示用 dict を構築 (5/7 P1 fix: 実績連動の動的算出)

    Args:
        impact_estimate: rule_messaging.yaml の impact_estimate
                         lift_rate ベース (推奨) or 固定値 (旧 schema、フォールバック)
        perf_labels: 算定不可時の効果区分表示に使う
        actual_monthly_spend: 実績月額広告費 (JPY)。None or 0 なら固定値フォールバック使用。

    impact_estimate スキーマ (新 5/7):
        lift_rate:                # 月額広告費に対する改善率 (主軸)
          minimum:   0.019
          realistic: 0.050
          upper:     0.125
        source_basis: "Meta 公式..."          # 根拠 (preview / 詳細表示用)
        measurement_window:                  # P2: 効果が出るまでの期間
          signal:  "1-2 週"
          verdict: "4 週"
        assumed_monthly_spend: 1600000        # フォールバック用、固定値の前提額
        minimum_fallback:   30000             # actual_monthly_spend 取れない時
        realistic_fallback: 80000
        upper_fallback:     200000

    Returns:
        {
            "calculable": bool,
            "minimum":   int (yen) or None,
            "realistic": int (yen) or None,
            "upper":     int (yen) or None,
            "calc_basis":         "actual_spend" | "fixed_fallback" | "fixed_legacy",
            "monthly_spend_used": int or None,
            "source_basis":       str or None,
            "measurement_window": {"signal", "verdict"} or None,
            "lift_rate":          {minimum, realistic, upper} or None,
            "display": {
                "minimum":   "+¥30,000",
                ...
                "category_label": "計測精度改善 / 配信学習安定化",
                "spend_basis":    "月額広告費 ¥1,409,403 連動算出",  # or "固定値 (¥160万 spend 想定)"
            }
        }
    """
    if not impact_estimate or not isinstance(impact_estimate, dict):
        return {
            "calculable": False,
            "minimum":   None, "realistic": None, "upper": None,
            "calc_basis": None, "monthly_spend_used": None,
            "source_basis": None, "measurement_window": None, "lift_rate": None,
            "display": {
                "category_label": " / ".join(perf_labels) if perf_labels else "—",
            },
        }

    lift_rate = impact_estimate.get("lift_rate")
    source_basis = impact_estimate.get("source_basis")
    measurement_window = impact_estimate.get("measurement_window")

    # 1. lift_rate + actual_monthly_spend → 動的算出 (推奨経路)
    if lift_rate and isinstance(lift_rate, dict) and actual_monthly_spend and actual_monthly_spend > 0:
        minimum   = int(actual_monthly_spend * float(lift_rate.get("minimum", 0)))
        realistic = int(actual_monthly_spend * float(lift_rate.get("realistic", 0)))
        upper     = int(actual_monthly_spend * float(lift_rate.get("upper", 0)))
        calc_basis = "actual_spend"
        spend_basis_label = f"月額広告費 ¥{int(actual_monthly_spend):,} 連動算出"

    # 2. lift_rate あるが actual_monthly_spend 未取得 → assumed_monthly_spend で計算 (固定値)
    elif lift_rate and isinstance(lift_rate, dict):
        assumed = float(impact_estimate.get("assumed_monthly_spend", 1600000))
        minimum   = impact_estimate.get("minimum_fallback")   or int(assumed * float(lift_rate.get("minimum", 0)))
        realistic = impact_estimate.get("realistic_fallback") or int(assumed * float(lift_rate.get("realistic", 0)))
        upper     = impact_estimate.get("upper_fallback")     or int(assumed * float(lift_rate.get("upper", 0)))
        calc_basis = "fixed_fallback"
        spend_basis_label = f"固定値 (月額広告費 ¥{int(assumed):,} 想定)"

    # 3. 旧 schema (lift_rate なし、固定値のみ)
    else:
        minimum   = impact_estimate.get("minimum")
        realistic = impact_estimate.get("realistic")
        upper     = impact_estimate.get("upper") or impact_estimate.get("independent")
        calc_basis = "fixed_legacy"
        spend_basis_label = "固定値 (前提条件未明記)"

    return {
        "calculable": True,
        "minimum":   minimum,
        "realistic": realistic,
        "upper":     upper,
        "calc_basis":         calc_basis,
        "monthly_spend_used": int(actual_monthly_spend) if actual_monthly_spend else None,
        "source_basis":       source_basis,
        "measurement_window": measurement_window,
        "lift_rate":          lift_rate,
        "display": {
            "minimum":      _format_yen(minimum),
            "realistic":    _format_yen(realistic),
            "upper":        _format_yen(upper),
            "category_label": " / ".join(perf_labels) if perf_labels else "—",
            "spend_basis":    spend_basis_label,
            "window_signal":  (measurement_window or {}).get("signal", ""),
            "window_verdict": (measurement_window or {}).get("verdict", ""),
        },
    }


def _format_yen(amount) -> str:
    """月額 JPY を「+¥30,000」形式に整形"""
    if amount is None:
        return "—"
    try:
        return f"+¥{int(amount):,}"
    except (ValueError, TypeError):
        return "—"


# ========== 5/8 v3 表示順スコア計算 ==========

PRIORITY_WEIGHTS = {"A": 0, "B": 100, "C": 200}

SEVERITY_WEIGHTS = {
    "critical": -30,
    "high":     -10,
    "medium":     0,
    "low":      +10,
}

# 実データ異常 (anomaly) と相性の良い goal_stage の rule に下駄
PERF_IMPACT_BOOST_PER_HIT = -50

# anomaly トリガー閾値 (絶対値 %)
ANOMALY_THRESHOLD_PCT = 30.0

# 各 anomaly が「成果を引き上げる対象」goal_stage の集合
ANOMALY_TO_BOOSTED_GOALS = {
    "cpa":        {"measurement_recovery", "cpa_diagnosis"},
    "impression": {"delivery_diagnosis", "measurement_recovery"},
    "cv":         {"measurement_recovery", "cpa_diagnosis"},
}


def compute_sort_score(
    item: dict, anomaly_summary: Optional[dict], goal_order: dict,
    already_notified_ids: Optional[set] = None,
) -> dict:
    """1 件の item の表示順スコアと内訳を返す

    Returns:
        {"score": int, "breakdown": {priority, goal_stage, severity, perf_impact,
                                     today_action, already_notified}}
    """
    already = already_notified_ids or set()
    breakdown: dict = {}

    breakdown["priority"]   = PRIORITY_WEIGHTS.get(item.get("priority", "B"), 999)
    breakdown["goal_stage"] = goal_order.get(item.get("goal_stage", "measurement_recovery"), 99)
    breakdown["severity"]   = SEVERITY_WEIGHTS.get((item.get("severity") or "medium").lower(), 0)

    # perf_impact: anomaly summary に該当する goal_stage の rule を引き上げる
    perf_impact = 0
    if anomaly_summary:
        item_goal = item.get("goal_stage")
        cpa_pct = anomaly_summary.get("cpa_change_pct")
        imp_pct = anomaly_summary.get("impression_change_pct")
        cv_pct  = anomaly_summary.get("cv_change_pct")
        if cpa_pct is not None and cpa_pct >= ANOMALY_THRESHOLD_PCT \
                and item_goal in ANOMALY_TO_BOOSTED_GOALS["cpa"]:
            perf_impact += PERF_IMPACT_BOOST_PER_HIT
        if imp_pct is not None and imp_pct <= -ANOMALY_THRESHOLD_PCT \
                and item_goal in ANOMALY_TO_BOOSTED_GOALS["impression"]:
            perf_impact += PERF_IMPACT_BOOST_PER_HIT
        if cv_pct is not None and cv_pct <= -ANOMALY_THRESHOLD_PCT \
                and item_goal in ANOMALY_TO_BOOSTED_GOALS["cv"]:
            perf_impact += PERF_IMPACT_BOOST_PER_HIT
    breakdown["perf_impact"] = perf_impact

    breakdown["today_action"] = -5 if item.get("today_action") else 0
    breakdown["already_notified"] = +200 if item.get("rule_id") in already else 0

    score = sum(breakdown.values())
    return {"score": score, "breakdown": breakdown}


def collect_already_notified_rule_ids(
    client_id: str, today_str: str,
) -> set:
    """当日中に既通知の rule_id 集合を返す (auto_proposal_history + indication_state)

    5/8 v3 fix: build_daily_todo で compute_sort_score の already_notified_p 軸を
    実際に動作させるための集合。cooldown / cap で eligible 段階で弾かれるのが
    第一防御だが、二重防御 + preview のデバッグ性向上のため、本文表示時にも
    既通知 rule をスコアで下位に押し下げる。

    Returns:
        既通知 rule_id の set (空 set もあり得る)
    """
    out: set = set()

    # 1. auto_proposal_history: today_str に sent された rule_id
    try:
        from engine.auto_proposal_engine import _load_history
        history = _load_history(client_id) or {}
        for rule_id, rec in history.items():
            if (rec or {}).get("last_sent_date") == today_str:
                out.add(rule_id)
    except Exception as e:
        log.debug(f"[{client_id}] _load_history 失敗 (already_notified 集計): {e}")

    # 2. indication_state: notified_date が today_str の rule_id
    try:
        from engine.indication_state import IndicationState
        state = IndicationState(client_id=client_id)
        for rec in state.list_open_or_pending():
            if rec.get("notified_date") == today_str:
                rid = rec.get("rule_id")
                if rid:
                    out.add(rid)
    except Exception as e:
        log.debug(f"[{client_id}] indication_state 失敗 (already_notified 集計): {e}")

    return out


def build_daily_todo(
    client_id: str,
    client_cfg: dict,
    layer_a_rule_ids: list[str],          # indication_state 由来 (rule_id list)
    eligible_rules: list[dict],           # auto_proposal_engine の eligible (rule full dicts)
    layer_a_rule_defs: Optional[dict] = None,   # rule_id → rule full dict (X-PI1 等の定義)
    anomaly_summary: Optional[dict] = None,
    today_str: Optional[str] = None,
    already_notified_ids: Optional[set] = None,
    actual_monthly_spend: Optional[float] = None,   # 5/7 P1: 実績月額広告費連動の動的算出
    audit_results: Optional[dict] = None,           # Meta API evidence / answer resolver context
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
        already_notified_ids: 当日既通知済の rule_id 集合 (5/8 v3 fix: compute_sort_score
            の already_notified_p 軸を実動作させる用)。None の場合 collect_already_notified_rule_ids
            から取得 (テストや preview で空 set を渡せばこの軸を 0 にできる)

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

    # === 0. ChatWork 回答ストアから suppression / wants_help / not_done 状態を取得 (5/8 v3) ===
    # confirmed_done / not_applicable な rule_id は本文から除外、
    # wants_help は customer_title に "[詳細案内]" プレフィクス、
    # not_done は最近 reminder したばかりなら抑制 (cooldown は不要、status が短期 expires)
    response_status_map: dict = {}
    suppressed_by_response: set = set()
    try:
        from engine.chatwork_response_store import get_status_map, is_suppressed
        response_status_map = get_status_map(client_id)
        suppressed_by_response = {
            rid for rid in response_status_map
            if is_suppressed(client_id, rid)
        }
        if suppressed_by_response:
            log.info(
                f"[{client_id}] response-suppressed rules ({len(suppressed_by_response)}): "
                f"{', '.join(sorted(suppressed_by_response))}"
            )
    except Exception as e:
        log.debug(f"[{client_id}] response store load failed (no-op): {e}")

    # === 0b. answer_source_preference 解決 (5/8 v3 P1-B 修正) ===
    # 各 rule の answer_source_preference に従って api / validator / chatwork_reply
    # の順で答えを取得試行。上位 source で resolved=True なら顧客質問抑制。
    # 現状: api 経路は Phase B で実装予定の stub、validator は限定的、
    # chatwork_reply は response_status_map と同じ判定 (confirmed_done/not_applicable)
    # → 効果は主に「明示的な解決パスの宣言」と「Phase B で API 連携時に質問抑制が
    # 自動的に効く」枠組み作り。
    suppressed_by_resolver: set = set()
    resolver_reasons: dict = {}
    try:
        from engine.answer_resolver import should_suppress_question
        for rid, msg_def in (messaging.get("rules") or {}).items():
            if not (msg_def or {}).get("answer_source_preference"):
                continue
            should_suppress, reason = should_suppress_question(
                client_id, rid, msg_def, api_context=audit_results,
            )
            if should_suppress:
                suppressed_by_resolver.add(rid)
                resolver_reasons[rid] = reason
        if suppressed_by_resolver:
            log.info(
                f"[{client_id}] resolver-suppressed rules "
                f"({len(suppressed_by_resolver)}): "
                f"{', '.join(sorted(suppressed_by_resolver))}"
            )
    except Exception as e:
        log.debug(f"[{client_id}] answer_resolver load failed (no-op): {e}")

    # 統合: response store と answer_resolver の両方で除外されるべき rule_id を結合
    suppressed_total = suppressed_by_response | suppressed_by_resolver

    # === 1. Layer A indications を items に変換 ===
    layer_a_items: list[dict] = []
    unmapped: list[str] = []
    suppressed_count = 0
    for rid in layer_a_rule_ids:
        if rid in suppressed_total:
            suppressed_count += 1
            continue
        msg_def = (messaging.get("rules") or {}).get(rid)
        if not msg_def:
            unmapped.append(rid)
            continue
        rule_def = layer_a_rule_defs.get(rid) or {"id": rid}
        item = build_recommendation_item(
            rid, rule_def, msg_def, messaging,
            actual_monthly_spend=actual_monthly_spend,
        )
        item["response_status"] = response_status_map.get(rid)
        if item["response_status"] == "wants_help":
            item["customer_title"] = f"[詳細案内] {item['customer_title']}"
        layer_a_items.append(item)

    # === 2. auto_proposal eligible rules を items に変換 ===
    auto_items: list[dict] = []
    for r in eligible_rules:
        rid = r.get("id", "")
        if rid in suppressed_total:
            suppressed_count += 1
            continue
        msg_def = (messaging.get("rules") or {}).get(rid)
        if not msg_def:
            unmapped.append(rid)
            continue
        item = build_recommendation_item(
            rid, r, msg_def, messaging,
            actual_monthly_spend=actual_monthly_spend,
        )
        item["response_status"] = response_status_map.get(rid)
        if item["response_status"] == "wants_help":
            item["customer_title"] = f"[詳細案内] {item['customer_title']}"
        auto_items.append(item)

    # === 3. 統合 + 多軸スコア順ソート (5/8 v3 順序ロジック明文化) ===
    all_items = layer_a_items + auto_items
    goal_order = messaging.get("goal_stage_order") or {}

    # 5/8 v3 fix: already_notified_ids が None なら collect_already_notified_rule_ids で取得
    # テスト等で空 set を明示的に渡せばこの軸を 0 に固定できる
    if already_notified_ids is None and today_str:
        already_notified_ids = collect_already_notified_rule_ids(client_id, today_str)
    elif already_notified_ids is None:
        already_notified_ids = set()

    # 各 item に sort_score / sort_breakdown を付与
    for it in all_items:
        scored = compute_sort_score(
            it, anomaly_summary, goal_order,
            already_notified_ids=already_notified_ids,
        )
        it["sort_score"]     = scored["score"]
        it["sort_breakdown"] = scored["breakdown"]

    # スコア小さい順、タイブレーク = rule_id 辞書順
    all_items.sort(key=lambda it: (it["sort_score"], it["rule_id"]))

    # === 4. items_today / items_this_week / items_legal_note に分割 ===
    # 「今日確認」: スコア順上位 DETAILED_TOP_N 件 (priority A だけに限定しない)
    # 「補足」:    legal_review 系で perf_impact が無いもの (= 法令・プライバシー)
    # 「今週中」: それ以外
    items_today = []
    items_this_week = []
    items_legal_note = []

    for it in all_items:
        # 「今日確認」上位 N 件: スコア閾値ではなく、上位 N 件で詳細表示
        # ただし legal_review の rule で perf_impact が無いものは「補足」へ強制
        is_legal_only = (
            it["goal_stage"] == "legal_review"
            and it["sort_breakdown"].get("perf_impact", 0) == 0
        )
        if is_legal_only:
            items_legal_note.append(it)
        elif len(items_today) < DETAILED_TOP_N:
            items_today.append(it)
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
        "to_line":      _build_chatwork_to_line(client_cfg),
        "headline":     headline,
        "items_today":         items_today,
        "items_this_week":     items_this_week,
        "items_legal_note":    items_legal_note,
        "total_count":         len(items_today) + len(items_this_week) + len(items_legal_note),
        "displayed_rule_ids":  [i["rule_id"] for i in items_today + items_this_week + items_legal_note],
        "internal_unmapped_rules": sorted(set(unmapped)),
        "anomaly_summary":     anomaly_summary or {},
        "already_notified_ids": sorted(already_notified_ids),
        # 5/8 v3 ingestion: response store + answer_resolver による除外件数 (preview 用)
        "suppressed_by_response_count": suppressed_count,
        "suppressed_by_response_ids":   sorted(suppressed_by_response),
        "suppressed_by_resolver_ids":   sorted(suppressed_by_resolver),
        "resolver_reasons":             resolver_reasons,
        "response_status_map":          response_status_map,
    }


# ========== Private ==========

def _build_chatwork_to_line(client_cfg: dict, channel: str = "daily_todo") -> str:
    """clients.yaml の chatwork_mentions から ChatWork TO 行を作る。

    Supported config:
        chatwork_mentions:
          daily_todo:
            - account_id: 123
              name: "山田 太郎"
          default: [...]

    ChatWork の宛先指定は `[To:account_id]名前さん` を本文先頭に置く。
    未設定なら空文字を返し、既存通知には影響しない。
    """
    mentions_cfg = client_cfg.get("chatwork_mentions") or {}
    if isinstance(mentions_cfg, dict):
        mentions = mentions_cfg.get(channel) or mentions_cfg.get("default") or []
    elif isinstance(mentions_cfg, list):
        mentions = mentions_cfg
    else:
        mentions = []

    lines: list[str] = []
    for mention in mentions:
        if isinstance(mention, dict):
            account_id = mention.get("account_id") or mention.get("id")
            name = str(mention.get("name") or "").strip()
        else:
            account_id = mention
            name = ""
        if not account_id:
            continue

        display = name
        if display and not display.endswith(("さん", "様")):
            display = f"{display}さん"
        lines.append(f"[To:{account_id}]{display}")

    return "\n".join(lines)


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

    # 5/7 P1: 効果額の動的算出用に actual_monthly_spend を抽出
    actual_monthly_spend = _extract_actual_monthly_spend(audit_results)

    # 4. 統合 context 構築
    context = build_daily_todo(
        client_id=client_id,
        client_cfg=client_cfg,
        layer_a_rule_ids=layer_a_rule_ids,
        eligible_rules=selected_rules,
        layer_a_rule_defs=layer_a_rule_defs,
        anomaly_summary=anomaly_summary,
        today_str=today_str,
        actual_monthly_spend=actual_monthly_spend,
        audit_results=audit_results,
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
        if result.get("message_id"):
            _save_reply_context_for_todo(client_id, today_str, context, result)
        summary["auto_proposal_skipped"] = len(selected_rules)
        log.info(f"[{client_id}] 統合 TODO スキップ (idempotency hit)")
        return summary

    # 7. 本番送信成功時のみ state を進める
    _save_reply_context_for_todo(client_id, today_str, context, result)

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


def _save_reply_context_for_todo(client_id: str, today_str: str, context: dict, result: dict) -> None:
    """統合TODOの表示順を、1文字返信マッピング用に保存する。"""
    try:
        from engine.chatwork_reply_context_store import save_latest_context
        save_latest_context(client_id, {
            "message_id": result.get("message_id"),
            "today": today_str,
            "displayed_rule_ids": context.get("displayed_rule_ids") or [],
        })
    except Exception as e:
        log.warning(f"[{client_id}] reply context 保存失敗: {e}")


def _extract_actual_monthly_spend(audit_results: Optional[dict]) -> Optional[float]:
    """audit_results から「月額相当の actual 広告費」を取り出す (5/7 P1)

    pilotton の lookback_days=30 を前提とし、ads_audit.total_cost をそのまま返す。
    将来 lookback が異なるクライアントに広げる場合はここで日数正規化する。

    Returns: 月額換算 spend (JPY)、取得不能なら None。None なら lift_rate 経路は
             固定値 fallback (assumed_monthly_spend ベース) に切替。
    """
    if not audit_results or not audit_results.get("data_available"):
        return None
    ads = audit_results.get("ads_audit") or {}
    cost = ads.get("total_cost")
    try:
        cost = float(cost or 0)
    except (TypeError, ValueError):
        return None
    if cost <= 0:
        return None
    return cost


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
            "measurement_recovery": "まずは計測精度・設定状況の確認を優先してください。",
            "cpa_diagnosis":        "まずは CPA 悪化要因の切り分けを優先してください。",
            "delivery_diagnosis":   "まずは配信量低下の原因切り分けを優先してください。",
            "first_party_data":     "1st party data 整備を進めてください。",
            "legal_review":         "広告審査リスクの予防確認をお願いします。",
        }
        parts.append(goal_intro.get(first_goal, "下記の項目をご確認ください。"))
    else:
        parts.append("緊急対応はありません。今週中に確認したい項目を以下にまとめています。")

    return " ".join(parts)
