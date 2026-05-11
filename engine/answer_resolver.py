"""answer_source_preference の解決エンジン (5/8 v3 P1-B 修正)

責務: rule_messaging.yaml の answer_source_preference に従って、各 rule の答えを
      api / validator / chatwork_reply の順で取得試行する。上位ソースで解決
      できれば顧客への質問を抑制 (= 本文から除外) し、解決できない場合のみ
      ChatWork で質問する。

設計:
    答えのソースは 3 層:
      1. api          : 媒体公式 API (Meta Business Manager / Pixel API 等)
                         Phase B Week 2-3 で実 API 接続予定。
                         現状は未実装 → resolved=False を返す stub。
      2. validator    : validators.client_tech_stack_validator の検証結果
                         (outputs/{client_id}/tech_stack_verification.yaml)
                         match / detected_only / declared_only / mismatch を見て解決判定。
      3. chatwork_reply: ChatWork 顧客回答 (chatwork_response_store)
                         A/B/C 形式の回答を見て解決判定 (confirmed_done/etc)。

戻り値の status:
    - "resolved"           : 答えが取れた、本文から除外
    - "manual_required"    : 顧客に質問する必要あり (chatwork_reply でも未解決)
    - "unknown"            : 評価できない (rule に answer_source_preference 未宣言等)

主要関数:
    - resolve_rule_answer(client_id, rule_id, msg_def, today=None) -> dict
        {"status": ..., "source": ..., "value": ..., "reason": ...}
    - should_suppress_question(client_id, rule_id, msg_def) -> bool
        True なら本文から除外 (= 顧客に質問しない)
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
TECH_STACK_VERIFY_DIR = ROOT / "outputs"


# ========== Public API ==========

def resolve_rule_answer(
    client_id: str, rule_id: str, msg_def: dict,
    today: Optional[datetime] = None,
    api_context: Optional[dict] = None,
) -> dict:
    """rule の answer_source_preference に従って答えを解決試行

    Returns:
        {
            "status":  "resolved" | "manual_required" | "unknown",
            "source":  "api" | "validator" | "chatwork_reply" | None,
            "value":   <解決した値 or status>,
            "reason":  "(human-readable)",
        }
    """
    pref = msg_def.get("answer_source_preference") or []
    if not pref:
        # answer_source_preference 未宣言 → chatwork_reply の挙動と同じ (顧客質問対象)
        return {
            "status": "unknown", "source": None, "value": None,
            "reason": "answer_source_preference 未宣言",
        }

    for source in pref:
        if source == "api":
            r = _try_api(client_id, rule_id, api_context=api_context)
        elif source == "validator":
            r = _try_validator(client_id, rule_id)
        elif source == "chatwork_reply":
            r = _try_chatwork_reply(client_id, rule_id, today=today)
        else:
            log.debug(f"unknown answer source: {source} for {rule_id}")
            continue

        if r.get("resolved"):
            return {
                "status": "resolved",
                "source": source,
                "value":  r.get("value"),
                "reason": r.get("reason", f"resolved via {source}"),
            }

    # どの source でも resolved できない → 顧客に質問する
    return {
        "status": "manual_required",
        "source": pref[-1] if pref else None,
        "value":  None,
        "reason": f"未解決、顧客質問が必要 (試行順: {pref})",
    }


def should_suppress_question(
    client_id: str, rule_id: str, msg_def: dict,
    today: Optional[datetime] = None,
    api_context: Optional[dict] = None,
) -> tuple:
    """本文から除外すべきか + 理由を返す

    Returns:
        (should_suppress: bool, reason: str)
        should_suppress=True なら本文に出さない (api/validator で解決済 or
                                  chatwork_reply で confirmed_done/not_applicable)
    """
    result = resolve_rule_answer(client_id, rule_id, msg_def, today=today, api_context=api_context)
    if result["status"] == "resolved":
        return (True, f"resolved via {result['source']}: {result['reason']}")
    return (False, result["reason"])


# ========== Source: api (Phase B 接続予定、現状 stub) ==========

def _try_api(client_id: str, rule_id: str, api_context: Optional[dict] = None) -> dict:
    """媒体公式 API で答えを取得試行

    fetch_audit_results が Meta API evidence を渡している場合はそれを使う。
    API が「設定済み/正常」を証明できたときだけ resolved=True にする。
    API が問題または未確認を示した場合は顧客質問を残す。

    Returns:
        {"resolved": False, "value": None, "reason": "..."}
    """
    evidence_map = _extract_api_evidence(api_context)
    evidence = evidence_map.get(rule_id) if evidence_map else None
    if not evidence:
        return {
            "resolved": False,
            "value":    None,
            "reason":   f"api evidence unavailable for {rule_id}",
        }

    status = evidence.get("status")
    if status == "resolved":
        return {
            "resolved": True,
            "value": evidence.get("value"),
            "reason": evidence.get("reason") or f"api resolved {rule_id}",
        }
    return {
        "resolved": False,
        "value": evidence.get("value"),
        "reason": evidence.get("reason") or f"api status={status}",
    }


def _extract_api_evidence(api_context: Optional[dict]) -> dict:
    if not api_context:
        return {}
    if api_context.get("meta_rule_evidence"):
        return api_context.get("meta_rule_evidence") or {}
    return (
        (api_context.get("platform_diagnostics") or {})
        .get("meta", {})
        .get("rule_evidence", {})
    )


# ========== Source: validator (tech_stack_verification.yaml) ==========

def _try_validator(client_id: str, rule_id: str) -> dict:
    """validators.client_tech_stack_validator の検証結果から取得試行

    outputs/{client_id}/tech_stack_verification.yaml に
    各カテゴリの diagnosis (match / detected_only / declared_only / mismatch / pending)
    が記録される。rule_id と category のマッピングは限定的:
      - F-AH-04 (ドメイン認証)        → ec_platform 検証時の domain_verification ヒント
      - F-LC-01 (景品表示法 LP 文言)  → ad_platforms validator (Phase B)
    現状はいずれも未マッピングの場合 resolved=False。

    Returns:
        {"resolved": bool, "value": str, "reason": "..."}
    """
    path = TECH_STACK_VERIFY_DIR / client_id / "tech_stack_verification.yaml"
    if not path.exists():
        return {
            "resolved": False, "value": None,
            "reason": f"validator: tech_stack_verification.yaml なし ({path.name})",
        }
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return {"resolved": False, "value": None, "reason": f"validator: yaml parse failed: {e}"}

    history = data.get("history") or []
    if not history:
        return {"resolved": False, "value": None, "reason": "validator: history 空"}
    latest = history[-1]
    categories = (latest.get("categories") or {})

    # rule_id → category のマッピング (Phase A は最小限)
    rule_to_category = {
        # ec_platform validator が match の場合 = LP 取得成功
        # F-AH-04 (Meta ドメイン認証) は Phase B で API 接続予定、現状未実装
    }
    cat = rule_to_category.get(rule_id)
    if not cat:
        return {
            "resolved": False, "value": None,
            "reason": f"validator: rule_id={rule_id} → category マッピング未定義",
        }

    cat_info = categories.get(cat) or {}
    diagnosis = cat_info.get("diagnosis")
    if diagnosis == "match":
        return {
            "resolved": True, "value": cat_info.get("detected"),
            "reason": f"validator: {cat}=match (detected={cat_info.get('detected')})",
        }
    return {
        "resolved": False, "value": None,
        "reason": f"validator: {cat}={diagnosis} (manual confirm 必要)",
    }


# ========== Source: chatwork_reply (chatwork_response_store) ==========

def _try_chatwork_reply(
    client_id: str, rule_id: str, today: Optional[datetime] = None,
) -> dict:
    """ChatWork 顧客回答ストアから取得試行

    chatwork_response_store.get_active_response で期限内の回答を確認。
    confirmed_done / not_applicable は resolved=True (本文から除外)、
    wants_help / not_done は resolved=False (本文に出して継続案内)。

    Returns:
        {"resolved": bool, "value": str, "reason": "..."}
    """
    try:
        from engine.chatwork_response_store import get_active_response
    except ImportError:
        return {"resolved": False, "value": None, "reason": "response store import failed"}

    rec = get_active_response(client_id, rule_id, today=today)
    if not rec:
        return {"resolved": False, "value": None, "reason": "chatwork_reply: 回答なし"}

    status = rec.get("status")
    if status in ("confirmed_done", "not_applicable"):
        return {
            "resolved": True, "value": status,
            "reason": f"chatwork_reply: status={status} (有効期限内)",
        }
    return {
        "resolved": False, "value": status,
        "reason": f"chatwork_reply: status={status} (継続案内対象)",
    }
