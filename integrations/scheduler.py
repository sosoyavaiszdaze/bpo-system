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

    log.info("スケジューラ起動: 週次監査(日曜02:00) / 日次Fraud(06:00) / 月次ベンチ(1日00:00)")
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


if __name__ == "__main__":
    start_scheduler()
