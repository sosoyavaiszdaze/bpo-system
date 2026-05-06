"""スケジューラ — APScheduler による定期実行"""
import os
import sys
import logging

log = logging.getLogger("bpo")


def start_scheduler():
    """スケジューラ起動"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("apscheduler 未インストール: pip install apscheduler")
        return

    scheduler = BlockingScheduler(timezone="Asia/Tokyo")

    # 週次フル監査: 日曜 02:00 JST
    scheduler.add_job(
        _run_full_audit,
        CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="weekly_full_audit",
        name="週次フル監査",
    )

    # 日次 Fraud スキャン: 毎日 06:00 JST
    scheduler.add_job(
        _run_fraud_scan,
        CronTrigger(hour=6, minute=0),
        id="daily_fraud_scan",
        name="日次Fraudスキャン",
    )

    # 月次ベンチマーク更新: 毎月1日 00:00 JST
    scheduler.add_job(
        _run_benchmark_update,
        CronTrigger(day=1, hour=0, minute=0),
        id="monthly_benchmark",
        name="月次ベンチマーク更新",
    )

    # 月次レポート生成: 毎月1日 03:00 JST
    scheduler.add_job(
        _run_monthly_crm_report,
        CronTrigger(day=1, hour=3, minute=0),
        id="monthly_crm_report",
        name="月次CRMレポート生成",
    )

    # 15分毎: Slack判断エスカレーション＆タイムアウト
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        scheduler.add_job(
            _run_judgment_escalation,
            IntervalTrigger(minutes=15),
            id="judgment_escalation",
            name="判断エスカレーション(15分毎)",
        )
    except Exception as e:
        log.warning(f"判断エスカレーションジョブ追加失敗: {e}")

    # 毎週月曜09:00: 学習レビュー → 閾値調整推奨
    scheduler.add_job(
        _run_weekly_learning_review,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_learning_review",
        name="週次学習レビュー",
    )

    # 日次 09:00 JST: ChatWork 指摘・解消通知 (ADR-005 / pilotton)
    scheduler.add_job(
        _run_daily_chatwork_check,
        CronTrigger(hour=9, minute=0),
        id="daily_chatwork_check",
        name="日次ChatWork指摘通知",
    )

    # 毎月1日 10:00 JST: ChatWork 月次レポート (ADR-005 / pilotton)
    scheduler.add_job(
        _run_monthly_chatwork_report,
        CronTrigger(day=1, hour=10, minute=0),
        id="monthly_chatwork_report",
        name="月次ChatWorkレポート",
    )

    log.info(
        "スケジューラ起動: 週次監査(日02:00) / 日次Fraud(06:00) / 月次ベンチ(1日00:00) / "
        "判断エスカ(15分毎) / 学習レビュー(月09:00) / "
        "日次ChatWork(09:00) / 月次ChatWork(1日10:00)"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("スケジューラ停止")


def _run_full_audit():
    """週次フル監査"""
    log.info("=== 週次フル監査開始 ===")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline import main as pipeline_main
    sys.argv = ["pipeline.py", "run", "all"]
    try:
        pipeline_main()
    except Exception as e:
        log.error(f"週次監査エラー: {e}")


def _run_fraud_scan():
    """日次 Fraud スキャン"""
    log.info("=== 日次Fraudスキャン開始 ===")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from pipeline import load_config, load_thresholds, fetch_data, run_fraud_audit
        cfg = load_config()
        thr = load_thresholds()
        for client_id, client_cfg in cfg.get("clients", {}).items():
            if not client_cfg.get("active"):
                continue
            data = fetch_data(client_id, client_cfg)
            if data:
                run_fraud_audit(client_id, data, thr)
    except Exception as e:
        log.error(f"日次Fraudスキャンエラー: {e}")


def _run_benchmark_update():
    """月次ベンチマーク更新"""
    log.info("=== 月次ベンチマーク更新 ===")
    try:
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "update_benchmarks.py"
        )
        if os.path.exists(script):
            exec(open(script).read())
        else:
            log.info("ベンチマーク更新スクリプト未検出、スキップ")
    except Exception as e:
        log.error(f"ベンチマーク更新エラー: {e}")


def _run_monthly_crm_report():
    """毎月1日: Twenty CRM 月次レポート生成"""
    log.info("=== 月次CRMレポート生成 ===")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from notifiers.crm_twenty import TwentyCRM
        from pipeline import load_config
        from datetime import datetime
        crm = TwentyCRM()
        cfg = load_config()
        month = datetime.now().strftime("%Y-%m")
        for client_id in cfg.get("clients", {}):
            report = crm.generate_monthly_report(client_id, month)
            crm.save_monthly_report(report)
            log.info(f"月次レポート保存: {client_id}/{month}")
    except Exception as e:
        log.error(f"月次CRMレポートエラー: {e}")


def _run_judgment_escalation():
    """15分毎: Slack判断のエスカレーション＆タイムアウト"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from analyzers.slack_judgment import check_and_escalate
        check_and_escalate()
    except Exception as e:
        log.error(f"判断エスカレーションエラー: {e}")


def _run_weekly_learning_review():
    """毎週月曜09:00: 学習データから閾値調整推奨を生成 + Slack送信"""
    log.info("=== 週次学習レビュー ===")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from analyzers.judgment_db import JudgmentDB
        import json
        import urllib.request

        db = JudgmentDB()
        recommendations = db.generate_threshold_adjustment_recommendations()
        stats = db.get_learning_stats()

        if not recommendations and not stats:
            log.info("学習レビュー: 推奨事項なし")
            return

        # レポート生成
        lines = ["# 週次学習レビュー"]
        if stats:
            lines.append("\n## 判断統計")
            for cat, actions in stats.items():
                total = sum(actions.values())
                lines.append(f"**{cat}** ({total}件): " + ", ".join(f"{a}={c}" for a, c in actions.items()))
        if recommendations:
            lines.append("\n## 閾値調整推奨")
            for r in recommendations:
                lines.append(f"- [{r['category']}] {r['suggestion']} (信頼度: {r['confidence']*100:.0f}%, サンプル: {r['sample_size']})")

        # Intent Override 期限チェック
        try:
            from engine.intent_filter import get_expiring_overrides
            from pipeline import load_config
            cfg = load_config()
            for cid in cfg.get("clients", {}):
                expiring = get_expiring_overrides(cid, days=30)
                if expiring:
                    lines.append(f"\n## Intent Override 期限切れ警告 ({cid})")
                    for o in expiring:
                        lines.append(f"- ルール {o.get('rule_ids')}: {o.get('reason')} (期限: {o.get('expires_at')})")
        except Exception as e:
            log.debug(f"Intent Override期限チェックスキップ: {e}")

        report_text = "\n".join(lines)
        log.info(report_text)

        # Slack送信
        webhook_url = os.environ.get("SLACK_FRAUD_JUDGMENT_WEBHOOK", "")
        if webhook_url:
            try:
                payload = json.dumps({"text": report_text}).encode("utf-8")
                req = urllib.request.Request(webhook_url, data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10):
                    log.info("週次学習レビューSlack送信完了")
            except Exception as e:
                log.warning(f"週次学習レビューSlack送信失敗: {e}")
    except Exception as e:
        log.error(f"週次学習レビューエラー: {e}")


def _run_daily_chatwork_check():
    """日次 09:00 JST: ChatWork 指摘・解消通知 (ADR-005)

    内部レビュー期間中 (最初の 2 週間) は CHATWORK_TEST_PREFIX="[テスト] " を
    .env に設定して投稿する。本番化時に空文字に切替。
    """
    log.info("=== 日次ChatWork指摘通知開始 ===")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from scripts.daily_chatwork_check import run_daily_check, post_self_alert
        prefix = os.environ.get("CHATWORK_TEST_PREFIX", "")
        # pilotton 単一クライアント運用 (Phase A 範囲)
        result = run_daily_check(client_id="pilotton", dry_run=False, test_prefix=prefix)
        if result.get("errors"):
            log.warning(f"日次ChatWork: 部分失敗 {result['errors']}")
    except Exception as e:
        log.error(f"日次ChatWorkエラー: {e}")
        try:
            from scripts.daily_chatwork_check import post_self_alert
            post_self_alert(f"日次ジョブ失敗: {e}")
        except Exception as e2:
            log.error(f"自己監視通知も失敗: {e2}")


def _run_monthly_chatwork_report():
    """毎月1日 10:00 JST: ChatWork 月次レポート (ADR-005)

    前月 (period 自動算定) を集計して投稿、PDF 添付、resolved_confirmed をアーカイブ。
    """
    log.info("=== 月次ChatWorkレポート開始 ===")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from scripts.monthly_chatwork_report import run_monthly_report
        prefix = os.environ.get("CHATWORK_TEST_PREFIX", "")
        result = run_monthly_report(
            client_id="pilotton",
            period=None,  # 自動: 前月
            dry_run=False,
            test_prefix=prefix,
            archive_after=True,
        )
        if result.get("errors"):
            log.warning(f"月次ChatWork: 部分失敗 {result['errors']}")
    except Exception as e:
        log.error(f"月次ChatWorkエラー: {e}")


if __name__ == "__main__":
    start_scheduler()
