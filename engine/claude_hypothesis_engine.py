"""Claude-backed hypothesis engine for resolved anomaly follow-ups.

急変アラートが 3 日連続で再発しなくなっても、CPA や配信量の水準が
悪化後のままなら「改善完了」ではない。この module は:

1. data/{client}_{date}.json から悪化前/直近の campaign 指標を比較
2. 症状に応じて YAML ルール候補を抽出
3. Claude API で仮説順位づけを生成
4. API 不可時も同じ JSON 形のフォールバックを返す

Claude は意思決定者ではなく、YAML ルールに基づく仮説整理係として使う。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from engine.claude_insights import ClaudeInsights

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
META_RULES_PATH = ROOT / "config" / "rules" / "meta_rules.yaml"

ANOMALY_RULE_IDS = {"ANO_CPA_SPIKE", "ANO_IMPRESSION_DROP"}

DEFAULT_RULE_IDS = {
    "learning_reset": ["M68", "M13", "M45"],
    "creative_fatigue": ["M57"],
    "audience_overlap": ["M49", "M52", "M61"],
    "measurement_quality": ["M02", "M03", "M04", "M06"],
}


def build_anomaly_followup(
    client_id: str,
    record: dict,
    today_str: str,
    data_dir: Path = DATA_DIR,
) -> Optional[dict]:
    """resolved anomaly が本当に完了か、継続課題かを判定して仮説を返す。

    Returns None when the record is not an anomaly or enough data is unavailable.
    """
    rule_id = record.get("rule_id")
    if rule_id not in ANOMALY_RULE_IDS:
        return None

    payload = record.get("payload") or {}
    metric_type = _metric_type(rule_id, payload)
    if metric_type not in {"cpa", "impressions"}:
        return None

    current = _load_data(client_id, today_str, data_dir)
    if not current:
        return None

    baseline_date = _resolve_baseline_date(client_id, record, metric_type, data_dir)
    if not baseline_date:
        return None
    baseline = _load_data(client_id, baseline_date, data_dir)
    if not baseline:
        return None

    account = _compare_account(metric_type, baseline, current)
    campaigns = _compare_campaigns(baseline, current)
    if not account or not campaigns:
        return None

    still_bad = _is_still_bad(metric_type, account)
    candidate_rules = _candidate_rules_for_symptoms(campaigns, metric_type)
    rule_defs = _load_rule_defs(candidate_rules)

    payload_for_llm = {
        "client_id": client_id,
        "anomaly": {
            "rule_id": rule_id,
            "metric_type": metric_type,
            "first_detected_date": record.get("first_detected_date"),
            "resolved_date": record.get("resolved_date") or today_str,
            "baseline_date": baseline_date,
            "latest_date": today_str,
            "original_fact": payload.get("fact") or payload.get("title"),
        },
        "account_metric": account,
        "campaign_metrics": campaigns[:5],
        "candidate_yaml_rules": rule_defs,
    }

    if not still_bad:
        return {
            "type": "completed",
            "summary": "急変アラートは終了し、直近水準も悪化前に近い状態へ戻っています。",
            "baseline_date": baseline_date,
            "latest_date": today_str,
            "account_metric": account,
            "campaign_metrics": campaigns[:5],
            "hypotheses": [],
            "customer_question": "",
            "source": "deterministic",
        }

    claude_result = _ask_claude(client_id, payload_for_llm)
    result = claude_result or _fallback_hypotheses(payload_for_llm)
    result.update({
        "type": "continued_issue",
        "baseline_date": baseline_date,
        "latest_date": today_str,
        "account_metric": account,
        "campaign_metrics": campaigns[:5],
        "source": "claude" if claude_result else "fallback",
    })
    return result


def build_current_todo_hypotheses(
    client_id: str,
    audit_results: dict,
    rule_ids: list[str],
    client_config: dict | None = None,
) -> dict[str, dict]:
    """Build hypotheses for today's TODO items from current Meta diagnostics.

    This is used before completion, not only after an anomaly clears. It keeps
    the product promise explicit: improve CPA while preserving CV volume.
    """
    if not audit_results or not rule_ids:
        return {}

    diagnostics = (
        (audit_results.get("platform_diagnostics") or {})
        .get("meta", {})
        .get("performance_diagnostics", {})
    )
    if not diagnostics:
        evidence = (
            (audit_results.get("platform_diagnostics") or {})
            .get("meta", {})
            .get("rule_evidence", {})
        )
        for ev in evidence.values():
            val = ev.get("value") if isinstance(ev, dict) else {}
            if isinstance(val, dict) and val.get("performance_diagnostics"):
                diagnostics = val.get("performance_diagnostics") or {}
                break
    if not diagnostics:
        return {}

    rule_defs = _load_rule_defs([rid for rid in rule_ids if rid.startswith("M")])
    vertical_context = _vertical_context(client_id, client_config)
    payload = {
        "client_id": client_id,
        "goal": "CV数を落とさずCPAを改善する",
        "constraints": [
            "キャンペーン停止だけを結論にしない",
            "CVが出ている配信単位は根拠なく止めない",
            "まず計測・学習・配信制約・配置品質を切り分ける",
        ],
        "rule_ids": rule_ids,
        "candidate_yaml_rules": rule_defs,
        "vertical_context": vertical_context,
        "performance_diagnostics": diagnostics,
        "anomaly_summary": audit_results.get("anomalies") or {},
    }

    claude_result = _ask_claude_current(client_id, payload)
    if not claude_result:
        claude_result = _fallback_current_hypotheses(payload)

    by_rule = {}
    for rid in rule_ids:
        by_rule[rid] = {
            "summary": claude_result.get("summary"),
            "check_order": claude_result.get("check_order") or [],
            "hypotheses": claude_result.get("hypotheses") or [],
            "do_not_do": claude_result.get("do_not_do") or [],
            "vertical_context": vertical_context,
            "source": claude_result.get("source", "fallback"),
        }
    return by_rule


def _vertical_context(client_id: str, client_config: dict | None) -> dict:
    """Attach industry KPI semantics so hypotheses do not optimize the wrong CV."""
    try:
        from engine.vertical_kpi_registry import build_client_kpi_readiness
        if client_config is None:
            client_config = _load_client_config(client_id)
        readiness = build_client_kpi_readiness(client_id, client_config or {})
        return {
            "vertical_id": readiness.get("vertical_id"),
            "primary_goal": readiness.get("primary_goal"),
            "required_events": readiness.get("required_events") or [],
            "economic_metrics": readiness.get("economic_metrics") or {},
            "quality_dimensions": readiness.get("quality_dimensions") or [],
            "notification_focus": readiness.get("notification_focus") or [],
            "rule_focus": readiness.get("rule_focus") or {},
            "ready_for_high_confidence_recommendations": readiness.get("ready_for_high_confidence_recommendations"),
            "required_missing": readiness.get("required_missing") or [],
            "recommended_missing": readiness.get("recommended_missing") or [],
        }
    except Exception:
        return {"vertical_id": "unknown", "client_id": client_id}


def _load_client_config(client_id: str) -> dict:
    path = ROOT / "config" / "clients.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    clients = raw.get("clients") if isinstance(raw.get("clients"), dict) else {}
    return clients.get(client_id) or {}


def _metric_type(rule_id: str, payload: dict) -> str:
    metric = str(payload.get("metric") or "").lower()
    if "cpa" in metric or rule_id == "ANO_CPA_SPIKE":
        return "cpa"
    if "impression" in metric or rule_id == "ANO_IMPRESSION_DROP":
        return "impressions"
    return metric


def _load_data(client_id: str, date_str: str, data_dir: Path) -> Optional[dict]:
    path = data_dir / f"{client_id}_{date_str}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"failed to load campaign data: {path}: {e}")
        return None


def _resolve_baseline_date(client_id: str, record: dict, metric_type: str, data_dir: Path) -> Optional[str]:
    payload = record.get("payload") or {}
    before_value = _extract_before_value(payload.get("fact") or payload.get("title") or "", metric_type)
    first = record.get("first_detected_date") or record.get("last_detected_date")
    candidates: list[str] = []
    if first:
        try:
            dt = datetime.fromisoformat(first).date()
            candidates = [(dt - timedelta(days=i)).isoformat() for i in range(0, 8)]
        except ValueError:
            candidates = []
    # Include all local files as fallback. Pick closest to before_value if available.
    for p in sorted(data_dir.glob(f"{client_id}_20*.json")):
        date = p.stem.replace(f"{client_id}_", "")
        if date not in candidates:
            candidates.append(date)

    if before_value is None:
        return candidates[1] if len(candidates) > 1 else (candidates[0] if candidates else None)

    best: tuple[float, str] | None = None
    for date in candidates:
        data = _load_data(client_id, date, data_dir)
        if not data:
            continue
        value = _account_value(metric_type, data)
        if value is None:
            continue
        diff = abs(value - before_value) / max(before_value, 1)
        if best is None or diff < best[0]:
            best = (diff, date)
    return best[1] if best else None


def _extract_before_value(text: str, metric_type: str) -> Optional[float]:
    if metric_type == "cpa":
        m = re.search(r"¥\s*([0-9,]+)\s*→", text)
    else:
        m = re.search(r"\(([0-9,.]+)\s*→", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _account_value(metric_type: str, data: dict) -> Optional[float]:
    totals = data.get("totals") or {}
    if metric_type == "cpa":
        value = totals.get("avg_cpa")
        if value is not None:
            return float(value)
        cost = totals.get("total_cost")
        cv = totals.get("total_conversions")
        return float(cost) / float(cv) if cost and cv else None
    value = totals.get("total_impressions")
    return float(value) if value is not None else None


def _compare_account(metric_type: str, baseline: dict, current: dict) -> Optional[dict]:
    before = _account_value(metric_type, baseline)
    after = _account_value(metric_type, current)
    if before is None or after is None:
        return None
    change = (after - before) / before * 100 if before else 0
    return {
        "metric": metric_type,
        "baseline": round(before, 2),
        "latest": round(after, 2),
        "change_pct": round(change, 1),
    }


def _compare_campaigns(baseline: dict, current: dict) -> list[dict]:
    base_by = {
        c.get("campaign_id") or c.get("campaign"): c
        for c in baseline.get("campaigns", [])
    }
    rows = []
    for cur in current.get("campaigns", []):
        key = cur.get("campaign_id") or cur.get("campaign")
        base = base_by.get(key)
        if not base:
            continue
        b_cpa = _campaign_cpa(base)
        c_cpa = _campaign_cpa(cur)
        b_imp = _num(base.get("impressions"))
        c_imp = _num(cur.get("impressions"))
        if b_cpa is None or c_cpa is None or b_imp is None or c_imp is None:
            continue
        cpa_change = (c_cpa - b_cpa) / b_cpa * 100 if b_cpa else 0
        imp_change = (c_imp - b_imp) / b_imp * 100 if b_imp else 0
        row = {
            "campaign": cur.get("campaign") or key,
            "campaign_id": cur.get("campaign_id") or "",
            "baseline_cpa": round(b_cpa),
            "latest_cpa": round(c_cpa),
            "cpa_change_pct": round(cpa_change, 1),
            "baseline_impressions": round(b_imp),
            "latest_impressions": round(c_imp),
            "impression_change_pct": round(imp_change, 1),
            "baseline_cost": round(_num(base.get("cost")) or 0),
            "latest_cost": round(_num(cur.get("cost")) or 0),
            "baseline_conversions": round(_num(base.get("conversions")) or 0),
            "latest_conversions": round(_num(cur.get("conversions")) or 0),
            "frequency": cur.get("frequency"),
            "ctr": cur.get("ctr"),
            "status": cur.get("status"),
            "learning_phase": cur.get("learning_phase"),
        }
        row["severity_score"] = max(row["cpa_change_pct"], 0) + max(-row["impression_change_pct"], 0)
        rows.append(row)
    rows.sort(key=lambda r: r["severity_score"], reverse=True)
    return rows


def _campaign_cpa(campaign: dict) -> Optional[float]:
    value = campaign.get("cpa")
    if value is not None:
        return float(value)
    cost = _num(campaign.get("cost"))
    cv = _num(campaign.get("conversions"))
    return cost / cv if cost and cv else None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_still_bad(metric_type: str, account: dict) -> bool:
    change = account.get("change_pct", 0)
    if metric_type == "cpa":
        return change >= 20
    if metric_type == "impressions":
        return change <= -20
    return False


def _candidate_rules_for_symptoms(campaigns: list[dict], metric_type: str) -> list[str]:
    ids: list[str] = []
    worst = campaigns[0] if campaigns else {}
    if metric_type == "cpa":
        ids.extend(DEFAULT_RULE_IDS["learning_reset"])
        ids.extend(DEFAULT_RULE_IDS["creative_fatigue"])
        ids.extend(DEFAULT_RULE_IDS["audience_overlap"])
        ids.extend(DEFAULT_RULE_IDS["measurement_quality"])
    else:
        ids.extend(DEFAULT_RULE_IDS["learning_reset"])
        ids.extend(["M45", "M52"])
        ids.extend(DEFAULT_RULE_IDS["audience_overlap"])
    if (worst.get("impression_change_pct") or 0) <= -30:
        ids.extend(["M68", "M45", "M52"])
    if (worst.get("cpa_change_pct") or 0) >= 50:
        ids.extend(["M57", "M49", "M03"])
    return list(dict.fromkeys(ids))


def _load_rule_defs(rule_ids: list[str]) -> list[dict]:
    try:
        data = yaml.safe_load(META_RULES_PATH.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        data = {}
    by_id = {r.get("id"): r for r in data.get("rules", [])}
    out = []
    for rid in rule_ids:
        r = by_id.get(rid)
        if not r:
            continue
        impact = r.get("expected_impact") or {}
        out.append({
            "id": rid,
            "name": r.get("name"),
            "category": r.get("category"),
            "severity": r.get("severity"),
            "root_cause_group": r.get("root_cause_group"),
            "rationale": str(impact.get("rationale") or r.get("redesign_note") or "")[:260],
            "implementation_steps": (r.get("implementation_steps") or [])[:3],
        })
    return out


def _ask_claude(client_id: str, payload: dict) -> Optional[dict]:
    insights = ClaudeInsights(client_id)
    if not insights.api_available:
        return None
    prompt = f"""以下の広告異常フォローアップについて、YAMLルール候補に基づいて仮説を順位づけしてください。

制約:
- 候補にない rule_id を作らない
- キャンペーン停止/予算削減だけを結論にしない
- 顧客に聞く質問は、APIで取れない「運用変更の有無」に絞る
- 出力は JSON のみ

入力:
{json.dumps(payload, ensure_ascii=False, indent=2)}

出力フォーマット:
{{
  "summary": "80字以内。急変終了だが水準が戻っていない、のように表現",
  "hypotheses": [
    {{
      "rank": 1,
      "rule_id": "M68",
      "rule_name": "学習リセット要因イベント検出",
      "hypothesis": "仮説",
      "evidence": "実データに基づく根拠",
      "next_action": "次に見ること"
    }}
  ],
  "customer_question": "A/B/Cで答えられる質問",
  "answer_options": {{
    "A": "予算・入札・ターゲットを変更した",
    "B": "広告素材・LP・CVイベントを変更した",
    "C": "特に変更していない / 不明"
  }}
}}"""
    result = insights._invoke("anomaly_followup_hypotheses", prompt, max_tokens=1400)
    if not result or not isinstance(result.get("hypotheses"), list):
        return None
    return result


def _ask_claude_current(client_id: str, payload: dict) -> Optional[dict]:
    insights = ClaudeInsights(client_id)
    if not insights.api_available:
        return None
    prompt = f"""広告運用TODOの補足仮説を、YAMLルールとMeta実データに基づいて生成してください。

目的:
- CV数を落とさずCPAを改善する
- キャンペーン停止や予算削減だけを提案しない
- 顧客向けには、確認順・根拠・やってはいけないことを短く出す

制約:
- 候補にない rule_id を作らない
- CPA改善だけでCV減少を招く施策は避ける
- 出力はJSONのみ

入力:
{json.dumps(payload, ensure_ascii=False, indent=2)}

出力:
{{
  "summary": "80字以内",
  "check_order": ["最初に確認すること", "次に確認すること"],
  "hypotheses": [
    {{
      "rule_id": "M68",
      "hypothesis": "仮説",
      "evidence": "Meta実データに基づく根拠",
      "next_action": "次に見ること"
    }}
  ],
  "do_not_do": ["CVが出ているキャンペーンを根拠なく停止しない"]
}}"""
    result = insights._invoke("current_todo_hypotheses", prompt, max_tokens=1400)
    if not result or not isinstance(result.get("hypotheses"), list):
        return None
    result["source"] = "claude"
    return result


def _fallback_current_hypotheses(payload: dict) -> dict:
    diagnostics = payload.get("performance_diagnostics") or {}
    worst_campaigns = diagnostics.get("campaigns") or []
    worst_adsets = diagnostics.get("adsets") or []
    worst_ads = diagnostics.get("ads") or []
    worst_placements = diagnostics.get("placements") or []
    top = (worst_campaigns or worst_adsets or worst_ads or worst_placements or [{}])[0]

    hypotheses = []
    if worst_placements:
        hypotheses.append({
            "rule_id": "M39",
            "hypothesis": "配置品質の低い面にコストが寄っている可能性があります。",
            "evidence": _segment_evidence(worst_placements[0]),
            "next_action": "Audience Network等の配置別CPA/CVを確認し、CVが弱い面だけ除外候補にしてください。",
        })
    if worst_adsets:
        hypotheses.append({
            "rule_id": "M52",
            "hypothesis": "広告セット単位で配信制約またはターゲット偏りが起きている可能性があります。",
            "evidence": _segment_evidence(worst_adsets[0]),
            "next_action": "広告セット別にCV数・CPA・frequencyを比較してください。",
        })
    if worst_ads:
        hypotheses.append({
            "rule_id": "M57",
            "hypothesis": "一部広告の反応低下がCPAを押し上げている可能性があります。",
            "evidence": _segment_evidence(worst_ads[0]),
            "next_action": "CVが出ていない広告のみ差し替え候補にし、CVが出ている広告は維持してください。",
        })
    if not hypotheses and top:
        hypotheses.append({
            "rule_id": "M68",
            "hypothesis": "学習リセットや設定変更で効率が不安定になっている可能性があります。",
            "evidence": _segment_evidence(top),
            "next_action": "直近7日以内の予算・入札・CVイベント・ターゲット変更履歴を確認してください。",
        })

    return {
        "summary": "CPA改善は、停止ではなく計測・配置・広告セット単位でCVを守りながら切り分けます。",
        "check_order": [
            "計測/CAPI/dedupが正常か確認",
            "CPAが高い配信単位でもCVが出ているか確認",
            "CVが弱い配置・広告だけを見直し候補にする",
        ],
        "hypotheses": hypotheses[:3],
        "do_not_do": [
            "CVが出ているキャンペーンをCPAだけで停止しない",
            "計測不備が残る状態で配信判断を確定しない",
        ],
        "source": "fallback",
    }


def _segment_evidence(row: dict) -> str:
    name = row.get("name") or row.get("campaign") or row.get("id") or "対象配信単位"
    cpa = row.get("cpa")
    cv = row.get("conversions")
    cost = row.get("cost")
    return f"{name}: cost={cost}, CV={cv}, CPA={cpa}"


def _fallback_hypotheses(payload: dict) -> dict:
    campaigns = payload.get("campaign_metrics") or []
    rules = {r["id"]: r for r in payload.get("candidate_yaml_rules") or []}
    worst = campaigns[0] if campaigns else {}
    summary = "急変アラートは終了しましたが、CPA/配信量の水準は悪化後の状態が残っています。"
    preferred = ["M68", "M57", "M49"]
    if (worst.get("impression_change_pct") or 0) <= -30:
        preferred = ["M68", "M45", "M52"]
    if (worst.get("cpa_change_pct") or 0) >= 80:
        preferred = ["M68", "M57", "M49"]

    hypotheses = []
    text_by_rule = {
        "M68": ("5/5前後の予算・ターゲット・CVイベント変更で学習が再起動した可能性があります。", "CPA悪化と配信量低下が同時に出ているため、学習リセットを優先確認します。"),
        "M57": ("クリエイティブ反応の低下や疲弊でCPAが高止まりしている可能性があります。", "CPA悪化が大きく、配信効率の低下が見えます。"),
        "M49": ("類似/詳細/ASC間の重複で自社競合が起きている可能性があります。", "同時期に複数キャンペーンでCPAが上昇しています。"),
        "M45": ("入札上限やコスト制御が配信量を抑制している可能性があります。", "インプレッション低下が大きく、配信制約の確認が必要です。"),
        "M52": ("ターゲットが狭すぎる/広すぎることで学習効率が落ちている可能性があります。", "配信量低下とCPA高止まりが同時に残っています。"),
    }
    for rid in preferred:
        if rid not in rules:
            continue
        h, e = text_by_rule.get(rid, ("YAMLルール上の確認余地があります。", "実データ上の悪化が残っています。"))
        hypotheses.append({
            "rank": len(hypotheses) + 1,
            "rule_id": rid,
            "rule_name": rules[rid].get("name"),
            "hypothesis": h,
            "evidence": e,
            "next_action": "該当ルールの確認項目に沿って、設定変更履歴と現在の配信状態を確認してください。",
        })

    return {
        "summary": summary,
        "hypotheses": hypotheses,
        "customer_question": "5/5前後に、予算・ターゲット・CVイベント・広告素材の変更はありましたか?",
        "answer_options": {
            "A": "予算・入札・ターゲットを変更した",
            "B": "広告素材・LP・CVイベントを変更した",
            "C": "特に変更していない / 不明",
        },
    }
