"""広告監査 v4.0 — オーケストレータ (チェックモジュール + YAML スコアリング)

全チェックモジュールを呼び出し、YAMLルール評価エンジン経由でスコアリングする。
設計文書 v1.3 準拠: S = Σ(C_pass × W_sev × W_cat) / Σ(C_total × W_sev × W_cat) × 100
"""
import os
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

    # === 1. チェック実行（レジストリ経由で全モジュール呼び出し） ===
    try:
        from analyzers.registry import run_all_checks
        all_check_results = run_all_checks(campaigns, thresholds, data)
    except Exception as e:
        log.warning(f"レジストリチェックエラー、フォールバック使用: {e}")
        all_check_results = _fallback_checks(campaigns, thresholds, data)



    # === 2. YAML ルール評価 + スコアリング（3層エラーハンドリング） ===
    budget_shares = {}
    platform_scores = {}
    platform_details = {}
    platform_errors = {}

    # Layer 1: モジュールインポート
    try:
        from engine.yaml_evaluator import evaluate_checks
        from engine.scorer import calc_platform_score, calc_cross_platform_score, calc_budget_shares
    except ImportError as e:
        log.error(f"エンジンモジュールのインポート失敗: {e}")
        failed = [c for c in all_check_results if not c.get("passed", True)]
        total = len(all_check_results) if all_check_results else 1
        score = round((1 - len(failed) / total) * 100, 1) if total > 0 else 0
        grade = _fallback_grade(score)
        # 以降の処理をスキップして結果整理へ
        evaluate_checks = None

    if evaluate_checks:
        severity_weights = thresholds.get("scoring", {}).get("severity_weights", {})

        # プラットフォーム別にチェック結果を分割（crossも含む）
        platform_checks = {"google": [], "meta": [], "tiktok": [], "cross": [], "adtruth": []}
        for check in all_check_results:
            p = check.get("platform", "unknown")
            if p in platform_checks:
                platform_checks[p].append(check)
            elif p == "seo":
                pass  # SEOは別経路でスコアリング
            else:
                platform_checks["cross"].append(check)

        # Layer 2: プラットフォーム別評価（1つ失敗しても他は続行）
        for platform, checks in platform_checks.items():
            if not checks:
                continue
            try:
                eval_result = evaluate_checks(checks, platform, severity_weights)
                ps = calc_platform_score(eval_result)
                platform_scores[platform] = ps
                platform_details[platform] = eval_result.get("details", [])
            except Exception as e:
                log.warning(f"[{platform}] 評価エラー (スキップ): {e}", exc_info=True)
                platform_errors[platform] = str(e)

        # Layer 3: クロスプラットフォーム集計
        try:
            budget_shares = calc_budget_shares(data)
            overall = calc_cross_platform_score(platform_scores, budget_shares)
            score = overall.get("score", 0)
            grade = overall.get("grade", "F")
        except Exception as e:
            log.warning(f"クロスプラットフォーム集計エラー: {e}", exc_info=True)
            if platform_scores:
                scores = [ps.get("score", 0) for ps in platform_scores.values()]
                score = round(sum(scores) / len(scores), 1)
                grade = _fallback_grade(score)
            else:
                score, grade = 0, "F"

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
        # v2.0: 全platform_detailsを統合（axis conflict検出用）
        "all_details": [d for details in platform_details.values() for d in details],
        "platform_errors": platform_errors,
    }

    # v2.0: 軸ベース矛盾検出
    try:
        from engine.conflict_detector import detect_axis_conflicts
        result["axis_conflicts"] = detect_axis_conflicts(result.get("all_details", []))
    except Exception as e:
        log.warning(f"軸矛盾検出エラー: {e}", exc_info=True)
        result["axis_conflicts"] = {"hard": [], "potential": []}

    # v2.0: conflict_group ベースの矛盾検出 + 自動解決
    try:
        from engine.conflict_detector import detect_conflicts, resolve_conflicts
        client_cfg = data.get("client_config", {})
        if not client_cfg:
            try:
                import yaml as _yaml
                cfg_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "config", "clients.yaml"
                )
                with open(cfg_path, "r", encoding="utf-8") as f:
                    all_clients = _yaml.safe_load(f) or {}
                client_cfg = all_clients.get("clients", {}).get(client_id, {})
            except Exception:
                client_cfg = {"objective": "balanced"}
        conflicts = detect_conflicts(result, client_cfg)
        resolved = resolve_conflicts(conflicts, client_cfg)
        result["conflicts"] = resolved
        result["conflict_count"] = len(resolved)
    except Exception as e:
        log.warning(f"矛盾検出/解決エラー: {e}", exc_info=True)
        result["conflicts"] = []
        result["conflict_count"] = 0

    # v2.0: ルールカバレッジ分析
    try:
        from engine.rule_coverage import analyze_coverage
        coverage = analyze_coverage(all_check_results)
        result["rule_coverage"] = coverage
        if coverage["coverage_percent"] < 80:
            result["score_note"] = (
                f"注意: ルールカバレッジ {coverage['coverage_percent']}%。"
                f"未実装の{len(coverage['uncovered_critical'])}件のcriticalルールがあります"
            )
    except Exception as e:
        log.warning(f"カバレッジ分析エラー: {e}")

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
        # ── Common (C01-C15) ──
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
        # ── Google (YAML IDs) ──
        "G02": "コンバージョンカテゴリ設定: マクロ/マイクロ分離推奨",
        "G03": "Enhanced Conversions を有効化",
        "G04": "GA4とGoogle Adsを連携",
        "G06": "コンバージョン値を設定して value-based bidding を有効化",
        "G08": "Data-Driven Attribution モデルに変更",
        "G09": "オーディエンスセグメントを追加",
        "G09b": "Customer Match リストをアップロード",
        "G11": "Smart Bidding (Target CPA/ROAS) への移行を検討",
        "G11b": "目標CPA/ROAS と実績の乖離を調整",
        "G11c": "Manual CPC から Smart Bidding への移行を推奨",
        "G12": "学習フェーズ中: CV集約のためキャンペーン統合を検討",
        "G13": "予算制約キャンペーンの日予算を増額",
        "G13b": "予算制約(Limited by Budget): 日予算増額を推奨",
        "G15": "予算配分バランスを是正: ROAS基準で再配分",
        "G17": "同一タイプのキャンペーンを統合して学習効率を向上",
        "G20": "地理ターゲティングを 'People in' に変更",
        "G22": "サイトリンクを4個以上設定",
        "G22b": "コールアウトを4個以上設定",
        "G22c": "構造化スニペットを設定",
        "G22d": "画像拡張を設定",
        "G25": "キャンペーン命名規則を統一: [媒体]_[目的]_[ターゲット]_[日付]",
        "G26": "品質スコアをモニタリング（結果指標として参照のみ）",
        "G26b": "QS≤3キーワードの広告文・LP関連性を改善",
        "G26c": "Expected CTR改善: 広告文の訴求力を強化",
        "G26d": "Ad Relevance改善: KWと広告文の関連性を向上",
        "G26e": "LP Experience改善: ページ速度とコンテンツ品質を向上",
        "G27": "ネガティブキーワードリストを作成して適用",
        "G27b": "共有ネガティブKWリストをキャンペーンに適用",
        "G28": "検索語句レポートを定期レビュー (週次推奨)",
        "G29": "マッチタイプの偏りを改善: Smart Bidding+部分一致推奨",
        "G31": "ブランド検索と非ブランド検索をキャンペーン分離",
        "G32": "低インプレッションKWを整理・分析ノイズ低減",
        "G34": "Ad Strengthを改善: ヘッドライン多様化・KW挿入",
        "G37": "RSA見出し・説明文数を確認",
        "G37b": "RSAヘッドラインを8個以上に拡充",
        "G37c": "RSA説明文を4個以上に拡充",
        "G39": "広告グループあたりKW数を最適化 (≤15推奨)",
        "G41": "入札単価上限の妥当性を確認",
        "G42": "Search Partners のパフォーマンスを検証し除外を検討",
        "G53": "PMax+Search重複のカニバリ防止: ブランドKW除外",
        "G54": "低品質プレースメントを除外設定",
        "G59": "LP速度スコアを改善 (≥50推奨)",
        "G60": "LP関連性スコアを改善 (≥0.7推奨)",
        "G65": "PMaxアセット補充: 画像20+、ロゴ5+、動画5+",
        "G66": "動画クリエイティブを追加してエンゲージメント向上",
        "G68": "PMaxオーディエンスシグナルを設定",
        "G68b": "PMax Ad Strength: Good以上を目指す",
        "G68c": "PMax検索テーマを追加でシグナル精度向上",
        "G68d": "PMaxアカウントレベルのネガKWを設定",
        "G70": "PMaxにブランドKW除外を設定",
        "G70b": "PMaxブランドKW未除外: Searchとのカニバリ注意",
        "G74": "Demand Gen: 画像+動画の両方を推奨",
        "G75": "AI Max: パフォーマンス監視を推奨",
        "G76": "Video Action Campaign → Demand Genへの移行を推奨",
        "G77": "DGフリークエンシーキャップを設定",
        "G78": "CTV: Floodlight制限に注意",
        "G79": "ゼロCVキーワード群を停止または見直し",
        "G80": "無駄クリック削減: 検索語句レポートからネガKW追加",
        "G81": "Consent Mode v2 を実装 (EU/UK向け必須)",
        # ── Meta (YAML IDs) ──
        "M01": "Meta Pixel をウェブサイトに設置",
        "M02": "Conversions API を設定",
        "M03": "EMQ改善: CAPIパラメータ追加、ハッシュ化データ送信",
        "M04": "ドメイン検証を完了",
        "M05": "標準イベント (Purchase/Lead/AddToCart) を設定",
        "M06": "CAPI+Pixel の重複排除を設定",
        "M08": "iOS14+影響を計測・対策",
        "M09": "広告セット統合でCV数を集約、学習フェーズ脱出",
        "M11": "CBO (Campaign Budget Optimization) を有効化",
        "M12": "日予算をCPAの5倍以上に設定",
        "M14": "同一目的キャンペーンを統合",
        "M15": "広告セット数を5以下に統合してCV学習を効率化",
        "M19": "Business Manager検証を完了",
        "M24": "動画クリエイティブを追加して静止画+動画の混合に",
        "M35": "Reels/UGC クリエイティブを追加",
        "M44": "Advantage+カタログ整合を確認",
        "M45": "コスト上限または入札上限を設定してCPA暴騰を防止",
        "M47": "クリエイティブを10種以上に拡充",
        "M49": "オーディエンスオーバーラップを解消: 除外設定追加",
        "M50": "LLA(類似オーディエンス)の鮮度を確認 (1-3%推奨)",
        "M51": "カスタムオーディエンスを作成・設定",
        "M53": "既存顧客の除外オーディエンスを設定",
        "M54": "Advantage詳細ターゲット+設定を確認",
        "M56": "Aggregated Event Measurement を構成",
        "M57": "クリエイティブ入替でフリークエンシー疲弊を解消",
        "M58": "21日超のクリエイティブを新素材に差し替え",
        "M59": "Dynamic Creative Optimization を有効化",
        "M60": "Advantage+クリエイティブを有効化",
        "M61": "ファーストパーティデータをアップロード",
        "M62": "アトリビューション設定を確認・最適化",
        "M63": "配信最適化目標の妥当性を確認",
        "M64": "地域ターゲティングを 'People living' に変更",
        "M65": "支払い方法ステータスを確認",
        # ── TikTok (YAML IDs) ──
        "T01": "TikTok Pixel を設置",
        "T02": "Events API / ttclid パスバックを設定",
        "T03": "最適化イベントをCV系イベントに変更",
        "T06": "広告グループ統合でCV集約、予算増額検討",
        "T08": "入札戦略を見直し: Cost Cap/Target CPA への移行を検討",
        "T10": "キャンペーン目的の整合を確認",
        "T14": "クリエイティブを広告グループあたり5本以上に増量",
        "T15": "SmartVideo/Creative Centerテンプレートを活用",
        "T16": "動画尺を9-30秒に調整",
        "T19": "Hook率改善: 冒頭3秒のインパクトを強化",
        "T23": "Spark Ads権限を取得",
        "T30": "Pangle品質管理を強化",
        "T36": "TikTokは動画必須: 動画クリエイティブを作成",
        "T37": "動画完視聴率を改善 (≥15%推奨)",
        "T38": "テキストオーバーレイを追加して訴求力UP",
        "T39": "予算制約を緩和: 日予算増額または入札調整",
        "T40": "キャンペーン命名規則を統一",
        "T41": "リターゲティングとプロスペクティングを分離",
        "T42": "アプリキャンペーン: iOS/Android 分離推奨",
        "T43": "広告グループ数を最適化 (上限確認)",
        "T44": "重複ターゲティングを解消: オーディエンス分離",
        "T45": "Dayparting→All Dayに変更して学習優先",
        "T46": "自動入札+自動ターゲティングへの移行を推奨",
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


def _fallback_checks(campaigns, thresholds, data):
    """レジストリが使えない場合の直接import フォールバック"""
    results = []
    try:
        from analyzers.checks.common import run_common_checks
        results.extend(run_common_checks(campaigns, thresholds))
    except Exception as e:
        log.warning(f"共通チェックエラー: {e}")
    try:
        from analyzers.checks.google import run_google_checks
        results.extend(run_google_checks(campaigns, thresholds))
    except Exception as e:
        log.warning(f"Googleチェックエラー: {e}")
    try:
        from analyzers.checks.meta import run_meta_checks
        results.extend(run_meta_checks(campaigns, thresholds, data.get("pixel_status")))
    except Exception as e:
        log.warning(f"Metaチェックエラー: {e}")
    try:
        from analyzers.checks.tiktok import run_tiktok_checks
        results.extend(run_tiktok_checks(campaigns, thresholds, data.get("pixel_status")))
    except Exception as e:
        log.warning(f"TikTokチェックエラー: {e}")
    try:
        from analyzers.checks.cross import run_cross_checks
        results.extend(run_cross_checks(campaigns, thresholds))
    except Exception as e:
        log.warning(f"クロスチェックエラー: {e}")
    return results
