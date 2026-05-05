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
    aggregate_with_dedup,
    build_kpi_projection,
    calculate_independent_impact,
    calculate_minimum_impact,
    calculate_realistic_impact,
    estimate_for_rule,
)
from engine.longterm_projector import build_longterm_projection
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

    # v3.1 (Task B): beauty_d2c を新設
    valid_keys = {"ec_retail", "beauty_d2c", "saas_b2b", "finance", "education", "local_service"}
    if industry not in valid_keys:
        log.warning(f"industry='{industry}' は未対応キー、ec_retail にフォールバック")
        industry = "ec_retail"
        if not label or label.startswith("[要入力"):
            label = "EC・通販"

    if not label or label.startswith("[要入力"):
        label = {
            "ec_retail": "EC・通販",
            "beauty_d2c": "美容・D2C",
            "saas_b2b": "SaaS・B2B",
            "finance": "金融・保険",
            "education": "教育",
            "local_service": "地域サービス",
        }.get(industry, "(業界ラベル未設定)")

    return industry, label


# =============================================================================
# v3.1: ルール ID → 具体的な実装ステップ・確認方法のテンプレート
# =============================================================================
# Claude API 不在時に「明日から何をやるか」が読み手に伝わるよう、主要ルール
# 単位で実装手順と確認方法をテンプレ化する。redesign_note と組み合わせて使う。
RULE_PLAYBOOK: dict[str, dict] = {
    # === 計測基盤系 ===
    "M01": {
        "implementation_steps": [
            "Meta Events Manager で対象 Pixel ID の発火状況を確認（直近 7 日）",
            "発火していない場合: GTM / サイト埋め込みコードに Pixel が正しく設置されているか検証",
            "Helper 拡張機能 (Meta Pixel Helper) で実画面でのイベント発火を目視確認",
            "PageView / Purchase 等の主要イベントが正しいパラメータ付きで発火しているか確認",
        ],
        "verification": "Events Manager の「テストイベント」タブで実際に発火イベントが受信されること",
    },
    "M02": {
        "implementation_steps": [
            "Events Manager → 設定 → コンバージョン API のステータスを確認",
            "未実装の場合: サーバー側で Conversions API SDK (Python/Node) を実装、Pixel と同じ event_id でデデュプ",
            "実装済みの場合: EMQ スコアと「重複なし」判定を確認",
            "ハッシュ化メールアドレス / 電話番号 / fbc / fbp を必ず送信",
        ],
        "verification": "Events Manager の「コンバージョン API ヘルス」が緑色 / EMQ ≥ 7.0",
    },
    "M03": {
        "implementation_steps": [
            "Events Manager → イベントテストで現在の EMQ スコアを確認",
            "EMQ 7 未満の場合: ハッシュ化 email を全イベントに必須化",
            "電話番号、外部 ID、fbc/fbp も追加してマッチ精度向上",
            "PII 送信のプライバシーポリシー記載確認",
        ],
        "verification": "Purchase イベント EMQ 8.0+ / AddToCart 7.0+ に到達",
    },
    "M04": {
        "implementation_steps": [
            "ビジネス設定 → ブランドセーフティ → ドメイン検証ページを開く",
            "対象ドメイン（LP の親ドメイン）を追加",
            "メタタグ / DNS TXT レコード / HTML ファイルアップロードのいずれかで認証",
            "認証完了後、AEM で優先度イベントを 8 件まで設定",
        ],
        "verification": "ドメイン検証ステータスが「確認済み」/ AEM 優先度イベント表示が有効",
    },
    "M09": {
        "implementation_steps": [
            "学習中の広告セット一覧を Ads Manager で抽出",
            "予算 / ターゲット / 最適化イベントを 7 日間変更しないことを徹底",
            "週 50CV 未満の広告セットは集約検討（同類似ペルソナを統合）",
            "Advantage+ Audience の有効化で学習速度を約 30% 加速可能",
        ],
        "verification": "学習フェーズ → 「最適化済み」へ遷移 / 週次 CV ≥ 50",
    },
    "M61": {
        "implementation_steps": [
            "Customer File（顧客リスト CSV）を月次で再アップロード",
            "ハッシュ化メール / 電話 / 住所のマッチ率 60%+ を確認",
            "Lookalike Audience は LTV Top 1〜5% で seed",
            "新規獲得時は既存顧客を除外オーディエンスに設定",
        ],
        "verification": "Audiences 画面でマッチ率表示 / LLA seed の更新日が直近 30 日以内",
    },
    "M47": {
        "implementation_steps": [
            "現状の広告セット内アクティブ CR を Ads Manager でカウント",
            "15 本未満の場合: Hook 訴求軸 × フォーマット で組み合わせ生成",
            "DCO（Dynamic Creative Optimization）を有効化して自動組み合わせ最適化",
            "週次の CR 入替サイクルを業務に組み込む",
        ],
        "verification": "ASC 内アクティブ CR ≥ 15 / Frequency が前週比で低下",
    },
    "M57": {
        "implementation_steps": [
            "Ads Manager で Frequency 3.5+ の広告セットを抽出",
            "新 CR を投入（最低 5 本）または対象オーディエンスを 1.5 倍以上拡大",
            "リターゲティング広告の場合: 顧客除外設定を見直し",
            "週次で Frequency と CTR の相関をモニタリング",
        ],
        "verification": "Frequency 3.0 以下 / CTR が回復傾向（ベースライン比 +10%）",
    },
    # === Google 計測系 ===
    "G01": {
        "implementation_steps": [
            "Google Ads → ツール → 測定 → コンバージョンで重複定義を確認",
            "1 件の事業価値（例: 購入完了）に対して複数 CV アクションが定義されていないか",
            "重複していたら 1 つに統合し、他は「セカンダリ」へ降格",
            "Tag Assistant で実 Web ページでの発火数を検証",
        ],
        "verification": "Conversions タブで主要 CV の合計が想定発火回数 ±5% 以内",
    },
    "G05": {
        "implementation_steps": [
            "Tag Assistant（Chrome 拡張）で対象 LP を開き発火状況を確認",
            "エラーが出ているタグ ID を特定",
            "GTM / 直貼りのコード差分を比較し、設置漏れ / 構文エラーを修正",
            "公開後、Tag Assistant で再検証",
        ],
        "verification": "全主要 CV ページで Tag Assistant エラー 0 件",
    },
    "G27": {
        "implementation_steps": [
            "検索語句レポートを過去 30 日でエクスポート",
            "意図と無関係なクエリを抽出（例: 「無料」「やり方」等）",
            "ネガティブキーワードリストに追加（共有リスト推奨）",
            "週次で繰り返してネガリストを成長させる",
        ],
        "verification": "翌週以降の検索語句レポートで除外 KW のクリック発生 0 件",
    },
    "G12": {
        "implementation_steps": [
            "Smart Bidding 学習中のキャンペーンを一覧化",
            "予算 / 入札戦略 / CV 設定を 14 日間変更しない",
            "週 30 CV 未満の場合: キャンペーン統合または予算増額",
            "学習完了後、tCPA / tROAS の段階的調整を再開",
        ],
        "verification": "学習ステータス → 「最適化済み」/ CPA の週次変動 < 15%",
    },
    "G34": {
        "implementation_steps": [
            "RSA 広告強度 < 「優」の広告グループを抽出",
            "短い見出し（10〜12 字）を 5 本以上追加",
            "説明文も訴求軸ごとに 4 本以上揃える",
            "ピン留めは最小限（Brand 名のみ等）",
        ],
        "verification": "全主要 RSA で広告強度「優」/ CTR ベースライン比 +10%",
    },
    # === TikTok 計測系 ===
    "T01": {
        "implementation_steps": [
            "TikTok Events Manager で Pixel 発火状況を確認",
            "未発火の場合: TikTok Pixel Helper で実画面検証",
            "GTM / サイト埋め込みコードを確認・修正",
            "再公開後、Test Events タブで動作確認",
        ],
        "verification": "Test Events で全主要イベント受信 / Pixel ヘルス「Active」",
    },
    "T02": {
        "implementation_steps": [
            "Events API（CAPI 相当）の実装状況を確認",
            "未実装ならサーバー側で TikTok Events API を実装、event_id でデデュプ",
            "ハッシュ化メール / 電話番号送信を必須化",
            "実装後、Events Manager で「Pixel + Events API」両方の発火を確認",
        ],
        "verification": "Events Manager で重複なし / Match Quality スコア向上",
    },
    "T13": {
        "implementation_steps": [
            "疲弊している広告のフリークエンシー / CTR 推移を確認",
            "週 5 本以上の新 CR を投入（縦型 9:16、9〜15 秒、サウンド ON 設計）",
            "Spark Ads でオーガニック投稿を CR 化（疲弊しにくい）",
            "TikTok Creative Center でトレンド要素を取り込み",
        ],
        "verification": "Frequency 低下 / CTR 回復（ベースライン比 +15%）",
    },
    # === 計測ロイヤルテンプレ（未定義ルール用） ===
    "_default_measurement": {
        "implementation_steps": [
            "Events Manager / Tag Assistant で対象計測タグの発火を確認",
            "計測欠損があれば実装を修正・再デプロイ",
            "再発火を Test Events で確認",
            "翌週の CV 数 / CPA に反映されているかモニタリング",
        ],
        "verification": "計測タグが正常発火 / 翌週レポートで欠損解消",
    },
    "_default_creative": {
        "implementation_steps": [
            "対象クリエイティブのパフォーマンス指標を確認",
            "新 CR の制作要件（フォーマット・尺・訴求）を整理",
            "週次で CR 入替を実施（最低週 3 本）",
            "Frequency と CTR の相関をモニタリング",
        ],
        "verification": "CTR 維持・改善 / Frequency 上昇の鈍化",
    },
    "_default_structure": {
        "implementation_steps": [
            "現状のキャンペーン / 広告セット構造を可視化",
            "学習データ希薄なグループを特定",
            "集約方針を策定し、変更を 1 回で実施（学習リセット最小化）",
            "再学習期間（7 日）後にパフォーマンス比較",
        ],
        "verification": "学習脱出加速 / CPA 安定性向上",
    },
}


def _get_playbook(rule: dict) -> dict:
    """ルール ID または category から playbook を取得する。

    v3.1 Task D: ルール定義に implementation_steps / verification_method /
    estimated_duration フィールドが直接書かれている場合はそちらを最優先で採用する。
    """
    # 1. YAML 直接定義（最優先）
    if rule.get("implementation_steps") and rule.get("verification_method"):
        return {
            "implementation_steps": list(rule["implementation_steps"]),
            "verification": rule["verification_method"],
            "estimated_duration": rule.get("estimated_duration"),
        }
    # 2. RULE_PLAYBOOK（コード内定義）
    rid = rule.get("id", "")
    if rid in RULE_PLAYBOOK:
        pb = dict(RULE_PLAYBOOK[rid])
        pb.setdefault("estimated_duration", None)
        return pb
    # 3. category 別デフォルト
    cat = rule.get("category", "")
    if "計測" in cat:
        return {**RULE_PLAYBOOK["_default_measurement"], "estimated_duration": None}
    if cat == "クリエイティブ":
        return {**RULE_PLAYBOOK["_default_creative"], "estimated_duration": None}
    if cat in ("構造_設定", "予算_入札"):
        return {**RULE_PLAYBOOK["_default_structure"], "estimated_duration": None}
    return {**RULE_PLAYBOOK["_default_measurement"], "estimated_duration": None}


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


def _detect_data_quality_alerts(audit: dict) -> list[dict]:
    """v3.1 Task C: 監査データから動的に Critical Alert を生成する。

    検出ロジック:
    - cost > 0 かつ avg_roas == 0（または None）→ Conversion Value 未取得を疑う
    - cost > 0 かつ conversions == 0 → Pixel/CAPI 不発火を疑う

    各アラートには Meta Events Manager / CAPI の具体的な確認手順を併記する。
    "ROAS=0 は計測不備の可能性が高く、実際の収益貢献は別途評価が必要" という
    注記を必ず含める（営業時に誤った数値で説明してしまうリスクを避けるため）。
    """
    alerts: list[dict] = []
    ps = audit.get("platform_summary") or {}
    total_cost = float(audit.get("total_cost", 0) or 0)
    total_cv = float(audit.get("total_conversions", 0) or 0)

    roas_missing_msg_template = (
        "【⚠ ROAS 未計測リスク】コスト ¥{cost:,.0f} / CV {cv:.0f} 件は計測されていますが、"
        "収益額（Conversion Value / purchase_value）が 0 のため ROAS 評価が機能していません。\n"
        "▶ ROAS=0 は計測不備の可能性が高く、実際の収益貢献は別途評価が必要です。\n"
        "確認手順:\n"
        "①Meta Events Manager → 該当 Pixel → 「テストイベント」で Purchase イベントの value パラメータが入っているか確認\n"
        "②サイト埋め込み Pixel コードに value: <購入金額> が含まれているか検証\n"
        "③CAPI 実装している場合は、サーバ送信ペイロードで custom_data.value と currency が送信されているか確認\n"
        "④Events Manager の「データソース」→「コンバージョン API ヘルス」で「value 受信あり」表示を確認"
    )

    cv_zero_msg_template = (
        "【⚠ CV 計測不能リスク】コスト ¥{cost:,.0f} に対して CV 数 0 件。"
        "Pixel/CAPI の発火または計測タグ実装に問題がある可能性が高い。\n"
        "▶ 配信実績はあるため、計測修復後に正しい CPA / ROAS が見えてきます。\n"
        "確認手順:\n"
        "①Meta Pixel Helper（Chrome 拡張）で実 LP を開き、Purchase / Lead / CompleteRegistration 等の発火を目視確認\n"
        "②Events Manager → 「概要」タブで直近 24h の主要イベント受信件数を確認\n"
        "③ドメイン検証ステータスを確認（未検証なら AEM が機能せず CV 集計欠損）\n"
        "④優先度イベント設定で 8 件まで重要 CV を順位付け（iOS14 以降は最優先イベントのみ計測される）"
    )

    for pkey, s in ps.items():
        cost = float(s.get("cost", 0) or 0)
        roas = s.get("avg_roas")
        cv = s.get("conversions") or 0
        if cost <= 0:
            continue

        # ROAS 未計測（cost あるが ROAS=0 or None）
        if roas in (0, 0.0, None) and cv > 0:
            alerts.append({
                "rule_id": "DQ-ROAS-MISSING",
                "rule_name": f"ROAS 未計測 ({pkey}) — Conversion Value (revenue) が取得できていません",
                "severity": "critical",
                "category": "計測_トラッキング",
                "platform": pkey,
                "redesign_note": roas_missing_msg_template.format(cost=cost, cv=cv),
                "quick_win": False,
            })

        # CV ゼロ（cost あるが CV=0）
        if cv == 0:
            alerts.append({
                "rule_id": "DQ-CV-ZERO",
                "rule_name": f"CV 計測不能 ({pkey}) — Pixel/CAPI 不発火または計測タグ未実装",
                "severity": "critical",
                "category": "計測_トラッキング",
                "platform": pkey,
                "redesign_note": cv_zero_msg_template.format(cost=cost),
                "quick_win": True,
            })

    # アカウント全体で revenue 0
    if total_cost > 0 and total_cv > 0 and (audit.get("avg_roas") in (0, 0.0, None)):
        if not any(a["rule_id"] == "DQ-ROAS-MISSING" for a in alerts):
            alerts.append({
                "rule_id": "DQ-ROAS-ACCOUNT",
                "rule_name": "アカウント全体 ROAS 未計測 — revenue 列が空",
                "severity": "critical",
                "category": "計測_トラッキング",
                "platform": "all",
                "redesign_note": roas_missing_msg_template.format(cost=total_cost, cv=total_cv),
                "quick_win": False,
            })
    return alerts


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

        # v3.1: 具体的な実装ステップと確認方法を playbook から付与
        playbook = _get_playbook(rule)
        # narrative.steps が Claude API 由来でない（フォールバック）場合は playbook で上書き
        if narrative.get("_fallback") or not narrative.get("steps"):
            narrative["implementation_steps"] = playbook["implementation_steps"]
            narrative["verification"] = playbook["verification"]
        else:
            narrative["implementation_steps"] = [s.get("what", "") for s in narrative.get("steps", [])]
            narrative["verification"] = playbook["verification"]
        narrative["estimated_duration"] = playbook.get("estimated_duration")

        scenario = impact.get("scenario") or {}
        horizon = impact.get("impact_horizon_weeks") or 4

        enriched.append(
            {
                **action,
                "impact": impact,
                "narrative": narrative,
                "expected_savings_display": impact.get("estimated_savings_display"),
                "confidence_label": impact.get("confidence_label", "—"),
                "confidence_stars": impact.get("confidence_stars", "★☆☆"),
                "horizon_weeks": horizon,
                "horizon_label": _horizon_label(horizon),
                "principle_tag": principle_tag,
                "related_rule_ids": [action["rule_id"]],
                "calc_basis": impact.get("calc_basis", "monthly_spend"),
                # シナリオ別表示
                "scenario_conservative": f"¥{scenario.get('conservative_yen', 0):,}",
                "scenario_realistic": f"¥{scenario.get('realistic_yen', 0):,}",
                "scenario_optimistic": f"¥{scenario.get('optimistic_yen', 0):,}",
                "scenario_band_pct": scenario.get("band_pct", 40),
                # playbook 由来
                "implementation_steps": narrative["implementation_steps"],
                "verification": narrative["verification"],
                "estimated_duration": narrative.get("estimated_duration"),
            }
        )
    return enriched


def _horizon_label(weeks: int) -> str:
    """効果発現週数 → 期間ラベル"""
    if weeks <= 2:
        return f"即効（{weeks} 週）"
    if weeks <= 4:
        return f"短期（{weeks} 週）"
    if weeks <= 8:
        return f"中期（{weeks} 週）"
    return f"長期（{weeks}+ 週）"


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
    # v3.1: 重複排除付き集計（root_cause グループベース）
    dedup = aggregate_with_dedup(estimates, rules_by_id, weights)
    aggregate["dedup"] = dedup

    # v3.1.2 (Day 5.3 A-T3): Top5 + 3層インパクト統合表のために
    # 各アクションに per_estimate_with_factor 情報（group, factor, conservative/realistic/optimistic）を付与
    per_est = {p["rule_id"]: p for p in dedup.get("per_estimate_with_factor", [])}
    for a in actions:
        sc = (a.get("impact") or {}).get("scenario") or {}
        info = per_est.get(a.get("rule_id"), {})
        a["unified_table"] = {
            "group": info.get("group", "other"),
            "group_short": {
                "measurement_foundation": "MF",
                "delivery_learning_or_structure": "DLS",
                "creative_optimization": "CR",
                "budget_allocation": "BUD",
                "targeting": "TGT",
                "independent": "IND",
                "other": "—",
            }.get(info.get("group", "other"), "—"),
            "factor": info.get("factor", 1.0),
            "conservative": int(sc.get("conservative_yen", 0)),
            "realistic": int(sc.get("realistic_yen", 0)),
            "optimistic": int(sc.get("optimistic_yen", 0)),
        }

    # v3.1 Task F-5: pixel_health を取得（pilotton 等の clients.yaml から）
    from analyzers.ads_audit import detect_pixel_health
    pixels = (((client_cfg.get("ads") or {}).get("meta") or {}).get("pixels") or [])
    pixel_health = detect_pixel_health(pixels)

    # v3.1 Task F-3: 3層インパクト（最低値 / 現実値 / 上限値、pixel_health 連動）
    minimum = calculate_minimum_impact(estimates, rules_by_id, weights, pixel_health=pixel_health)
    realistic = calculate_realistic_impact(estimates, rules_by_id, weights, pixel_health=pixel_health)
    independent = calculate_independent_impact(estimates, rules_by_id, weights)
    aggregate["three_layer"] = {
        "minimum": minimum,
        "realistic": realistic,
        "independent": independent,
        "pixel_health": pixel_health,
    }

    # KPI 投影は最低値（最も保守的）を使う（営業時の過大評価を回避）
    aggregate_for_kpi = dict(aggregate)
    aggregate_for_kpi["total_savings_yen"] = minimum["total_yen"]
    kpi_proj = build_kpi_projection(audit, aggregate_for_kpi)

    # v3.2 (Codex 統合): 12 ヶ月長期効果予測 (lifecycle ベース)
    longterm = build_longterm_projection(actions, audit)

    critical_alerts = compute_critical_alerts(detected_ids, rules_by_id, weights)

    # === v3.1: 動的 Critical Alert: ROAS=0 検出 ===
    extra_alerts = _detect_data_quality_alerts(audit)
    if extra_alerts:
        critical_alerts = critical_alerts + extra_alerts

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

    # v3.1 Task F-7: グループ内 priority 順序ダイアグラム用データ
    # Top5 内に measurement_foundation のルールがあれば、そのグループの推奨実装順序を表示
    measurement_sequence = _build_measurement_priority_sequence(actions, rules_by_id)

    # v3.2 (Codex 統合): ページ番号動的化 (cover + summary + actions + longterm + insights + appendix = 6 固定 + 媒体別ページ数)
    total_pages = 6 + len(platforms)

    return {
        "client_id": client_id,
        "cover": cover,
        "summary": summary,
        "longterm": longterm,
        "actions": actions,
        "critical_alerts": critical_alerts,
        "platforms": platforms,
        "insights": insight_items,
        "appendix": appendix,
        "llm_stats": llm_stats,
        "measurement_sequence": measurement_sequence,
        "total_pages": total_pages,
        "footer_text": "本書は機密保持契約に基づき作成されました / © 2026 Zynect Media 株式会社",
    }


def _build_measurement_priority_sequence(actions: list[dict], rules_by_id: dict) -> dict | None:
    """v3.1 Task F-7: Top5 内に measurement_foundation グループのルールがある場合、
    そのグループの推奨実装順序を構築する。

    Returns:
        dict { steps: [...], current_ids: set, group_name: str } or None
    """
    top5_ids = {a.get("rule_id") for a in actions if a.get("rule_id")}
    # Top5 で measurement_foundation グループのルールを検出
    mf_in_top5 = []
    for rid in top5_ids:
        rule = rules_by_id.get(rid)
        if rule and rule.get("root_cause_group") == "measurement_foundation":
            mf_in_top5.append(rid)
    if not mf_in_top5:
        return None

    # measurement_foundation グループの全ルールを priority_in_group 順に並べる
    mf_all = []
    for rid, rule in rules_by_id.items():
        if rule.get("root_cause_group") == "measurement_foundation" and rule.get("priority_in_group"):
            mf_all.append({
                "id": rid,
                "name": rule.get("name", ""),
                "priority": rule.get("priority_in_group"),
                "is_in_top5": rid in mf_in_top5,
            })
    mf_all.sort(key=lambda x: x["priority"])

    return {
        "group_name": "計測基盤（Measurement Foundation）",
        "steps": mf_all[:7],  # 主要 7 ステップに制限（M01-M07）
        "current_ids": list(top5_ids & {x["id"] for x in mf_all}),
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
