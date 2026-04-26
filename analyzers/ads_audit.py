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
    budget_shares = {}
    try:
        from engine.yaml_evaluator import evaluate_checks
        from engine.scorer import calc_platform_score, calc_cross_platform_score, calc_budget_shares

        severity_weights = thresholds.get("scoring", {}).get("severity_weights", {})

        # プラットフォーム別にチェック結果を分割（crossも含む）
        platform_checks = {"google": [], "meta": [], "tiktok": [], "cross": []}
        for check in all_check_results:
            p = check.get("platform", "unknown")
            if p in platform_checks:
                platform_checks[p].append(check)
            elif p == "seo":
                pass  # SEOは別経路でスコアリング
            else:
                platform_checks["cross"].append(check)

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
    # YAML ルール定義のseverityをcheck IDごとにlookup（最初の一致を使用）
    yaml_severity_map = {}
    yaml_conflict_map = {}
    for details in platform_details.values():
        for d in details:
            did = d.get("id", "")
            if did and did not in yaml_severity_map:
                yaml_severity_map[did] = d.get("severity", "medium")
            if did and d.get("conflict_group") and did not in yaml_conflict_map:
                yaml_conflict_map[did] = d["conflict_group"]

    issues = []
    quick_wins = []
    for check in all_check_results:
        if not check.get("passed", True) and check.get("message"):
            check_id = check.get("id", "")
            # YAML severity を優先、check自身のseverity、fallback medium
            severity = yaml_severity_map.get(check_id, check.get("severity", "medium"))
            issue = {
                "id": check_id,
                "campaign": check.get("campaign", ""),
                "platform": check.get("platform", ""),
                "issue": check.get("message", ""),
                "severity": severity,
                "action": _suggest_action(check),
            }
            # conflict_group を保持（check由来 or YAML由来）
            cg = check.get("conflict_group") or yaml_conflict_map.get(check_id)
            if cg:
                issue["conflict_group"] = cg

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
        "budget_shares": budget_shares,
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
        # Common (C01-C15)
        "C01": "CTRを改善: 広告文のA/Bテスト、ターゲティング見直し",
        "C02": "ゼロCVキャンペーンの停止または予算削減を検討",
        "C03": "ROAS改善: LP最適化、入札戦略見直し、ターゲティング精緻化",
        "C04": "フリークエンシーキャップ設定またはオーディエンス拡大",
        "C05": "CPA急騰キャンペーンの入札・ターゲティングを個別確認",
        "C06": "コスト集中キャンペーンの予算を他キャンペーンに再配分",
        "C07": "学習フェーズ達成のため広告グループ統合・予算増額を検討",
        "C08": "CPM急騰の原因調査: 競合激化・配信面変更・オーディエンス枯渇",
        "C09": "インプレッション急減の原因確認: 予算・入札・審査ステータス",
        "C10": "赤字キャンペーンの即時停止または大幅な改善策を実施",
        "C11": "キャンペーン数削減: 類似目的のキャンペーンを統合",
        "C12": "停止キャンペーンを削除またはアーカイブ",
        "C13": "予算制約の緩和: 日予算の増額または入札引き下げ",
        "C14": "CVR改善: LP改善、ターゲティング見直し、フォーム最適化",
        "C15": "ROAS低下キャンペーンのLP・クリエイティブ・入札を見直し",
        # Google (G01-G60+)
        "G01": "キャンペーン命名規則を統一: [媒体]_[目的]_[ターゲット]_[日付]",
        "G03": "STAG構造に整理: 1広告グループ15KW以下に分割",
        "G04": "同一タイプのキャンペーンを統合して学習効率を向上",
        "G05": "ブランド検索と非ブランド検索をキャンペーン分離",
        "G07": "PMaxにブランドKWネガティブ設定でカニバリ防止",
        "G08": "予算制約キャンペーンの日予算を増額",
        "G09": "非効率な予算集中を是正: ROAS基準で再配分",
        "G11": "地理ターゲティングを 'People in' に変更",
        "G12": "Search Partners のパフォーマンスを検証し除外を検討",
        "G13": "検索語句レポートを定期レビュー (週次推奨)",
        "G14": "ネガティブキーワードリストを作成して適用",
        "G15": "共有ネガティブKWリストをキャンペーンに適用",
        "G16": "無駄クリック削減: 検索語句レポートからネガKW追加",
        "G17": "Broad Match には Smart Bidding を組み合わせ",
        "G20": "低QSキーワードの広告文・LP改善",
        "G21": "QS≤3キーワードの広告文・LP関連性を改善",
        "G26": "RSA広告を最低1つ作成",
        "G27": "RSAヘッドラインを8個以上に拡充",
        "G28": "RSA説明文を4個以上に拡充",
        "G29": "Ad Strengthを改善: ヘッドライン多様化・KW挿入",
        "G31": "PMaxアセット補充: 画像20+、ロゴ5+、動画5+",
        "G32": "動画クリエイティブを追加してエンゲージメント向上",
        "G36": "Smart Bidding (Target CPA/ROAS) への移行を検討",
        "G37": "目標CPA/ROAS と実績の乖離を調整",
        "G38": "学習フェーズ中: CV集約のためキャンペーン統合を検討",
        "G39": "予算制約キャンペーンの日予算増額を推奨",
        "G40": "Manual CPC から Smart Bidding への移行を推奨",
        "G43": "Enhanced Conversions を有効化",
        "G45": "Consent Mode v2 を実装 (EU/UK向け必須)",
        "G47": "マクロ/マイクロCVアクションを分離設定",
        "G48": "Data-Driven Attribution モデルに変更",
        "G49": "コンバージョン値を設定して value-based bidding を有効化",
        "G50": "サイトリンクを4個以上設定",
        "G51": "コールアウトを4個以上設定",
        "G52": "構造化スニペットを設定",
        "G53": "画像拡張を設定",
        "G56": "オーディエンスセグメントを追加",
        "G57": "Customer Match リストをアップロード",
        "G58": "低品質プレースメントを除外設定",
        "G-PM1": "PMaxオーディエンスシグナルを設定",
        "G-PM3": "PMaxにブランドKW除外を設定",
        "G-PM4": "PMax検索テーマを追加",
        "G-PM5": "PMaxアカウントレベルのネガKWを設定",
        "G-PM6": "PMaxからブランドKWを除外",
        "G-CT2": "GA4とGoogle Adsを連携",
        "G-WS1": "ゼロCVキーワード群を停止または見直し",
        "G-KW1": "ゼロインプレッションKWを整理・削除",
        # Meta (M-PI/CR/ST/AU/C)
        "M-PI1": "Meta Pixel をウェブサイトに設置",
        "M-PI2": "EMQ改善: CAPIパラメータ追加、ハッシュ化データ送信",
        "M-PI3": "Conversions API を設定",
        "M-PI4": "標準イベント (Purchase/Lead/AddToCart) を設定",
        "M-PI5": "CAPI+Pixel の重複排除を設定",
        "M-CR1": "クリエイティブを10種以上に拡充",
        "M-CR2": "動画クリエイティブを追加して静止画+動画の混合に",
        "M-CR3": "クリエイティブ入替でフリークエンシー疲弊を解消",
        "M-CR4": "21日超のクリエイティブを新素材に差し替え",
        "M-CR5": "Reels/UGC クリエイティブを追加",
        "M-CR6": "Dynamic Creative Optimization を有効化",
        "M-ST1": "同一目的キャンペーンを統合",
        "M-ST2": "広告セット数を5以下に統合してCV学習を効率化",
        "M-ST3": "広告セット統合でCV数を集約、学習フェーズ脱出",
        "M-ST5": "CBO (Campaign Budget Optimization) を有効化",
        "M-ST6": "日予算をCPAの5倍以上に設定",
        "M-AU1": "オーディエンスオーバーラップを解消: 除外設定追加",
        "M-AU2": "カスタムオーディエンスを作成・設定",
        "M-AU4": "既存顧客の除外オーディエンスを設定",
        "M-AU6": "ファーストパーティデータをアップロード",
        "M-C01": "アトリビューション設定を確認・最適化",
        "M-C03": "コスト上限または入札上限を設定してCPA暴騰を防止",
        "M-C04": "地域ターゲティングを 'People living' に変更",
        # TikTok (T-TC/CR/BL/ST/C)
        "T-TC1": "TikTok Pixel を設置",
        "T-TC2": "ttclid パスバックを設定",
        "T-CR1": "TikTokは動画必須: 動画クリエイティブを作成",
        "T-CR2": "動画尺を9-30秒に調整",
        "T-CR3": "冒頭3秒のフック改善、動画尺の最適化",
        "T-CR5": "Hook率改善: 冒頭3秒のインパクトを強化",
        "T-CR7": "クリエイティブを広告グループあたり5本以上に増量",
        "T-CR8": "SmartVideo/Creative Centerテンプレートを活用",
        "T-BL1": "広告グループ統合でCV集約、予算増額検討",
        "T-BL2": "予算制約を緩和: 日予算増額または入札調整",
        "T-BL3": "入札戦略を見直し: Cost Cap/Target CPA への移行を検討",
        "T-ST1": "キャンペーン命名規則を統一",
        "T-ST3": "リターゲティングとプロスペクティングを分離",
        "T-C01": "最適化イベントをCV系イベントに変更",
    }
    return actions.get(check_id, check.get("message", "詳細を確認してください"))


def _severity_order(severity):
    """ソート用: critical=0, high=1, medium/warning=2, low=3"""
    return {"critical": 0, "high": 1, "medium": 2, "warning": 2, "low": 3}.get(severity, 4)


def _fallback_grade(score):
    """フォールバック用グレード判定"""
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"
