"""指摘検出器 (ADR-005 / Day 2 C3)

各 analyzer の出力を統一された IndicationCandidate 形式に変換し、
IndicationState に upsert する。

サポート analyzer:
- ads_audit (issues)
- anomaly (alerts)
- fraud_audit (issues)
- cv_quality_scorer (calculate_true_cv_count の集約)

統一 IndicationCandidate:
{
    "rule_id": str,
    "platform": str,           # google / meta / tiktok / cross / seo
    "target_id": str,          # campaign_id / pixel_id / cross / その他
    "severity": str,           # critical / high / medium / low
    "title": str,              # 短い説明
    "payload": dict,           # 元 analyzer データの抜粋（テンプレート描画用）
}

呼び出しパターン:
    candidates = collect_candidates(audit_results)
    detected_records = []
    for cand in candidates:
        rec = state.upsert_detected(**cand)
        detected_records.append(rec)
    state.save()
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

log = logging.getLogger("bpo")

# ---------- ルールメタ補完 (空 fact/impact のフォールバック用) ----------
_RULE_META_CACHE: Optional[dict[str, dict]] = None
_RULES_DIR = Path(__file__).resolve().parent.parent / "config" / "rules"


def _load_rule_meta() -> dict[str, dict]:
    """config/rules/*.yaml を全部読んで rule_id → {name, category, severity} の辞書を返す。

    fact/impact が空の analyzer 出力 (M62 等) を補完するためのフォールバック情報源。
    """
    global _RULE_META_CACHE
    if _RULE_META_CACHE is not None:
        return _RULE_META_CACHE
    out: dict[str, dict] = {}
    if _RULES_DIR.exists():
        for yaml_file in _RULES_DIR.glob("*.yaml"):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                for rule in data.get("rules", []):
                    rid = rule.get("id")
                    if rid:
                        out[rid] = {
                            "name": rule.get("name", ""),
                            "category": rule.get("category", ""),
                            "severity": rule.get("severity", "medium"),
                            "platform": rule.get("platform", ""),
                        }
            except (yaml.YAMLError, OSError):
                continue
    _RULE_META_CACHE = out
    return out


def _enrich_payload(payload: dict, rule_id: str) -> dict:
    """payload の title/fact/impact が空なら rule_id のメタ情報で補完する。

    ads_audit 等が message を空のまま issue を返すケース (M62 等) で
    daily_indication.md.j2 が「事実」「影響」欄を空表示するのを防ぐ。
    """
    meta = _load_rule_meta().get(rule_id, {})
    name = meta.get("name") or rule_id
    category = meta.get("category") or ""
    if not payload.get("title"):
        payload["title"] = name
    if not payload.get("fact"):
        payload["fact"] = (
            f"{name} (ルールID: {rule_id}) のチェックに該当しています"
            + (f"（カテゴリ: {category}）" if category else "")
            + "。"
        )
    if not payload.get("impact"):
        action = payload.get("recommended_action") or ""
        payload["impact"] = (
            f"本指摘 ({rule_id}) は {category or '運用品質'} に関わる"
            + ("重大" if meta.get("severity") in ("critical", "high") else "")
            + f"課題で、未対応のまま運用すると {category or '配信効率'} に悪影響が及びます。"
            + (f" 推奨アクション: {action}" if action else "")
        )
    return payload

# severity 同義語マップ（warning → high など analyzer 個別の用語を吸収）
SEVERITY_NORMALIZE = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "warning": "high",  # anomaly.py で warning は high 扱い
    "info": "low",
}


def _norm_severity(value: Optional[str]) -> str:
    if not value:
        return "medium"
    return SEVERITY_NORMALIZE.get(str(value).lower(), "medium")


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _coalesce(*values, default=""):
    for v in values:
        if v is not None and v != "":
            return v
    return default


# ---------- analyzer 別変換 ----------

def from_ads_audit(audit_result: Optional[dict]) -> list[dict]:
    """ads_audit.run_audit の結果から候補を抽出

    audit_result["issues"] の各要素は以下のフィールドを持つ:
        id, severity, platform, message, action, campaign, suppressed?
    """
    if not audit_result or "issues" not in audit_result:
        return []
    out: list[dict] = []
    for issue in audit_result["issues"]:
        if issue.get("suppressed"):
            continue
        rule_id = _safe_str(_coalesce(issue.get("id"), issue.get("check_id"), default=""))
        if not rule_id:
            continue
        platform = _safe_str(issue.get("platform"), "cross")
        # campaign 名 or campaign_id を target_id として使う（無ければ rule-level → "global"）
        target = _safe_str(_coalesce(issue.get("campaign_id"), issue.get("campaign"), default="global"))
        payload = {
            "source": "ads_audit",
            "title": _safe_str(issue.get("message")) or rule_id,
            "fact": _safe_str(issue.get("message")),
            "impact": _safe_str(issue.get("impact")) or _safe_str(issue.get("message")),
            "recommended_action": _safe_str(issue.get("action")),
            "campaign": _safe_str(issue.get("campaign")),
        }
        out.append({
            "rule_id": rule_id,
            "platform": platform,
            "target_id": target,
            "severity": _norm_severity(issue.get("severity")),
            "payload": _enrich_payload(payload, rule_id),
        })
    return out


def from_anomaly(anomaly_result: Optional[dict]) -> list[dict]:
    """anomaly.detect_anomalies の結果から候補を抽出

    anomaly_result["alerts"] の各要素:
        severity, message, cause, action, platform, metric?, campaign?
    """
    if not anomaly_result or "alerts" not in anomaly_result:
        return []
    out: list[dict] = []
    for alert in anomaly_result["alerts"]:
        platform = _safe_str(alert.get("platform"), "cross")
        # anomaly は rule_id を持たないため metric + platform を rule_id として合成
        metric = _safe_str(_coalesce(alert.get("metric"), alert.get("type"), default="anomaly"))
        rule_id = f"ANO_{metric.upper()}"
        target = _safe_str(_coalesce(alert.get("campaign_id"), alert.get("campaign"), platform, default="global"))
        payload = {
            "source": "anomaly",
            "title": _safe_str(alert.get("message"), "異常検知"),
            "fact": _safe_str(alert.get("message")),
            "impact": _safe_str(alert.get("cause")),
            "recommended_action": _safe_str(alert.get("action")),
            "metric": metric,
        }
        out.append({
            "rule_id": rule_id,
            "platform": platform,
            "target_id": target,
            "severity": _norm_severity(alert.get("severity")),
            "payload": _enrich_payload(payload, rule_id),
        })
    return out


def from_fraud_audit(fraud_result: Optional[dict]) -> list[dict]:
    """fraud_audit.run_fraud_audit の結果から候補を抽出

    fraud_result["issues"] の各要素:
        check_id, severity, platform, message
    """
    if not fraud_result or "issues" not in fraud_result:
        return []
    out: list[dict] = []
    for issue in fraud_result["issues"]:
        rule_id = _safe_str(_coalesce(issue.get("check_id"), issue.get("id"), default=""))
        if not rule_id:
            continue
        platform = _safe_str(issue.get("platform"), "cross")
        target = _safe_str(_coalesce(issue.get("campaign"), platform, default="global"))
        payload = {
            "source": "fraud_audit",
            "title": _safe_str(issue.get("message"), rule_id),
            "fact": _safe_str(issue.get("message")),
            "impact": _safe_str(issue.get("impact")) or _safe_str(issue.get("message")),
            "recommended_action": _safe_str(issue.get("action")),
        }
        out.append({
            "rule_id": rule_id,
            "platform": platform,
            "target_id": target,
            "severity": _norm_severity(issue.get("severity")),
            "payload": _enrich_payload(payload, rule_id),
        })
    return out


def from_cv_quality(cv_result: Optional[dict], platform: str = "meta") -> list[dict]:
    """cv_quality_scorer.calculate_true_cv_count の集約結果から候補を抽出

    fake CV 比率が一定以上なら CV_QUALITY_FAKE_RATE 指摘として 1 件発火。
    """
    if not cv_result or cv_result.get("total_cvs", 0) == 0:
        return []
    fake_count = cv_result.get("fake_cvs", 0)
    real_count = cv_result.get("real_cvs", 0)
    total = cv_result.get("total_cvs", 0)
    if total == 0:
        return []
    fake_rate = fake_count / total
    # 30% 超で high、50% 超で critical
    if fake_rate < 0.30:
        return []
    severity = "critical" if fake_rate >= 0.50 else "high"
    return [{
        "rule_id": "CV_QUALITY_FAKE_RATE",
        "platform": platform,
        "target_id": f"{platform}_cv_pool",
        "severity": severity,
        "payload": {
            "source": "cv_quality_scorer",
            "title": f"CV 品質低下: 不正疑い {fake_rate:.0%}",
            "fact": f"直近の CV {total} 件中 {fake_count} 件 ({fake_rate:.0%}) が品質スコア閾値未満。",
            "impact": "学習シグナル汚染により CPA 改善が阻害される可能性。",
            "recommended_action": "AdTruth タグ実装 + 怪しい流入の除外配信",
            "fake_cvs": fake_count,
            "real_cvs": real_count,
            "total_cvs": total,
        },
    }]


# ---------- 統合 ----------

def collect_candidates(audit_results: dict) -> list[dict]:
    """全 analyzer の結果を統合して候補リストを返す

    Args:
        audit_results: pipeline.py が組み立てる結果辞書
            {
                "ads_audit": {...},
                "anomalies": {...},
                "fraud_audit": {...},
                "cv_quality": {...},  # オプション
            }

    Returns:
        IndicationCandidate のリスト（上流順、重複排除なし）
    """
    candidates: list[dict] = []
    candidates.extend(from_ads_audit(audit_results.get("ads_audit")))
    candidates.extend(from_anomaly(audit_results.get("anomalies")))
    candidates.extend(from_fraud_audit(audit_results.get("fraud_audit")))
    cv_q = audit_results.get("cv_quality")
    if cv_q:
        # cv_quality は media を辞書で持つ場合と直接 result の場合がある
        if isinstance(cv_q, dict) and "platforms" in cv_q:
            for p, pres in cv_q["platforms"].items():
                candidates.extend(from_cv_quality(pres, platform=p))
        else:
            candidates.extend(from_cv_quality(cv_q))

    # 同 (rule_id, platform, target_id) の重複を除去（最初の1件を採用）
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for c in candidates:
        key = (c["rule_id"], c["platform"], c["target_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def detect_and_upsert(
    audit_results: dict,
    state,  # IndicationState
    today: Optional[str] = None,
) -> tuple[list[dict], list[dict]]:
    """audit_results を candidate に変換 → state に upsert → clean 判定

    Returns:
        (newly_detected_records, clean_observed_records)

    clean_observed_records:
        前回まで open/resolved_pending だったが今回検知されなかったレコード。
        呼び出し側で mark_clean を逐次呼ぶことで status 遷移する。
    """
    candidates = collect_candidates(audit_results)
    detected_keys: set[tuple] = set()
    upserted: list[dict] = []
    for cand in candidates:
        rec = state.upsert_detected(today=today, **cand)
        upserted.append(rec)
        detected_keys.add((rec["rule_id"], rec["platform"], rec["target_id"]))

    # 前回 active で今回検知されなかったものを clean 候補として返す
    clean_candidates = [
        r for r in state.list_open_or_pending()
        if (r["rule_id"], r["platform"], r["target_id"]) not in detected_keys
    ]
    return upserted, clean_candidates


def reconcile_clean(
    clean_candidates: Iterable[dict],
    state,
    today: Optional[str] = None,
    data_available: bool = True,
    clean_threshold: int = 3,
) -> list[dict]:
    """clean 候補に mark_clean を適用し、status 遷移後のレコードを返す

    data_available=False の場合は consecutive_clean_days を進めない（C4 の一時欠損ガード）。
    """
    out: list[dict] = []
    for r in clean_candidates:
        updated = state.mark_clean(
            r["indication_id"],
            today=today,
            data_available=data_available,
            clean_threshold=clean_threshold,
        )
        if updated:
            out.append(updated)
    return out
