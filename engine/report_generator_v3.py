"""v3 レポート生成オーケストレータ。

設計: docs/report_design/v3_structure.md / v3_content_strategy.md

データフロー:
    audit_results
      ↓
    benchmark_compare（業界比較）
    impact_estimator（数値試算）
    priority_ranker（Top5 + Critical Alerts）
    claude_insights（顧客語翻訳・ナラティブ・Zynect Insights）
      ↓
    Jinja2 v3 テンプレートに渡すコンテキスト dict を返す
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.benchmark_compare import (
    build_chart_data,
    build_health_score_3axis,
    build_metric_label,
    compare_3axis,
    load_benchmarks,
)
from engine.claude_insights import ClaudeInsights
from engine.impact_estimator import (
    aggregate_top5_impact,
    build_kpi_projection,
    estimate_for_rule,
)
from engine.priority_ranker import (
    compute_critical_alerts,
    compute_top_actions,
    load_all_rules,
    load_weights,
)

log = logging.getLogger("bpo")

PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads"}
PLATFORM_KEY = {"google": "google_ads", "meta": "meta_ads", "tiktok": "tiktok_ads"}
GRADE_DESC = {
    "A": "優秀 — 最適化済み",
    "B": "良好 — 軽微な改善余地",
    "C": "要改善 — 構造的問題あり",
    "D": "要注意 — 複数の重大問題",
    "F": "危険 — 即時対応が必要",
}

# 媒体別の主要メトリクス（platform 詳細ページで3軸比較する対象）
PLATFORM_METRICS = {
    "google": ["ctr", "cpa", "cvr", "roas"],
    "meta": ["ctr", "cpa", "cvr", "roas", "frequency"],
    "tiktok": ["ctr", "cpa", "cvr", "roas"],
}


def _format_addressee(client_cfg: dict) -> dict[str, str]:
    """company / contact ブロックから宛先表記を組み立てる。"""
    company = client_cfg.get("company") or {}
    contact = client_cfg.get("contact") or {}

    company_name = company.get("name") or client_cfg.get("name") or "(企業名未設定)"
    company_honorific = company.get("honorific") or "御中"
    contact_name = contact.get("name") or ""
    contact_honorific = contact.get("honorific") or "様"
    contact_title = contact.get("title") or ""

    lines = [f"{company_name} {company_honorific}"]
    if contact_name and not contact_name.startswith("[要入力"):
        prefix = f"{contact_title} " if contact_title and not contact_title.startswith("[要入力") else ""
        lines.append(f"ご担当 {prefix}{contact_name} {contact_honorific}")
    else:
        lines.append("ご担当者様")

    return {
        "company_name": company_name,
        "company_honorific": company_honorific,
        "contact_name": contact_name,
        "contact_honorific": contact_honorific,
        "addressee_lines": lines,
    }


def _resolve_industry(client_cfg: dict) -> tuple[str, str]:
    """業界キーと表示ラベル。フォールバックは ec_retail。"""
    company = client_cfg.get("company") or {}
    industry = (company.get("industry") or "").strip()
    label = (company.get("industry_label") or "").strip()

    valid_keys = {"ec_retail", "saas_b2b", "finance", "education", "local_service"}
    if industry not in valid_keys:
        log.warning(f"industry='{industry}' は未対応キー、ec_retail にフォールバック")
        industry = "ec_retail"
        if not label or label.startswith("[要入力"):
            label = "EC・通販"

    if not label or label.startswith("[要入力"):
        label = {
            "ec_retail": "EC・通販",
            "saas_b2b": "SaaS・B2B",
            "finance": "金融・保険",
            "education": "教育",
            "local_service": "地域サービス",
        }.get(industry, "(業界ラベル未設定)")

    return industry, label


def _polar_marker(pct: float, radius: float = 42.0, cx: float = 50.0, cy: float = 50.0) -> dict:
    """0〜100 の % 値から、SVG 円形チャート上のマーカー (x, y) を計算する。

    macro の SVG は viewBox=0 0 100 100、円中心(50,50) 半径42、画面全体を -90deg 回転して
    描画している。stroke-dasharray は (cx+r, cy) を起点に時計回りに描画されるため、
    回転前座標系における始点は「右」（x=cx+r, y=cy）。
    pct 進んだ位置 = 始点から時計回り角度 (pct/100)*2π の点。
    時計回りは標準数学座標（反時計回り正）と逆なので符号反転して計算する。
    """
    angle_rad = (pct / 100.0) * 2.0 * math.pi
    x = cx + radius * math.cos(angle_rad)
    y = cy - radius * math.sin(angle_rad)  # SVG y 軸は下向き、時計回り角度に合わせ符号反転
    return {"x": round(x, 2), "y": round(y, 2), "pct": round(pct, 1)}


def _build_platform_compare(
    audit: dict,
    industry: str,
    bm: dict,
) -> list[dict]:
    """媒体別詳細ページ用の 3軸比較データを構築する。

    C7: 監査対象 3 媒体（Google / Meta / TikTok）を常に出力し、
    データ無しの媒体には has_data=False のプレースホルダを返す。
    """
    platform_summary = audit.get("platform_summary") or {}
    issues = audit.get("issues") or []

    out: list[dict] = []
    # 監査対象の固定 3 媒体（順序も固定）
    target_platforms = ["google", "meta", "tiktok"]
    for pkey in target_platforms:
        if pkey not in platform_summary:
            # データなしプレースホルダ
            out.append({
                "key": pkey,
                "label": PLATFORM_LABEL.get(pkey, pkey),
                "has_data": False,
                "score": None,
                "campaigns": 0,
                "cost_display": "—",
                "cv": 0,
                "roas": 0,
                "score_3axis": None,
                "metrics": [],
                "top3_issues": [],
                "issues_count": 0,
                "no_data_reason": "本媒体は分析対象外（データ未取得）",
            })
            continue
        summary = platform_summary[pkey]
        bench_pf = PLATFORM_KEY.get(pkey, pkey)
        score_3axis = build_health_score_3axis(industry, summary.get("score", 0), bm)

        metrics_data: list[dict] = []
        # 各指標の現状値を audit から取り出す（None は「—」表示）
        def _first_non_none(*keys):
            for k in keys:
                v = summary.get(k)
                if v is not None:
                    return v
            return None
        current_values = {
            "ctr": _first_non_none("avg_ctr"),
            "cpa": _first_non_none("avg_cpa", "cpa"),
            "cpc": _first_non_none("avg_cpc", "cpc"),
            "cvr": _first_non_none("avg_cvr"),
            "roas": _first_non_none("avg_roas", "roas"),
            "frequency": _first_non_none("avg_frequency", "frequency"),
        }
        for metric in PLATFORM_METRICS.get(pkey, ["ctr", "cpa", "roas"]):
            current = current_values.get(metric)
            if current is None:
                cmp = compare_3axis(industry, bench_pf, metric, None, bm)
            else:
                cmp = compare_3axis(industry, bench_pf, metric, float(current), bm)
            chart = build_chart_data(cmp)
            metrics_data.append(
                {
                    "metric": metric,
                    "label": build_metric_label(metric),
                    "current": current,
                    "current_display": _format_metric(metric, current),
                    "industry_avg": cmp.get("industry_avg"),
                    "industry_avg_display": _format_metric(metric, cmp.get("industry_avg")),
                    "zynect_recommended": cmp.get("zynect_recommended"),
                    "zynect_display": _format_metric(metric, cmp.get("zynect_recommended")),
                    "status": cmp.get("status"),
                    "status_color": chart.get("status_color"),
                    "current_pct": chart.get("current_pct") or 0,
                    "industry_pct": chart.get("industry_avg_pct"),
                    "zynect_pct": chart.get("zynect_pct"),
                    "available": chart.get("available", False),
                    "note": cmp.get("note"),
                }
            )

        p_issues = [i for i in issues if i.get("platform") == pkey]
        # TOP3 検出問題（severity: critical → high → medium 順）
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        p_issues_sorted = sorted(p_issues, key=lambda x: sev_order.get(x.get("severity"), 9))[:3]

        out.append(
            {
                "key": pkey,
                "label": PLATFORM_LABEL.get(pkey, pkey),
                "has_data": True,
                "score": summary.get("score", 0),
                "campaigns": summary.get("campaigns", 0),
                "cost_display": f"{summary.get('cost', 0):,.0f}",
                "cv": summary.get("conversions", 0),
                "roas": summary.get("roas", 0),
                "score_3axis": score_3axis,
                "metrics": metrics_data,
                "top3_issues": p_issues_sorted,
                "issues_count": len(p_issues),
            }
        )
    return out


def _format_metric(metric: str, value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)
    if metric in ("ctr", "cvr"):
        return f"{v:.2f}%"
    if metric == "roas":
        return f"{v:.2f}倍"
    if metric == "frequency":
        return f"{v:.2f}回/月"
    if metric in ("cpa", "cpc", "cpm"):
        return f"¥{v:,.0f}"
    return f"{v:,.2f}"


def _gather_detected_rule_ids(audit: dict) -> list[str]:
    """audit results の issues から rule ID を抽出する。

    Issues の rule ID キーは実装によって `check_id` または `id` のどちらか。
    両方をサポートし、重複除去は priority_ranker 側に任せる。
    """
    issues = audit.get("issues") or []
    out: list[str] = []
    for i in issues:
        rid = i.get("check_id") or i.get("id") or i.get("rule_id")
        if rid:
            out.append(rid)
    return out


def _build_actions_with_narrative(
    rules_by_id: dict,
    weights: dict,
    detected_ids: list[str],
    monthly_spend: float,
    insights: ClaudeInsights,
    audit: dict,
) -> list[dict]:
    """priority_ranker で Top5 を取り、各アクションに impact + narrative を付与する。

    A2: current_metrics（CPA/ROAS/CV数/プラットフォーム別キャンペーン情報）を
    impact_estimator に渡し、キャンペーン状態に応じた精緻な試算を実行する。
    """
    top5 = compute_top_actions(detected_ids, rules_by_id, weights, monthly_spend, max_n=5)
    issues = audit.get("issues") or []
    platform_summary = audit.get("platform_summary") or {}

    # アカウント全体の現状値
    base_metrics = {
        "cpa": audit.get("avg_cpa") or 0,
        "roas": audit.get("avg_roas") or audit.get("avg_ctr") or 0,  # avg_roas がある前提だが念のためフォールバック
        "cv_count": audit.get("total_conversions") or 0,
        "cost": audit.get("total_cost") or monthly_spend,
        "ctr": audit.get("avg_ctr") or 0,
    }

    # rule_id ごとに最も影響の大きい issue をマップ
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues_by_rule: dict[str, dict] = {}
    for issue in sorted(issues, key=lambda x: sev_order.get(x.get("severity"), 9)):
        rid = issue.get("check_id") or issue.get("id") or issue.get("rule_id")
        if rid and rid not in issues_by_rule:
            issues_by_rule[rid] = issue

    enriched: list[dict] = []
    for action in top5:
        rule = rules_by_id.get(action["rule_id"])
        if not rule:
            continue

        # current_metrics 構築: ルールに紐づく issue のプラットフォーム平均を採用
        current_metrics = dict(base_metrics)
        issue = issues_by_rule.get(action["rule_id"])
        if issue:
            pkey = issue.get("platform")
            if pkey and pkey in platform_summary:
                ps = platform_summary[pkey]
                # プラットフォーム平均を採用（より精緻）
                if ps.get("avg_cpa"):
                    current_metrics["cpa"] = ps["avg_cpa"]
                if ps.get("avg_roas"):
                    current_metrics["roas"] = ps["avg_roas"]
                if ps.get("conversions"):
                    current_metrics["cv_count"] = ps["conversions"]
                if ps.get("cost"):
                    current_metrics["campaign_cost"] = ps["cost"]
                    current_metrics["campaign_cpa"] = ps.get("avg_cpa") or 0
                    current_metrics["campaign_cv"] = ps.get("conversions") or 0

        impact = estimate_for_rule(rule, monthly_spend, current_metrics=current_metrics)
        principle_tag = action.get("principle_tag", "")
        narrative = insights.generate_action_narrative(rule, impact, principle_tag)

        enriched.append(
            {
                **action,
                "impact": impact,
                "narrative": narrative,
                "expected_savings_display": impact.get("estimated_savings_display"),
                "confidence_label": impact.get("confidence_label", "—"),
                "horizon_weeks": impact.get("impact_horizon_weeks"),
                "principle_tag": principle_tag,
                "related_rule_ids": [action["rule_id"]],
                "calc_basis": impact.get("calc_basis", "monthly_spend"),
            }
        )
    return enriched


def build_v3_context(
    client_id: str,
    client_cfg: dict,
    results: dict,
) -> dict[str, Any]:
    """v3 テンプレート用のコンテキストを構築する。"""
    audit = results.get("ads_audit") or {}
    score = audit.get("score", 0)
    grade = audit.get("grade", "F")

    # ベース情報
    addressee = _format_addressee(client_cfg)
    industry, industry_label = _resolve_industry(client_cfg)
    bm = load_benchmarks()
    weights = load_weights()
    rules_by_id = load_all_rules()
    monthly_spend = float(audit.get("total_cost", 0) or weights.get("default_monthly_spend_yen", 750000))

    # 検出結果
    detected_ids = _gather_detected_rule_ids(audit)
    issues = audit.get("issues") or []

    # Claude
    insights = ClaudeInsights(client_id)

    # === セクション1: Cover ===
    report_cfg = client_cfg.get("report") or {}
    cover = {
        "report_name": report_cfg.get("display_name") or "広告アカウント健康診断レポート",
        "version": "v3.0",
        "addressee_lines": addressee["addressee_lines"],
        "company_name": addressee["company_name"],
        "company_honorific": addressee["company_honorific"],
        "contact_name": addressee["contact_name"],
        "contact_honorific": addressee["contact_honorific"],
        "report_period": (report_cfg.get("report_period_days") or 30),
        "period_text": f"直近 {report_cfg.get('report_period_days') or 30} 日間",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M JST"),
        "industry_label": industry_label,
    }

    # === Top5 アクション + Critical Alerts ===
    actions = _build_actions_with_narrative(rules_by_id, weights, detected_ids, monthly_spend, insights, audit)

    # 集計
    estimates = [a["impact"] for a in actions]
    aggregate = aggregate_top5_impact(estimates)
    kpi_proj = build_kpi_projection(audit, aggregate)

    critical_alerts = compute_critical_alerts(detected_ids, rules_by_id, weights)

    # === セクション2: Executive Summary ===
    summary_3lines = insights.generate_summary_3lines(audit, aggregate, industry_label)
    health_score_3axis = build_health_score_3axis(industry, score, bm)
    # B4: 円形チャート上の業界平均・Zynect 推奨マーカー位置を事前計算
    health_score_3axis["industry_marker"] = _polar_marker(health_score_3axis["industry_avg_pct"])
    health_score_3axis["zynect_marker"] = _polar_marker(health_score_3axis["zynect_pct"])
    health_score_3axis["current_marker"] = _polar_marker(health_score_3axis["current_pct"])
    summary = {
        "score": score,
        "grade": grade,
        "grade_class": grade.lower(),
        "grade_description": GRADE_DESC.get(grade, ""),
        "industry_label": industry_label,
        "summary_3lines": summary_3lines,
        "health_score_3axis": health_score_3axis,
        "kpi_projection": kpi_proj,
        "aggregate": aggregate,
        "total_savings_display": aggregate.get("total_savings_display"),
        "confidence_summary": aggregate.get("confidence_summary"),
        "horizon_weeks_max": aggregate.get("horizon_weeks_max"),
        "platform_count": len(audit.get("platform_summary", {})),
        "total_checks": audit.get("total_checks", 0),
        "issue_count": len(issues),
        "critical_count": len([i for i in issues if i.get("severity") == "critical"]),
    }

    # === セクション4-6: Platform Detail ===
    platforms = _build_platform_compare(audit, industry, bm)

    # === セクション7: Zynect Insights ===
    detected_rules = [rules_by_id[rid] for rid in detected_ids if rid in rules_by_id]
    insight_items = insights.generate_zynect_insights(detected_rules, {}, max_count=4)

    # === セクション8: Appendix ===
    appendix = {
        "terminology": _appendix_terminology(),
        "principles": _appendix_principles(),
    }

    # Claude セッション統計（ログ用）
    llm_stats = insights.session_stats()
    log.info(
        f"[{client_id}] v3 LLM stats: api_available={llm_stats['api_available']}, "
        f"calls={llm_stats['total_calls']}, cost=¥{llm_stats['estimated_cost_jpy']}"
    )

    return {
        "client_id": client_id,
        "cover": cover,
        "summary": summary,
        "actions": actions,
        "critical_alerts": critical_alerts,
        "platforms": platforms,
        "insights": insight_items,
        "appendix": appendix,
        "llm_stats": llm_stats,
        "footer_text": "本書は機密保持契約に基づき作成されました / © 2026 Zynect Media 株式会社",
    }


def _appendix_terminology() -> list[dict]:
    """付録: 用語集（最重要 25 語）"""
    return [
        {"term": "CPA", "desc": "顧客獲得単価。広告経由で1人を獲得するのにかかった広告費。"},
        {"term": "ROAS", "desc": "広告費用対効果。広告費1円あたりの売上（倍率）。"},
        {"term": "CTR", "desc": "クリック率。広告が表示された回数のうち何%がクリックされたか。"},
        {"term": "CVR", "desc": "コンバージョン率。クリックしたユーザーのうち何%が成果に至ったか。"},
        {"term": "CV", "desc": "コンバージョン。広告経由で発生した購入・申込・問合せなど成果1件。"},
        {"term": "CPC", "desc": "1クリック単価。広告がクリックされるたびにかかる費用。"},
        {"term": "CPM", "desc": "広告表示1,000回あたりの費用。"},
        {"term": "インプレッション", "desc": "広告が表示された回数。"},
        {"term": "リーチ", "desc": "ユニーク到達ユーザー数（同一ユーザーの重複表示はカウントしない）。"},
        {"term": "フリークエンシー", "desc": "同一ユーザーへの広告表示回数。月4.0回超は広告疲れの兆候。"},
        {"term": "IS（インプレッションシェア）", "desc": "獲得可能だった表示機会のうち自社広告が獲得した割合。"},
        {"term": "学習フェーズ", "desc": "AI が配信を最適化中の状態。Meta は週 50CV、TikTok も同様の基準で安定。"},
        {"term": "学習データ不足", "desc": "月予算が CPA × 20 倍未満で AI が学習に必要なデータを集められない状態。"},
        {"term": "PMax / Performance Max", "desc": "Google AI が全配信面に自動配信するキャンペーン形式。"},
        {"term": "Smart Bidding", "desc": "Google の AI 自動入札。tCPA / tROAS で目標値を達成するよう調整。"},
        {"term": "Advantage+ / ASC+", "desc": "Meta の AI 自動最適化機能。配置・オーディエンス・CR を自動最適化。"},
        {"term": "Smart+", "desc": "TikTok の AI 自動最適化機能（PMax / Advantage+ の TikTok 版）。"},
        {"term": "RSA", "desc": "レスポンシブ検索広告。複数の見出し・説明文を AI が組み合わせて配信。"},
        {"term": "Pixel / Events API / CAPI", "desc": "ブラウザ計測タグ / サーバ送信 API。CV を直接 Meta / TikTok に送る計測。"},
        {"term": "EMQ", "desc": "Event Match Quality。Meta の計測品質スコア（0〜10）。Purchase は 8.0+ が目標。"},
        {"term": "ネガティブシグナル", "desc": "「配信を止めるべき要素」を AI に教える情報（除外 KW・除外オーディエンス等）。"},
        {"term": "クイックウィン", "desc": "小工数で大きな改善が見込める施策。"},
        {"term": "オーディエンス", "desc": "配信対象ユーザー層。"},
        {"term": "リターゲティング", "desc": "過去にサイト訪問・商品閲覧したユーザーへの再アプローチ広告。"},
        {"term": "拡張コンバージョン", "desc": "ハッシュ化メール等を Google に送り計測精度を高める仕組み。"},
    ]


def _appendix_principles() -> list[dict]:
    """付録: 米満氏理論 9+10 原則の要約"""
    return [
        {"id": "P1", "name": "計測精度=学習シグナル精度原則", "desc": "計測の正しさは全運用判断の前提。"},
        {"id": "P2", "name": "機械学習保護原則", "desc": "短期判断による長期最適化の毀損を回避する。"},
        {"id": "P3", "name": "結果指標非依存原則", "desc": "品質スコアではなく原因変数で判断する。"},
        {"id": "P4", "name": "ネガティブシグナル保持原則", "desc": "低パフォ要素は削除せず除外保持して学習を強化。"},
        {"id": "P5", "name": "Budget Lost 先行解消原則", "desc": "効率改善より機会損失の解消を優先する。"},
        {"id": "P6", "name": "集約優先・分離原則", "desc": "学習単位は集約し、評価軸が異質なものは分離。"},
        {"id": "P7", "name": "バリエーション幅最大化原則", "desc": "多パターン × 短い見出しで AI に選ばせる。"},
        {"id": "P8", "name": "自動化前提判断原則", "desc": "旧式手動運用の知識を捨て自動入札時代の判断へ。"},
        {"id": "P9", "name": "説明責任・判断ログ原則", "desc": "なぜその判断をしたかを月次レポートに残す。"},
        {"id": "M-α", "name": "計測=シグナル基盤原則", "desc": "Meta では Pixel + CAPI + EMQ で計測精度を担保。"},
        {"id": "M-β", "name": "学習フェーズ保護原則", "desc": "週 50CV 基準。学習中の編集を抑制する。"},
        {"id": "M-η", "name": "Advantage+ 自動化前提原則", "desc": "ASC+ など自動化機能を前提とした運用設計。"},
        {"id": "M-ζ", "name": "クリエイティブ量産・多様性最大化原則", "desc": "ASC 内 CR 15〜50 本のアクティブ運用。"},
        {"id": "M-θ", "name": "iOS14 計測欠損前提運用原則", "desc": "AEM・優先度設定で計測欠損を補う。"},
        {"id": "M-λ", "name": "広告-LP メッセージ完全一致原則", "desc": "広告コピー・動画と LP の整合度が CVR を支配。"},
    ]
