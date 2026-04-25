"""広告監査 v4.0 — オーケストレータ (チェックモジュール + YAML スコアリング)

全チェックモジュールを呼び出し、YAMLルール評価エンジン経由でスコアリングする。
設計文書 v1.3 準拠: S = Σ(C_pass × W_sev × W_cat) / Σ(C_total × W_sev × W_cat) × 100
"""
import logging

log = logging.getLogger("bpo")

PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads"}


def run_audit(client_id, data, thresholds):
    """広告監査メイン — 全チェックモジュール呼び出し + スコアリング

    Args:
        client_id: クライアントID
        data: unified format データ
        thresholds: 閾値設定
    Returns:
        dict: 監査結果 (score, grade, issues, quick_wins, platform_summary 等)
    """
    campaigns = data.get("campaigns", [])
    if not campaigns:
        return {"score": 0, "grade": "F", "issues": [], "error": "No campaigns"}

    log.info(f"[{client_id}] 広告監査: {len(campaigns)} キャンペーン")

    # === 1. チェック実行 ===
    all_check_results = []

    # 共通チェック
    try:
        from analyzers.checks.common import run_common_checks
        common_results = run_common_checks(campaigns, thresholds)
        all_check_results.extend(common_results)
    except Exception as e:
        log.warning(f"共通チェックエラー: {e}")

    # Google チェック
    try:
        from analyzers.checks.google import run_google_checks
        google_results = run_google_checks(campaigns, thresholds)
        all_check_results.extend(google_results)
    except Exception as e:
        log.warning(f"Googleチェックエラー: {e}")

    # Meta チェック
    try:
        from analyzers.checks.meta import run_meta_checks
        pixel_status = data.get("pixel_status")
        meta_results = run_meta_checks(campaigns, thresholds, pixel_status)
        all_check_results.extend(meta_results)
    except Exception as e:
        log.warning(f"Metaチェックエラー: {e}")

    # TikTok チェック
    try:
        from analyzers.checks.tiktok import run_tiktok_checks
        tiktok_pixel = data.get("pixel_status")
        tiktok_results = run_tiktok_checks(campaigns, thresholds, tiktok_pixel)
        all_check_results.extend(tiktok_results)
    except Exception as e:
        log.warning(f"TikTokチェックエラー: {e}")

    # クロスプラットフォームチェック
    try:
        from analyzers.checks.cross import run_cross_checks
        cross_results = run_cross_checks(campaigns, thresholds)
        all_check_results.extend(cross_results)
    except Exception as e:
        log.warning(f"クロスチェックエラー: {e}")

    # === 2. YAML ルール評価 + スコアリング ===
    try:
        from engine.yaml_evaluator import evaluate_checks
        from engine.scorer import calc_platform_score, calc_cross_platform_score, calc_budget_shares

        severity_weights = thresholds.get("scoring", {}).get("severity_weights", {})

        # プラットフォーム別にチェック結果を分割
        platform_checks = {"google": [], "meta": [], "tiktok": []}
        for check in all_check_results:
            p = check.get("platform", "unknown")
            if p in platform_checks:
                platform_checks[p].append(check)

        platform_scores = {}
        platform_details = {}
        for platform, checks in platform_checks.items():
            if checks:
                eval_result = evaluate_checks(checks, platform, severity_weights)
                ps = calc_platform_score(eval_result)
                platform_scores[platform] = ps
                platform_details[platform] = eval_result.get("details", [])

        # 予算シェア加重平均
        budget_shares = calc_budget_shares(data)
        overall = calc_cross_platform_score(platform_scores, budget_shares)
        score = overall.get("score", 0)
        grade = overall.get("grade", "F")

    except Exception as e:
        log.warning(f"スコアリングエラー (フォールバック使用): {e}")
        # フォールバック: 簡易スコアリング
        failed = [c for c in all_check_results if not c.get("passed", True)]
        total = len(all_check_results) if all_check_results else 1
        score = round((1 - len(failed) / total) * 100, 1) if total > 0 else 0
        grade = _fallback_grade(score)
        platform_scores = {}
        platform_details = {}

    # === 3. 結果整理 ===
    issues = []
    quick_wins = []
    for check in all_check_results:
        if not check.get("passed", True) and check.get("message"):
            severity = check.get("severity", "medium")
            issue = {
                "id": check.get("id", ""),
                "campaign": check.get("campaign", ""),
                "platform": check.get("platform", ""),
                "issue": check.get("message", ""),
                "severity": severity,
                "action": _suggest_action(check),
            }
            issues.append(issue)

            # Quick Win: medium/low severity で修正しやすいもの
            if severity in ("medium", "low"):
                quick_wins.append({
                    "campaign": check.get("campaign", ""),
                    "platform": check.get("platform", ""),
                    "action": _suggest_action(check),
                })

    # プラットフォーム別サマリー
    platform_summary = _build_platform_summary(campaigns, issues, platform_scores)

    # 集計
    totals = data.get("totals", {})

    result = {
        "score": score,
        "grade": grade,
        "total_campaigns": len(campaigns),
        "total_cost": totals.get("total_cost", 0),
        "total_conversions": totals.get("total_conversions", 0),
        "avg_cpa": totals.get("avg_cpa", 0),
        "avg_ctr": totals.get("avg_ctr", 0),
        "total_checks": len(all_check_results),
        "passed_checks": len([c for c in all_check_results if c.get("passed", True)]),
        "failed_checks": len([c for c in all_check_results if not c.get("passed", True)]),
        "issues": sorted(issues, key=lambda x: _severity_order(x.get("severity", "low"))),
        "quick_wins": quick_wins[:10],
        "platform_summary": platform_summary,
        "platform_scores": platform_scores,
        "budget_shares": budget_shares if "budget_shares" in dir() else {},
    }

    log.info(f"[{client_id}] 監査完了: Score {score} ({grade}) / "
             f"チェック {result['total_checks']}件 / 問題 {result['failed_checks']}件")

    return result


def _build_platform_summary(campaigns, issues, platform_scores):
    """プラットフォーム別サマリーを構築"""
    summary = {}
    for camp in campaigns:
        p = camp.get("platform", "unknown")
        if p not in summary:
            summary[p] = {
                "campaigns": 0, "cost": 0, "conversions": 0, "roas": 0,
                "issues": 0, "critical": 0, "score": 0, "grade": "?",
            }
        summary[p]["campaigns"] += 1
        summary[p]["cost"] += camp.get("cost", 0)
        summary[p]["conversions"] += camp.get("conversions", 0)

    # issue count
    for issue in issues:
        p = issue.get("platform", "unknown")
        if p in summary:
            summary[p]["issues"] += 1
            if issue.get("severity") == "critical":
                summary[p]["critical"] += 1

    # ROAS & score
    for p, s in summary.items():
        if s["cost"] > 0:
            total_rev = sum(c.get("revenue", 0) for c in campaigns if c.get("platform") == p)
            s["roas"] = round(total_rev / s["cost"], 1) if s["cost"] > 0 else 0
        ps = platform_scores.get(p, {})
        s["score"] = ps.get("score", 0)
        s["grade"] = ps.get("grade", "?")

    return summary


def _suggest_action(check):
    """チェック結果からアクション推奨を生成"""
    check_id = check.get("id", "")
    actions = {
        "C01": "CTRを改善: 広告文のA/Bテスト、ターゲティング見直し",
        "C02": "ゼロCVキャンペーンの停止または予算削減を検討",
        "C04": "フリークエンシーキャップ設定またはオーディエンス拡大",
        "G05": "ブランド検索と非ブランド検索をキャンペーン分離",
        "G14": "ネガティブキーワードリストを作成して適用",
        "G17": "Broad Match には Smart Bidding を組み合わせ",
        "G20": "低QSキーワードの広告文・LP改善",
        "G36": "Smart Bidding (Target CPA/ROAS) への移行を検討",
        "G43": "Enhanced Conversions を有効化",
        "G48": "Data-Driven Attribution モデルに変更",
        "M-PI1": "Meta Pixel をウェブサイトに設置",
        "M-PI3": "Conversions API を設定",
        "M-ST3": "広告セット統合でCV数を集約、学習フェーズ脱出",
        "T-TC1": "TikTok Pixel を設置",
        "T-CR3": "冒頭3秒のフック改善、動画尺の最適化",
        "T-BL1": "広告グループ統合でCV集約、予算増額検討",
    }
    return actions.get(check_id, check.get("message", "詳細を確認してください"))


def _severity_order(severity):
    """ソート用: critical=0, high=1, medium=2, low=3"""
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(severity, 4)


def _fallback_grade(score):
    """フォールバック用グレード判定"""
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"
