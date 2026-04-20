#!/usr/bin/env python3
"""BPO System - Main Pipeline Orchestrator"""
import os
import sys
import json
import yaml
import logging
import glob
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# ログ設定
os.makedirs(LOGS_DIR, exist_ok=True)
log_file = os.path.join(LOGS_DIR, f"{datetime.now():%Y-%m-%d}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bpo")


def load_config():
    path = os.path.join(CONFIG_DIR, "clients.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_thresholds():
    path = os.path.join(CONFIG_DIR, "thresholds.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_data(client_id, client_cfg):
    """データ取得: API -> CSV fallback"""
    log.info(f"[{client_id}] データ取得開始")

    # Phase 1: CSV fallback (API連携は後で追加)
    csv_pattern = os.path.join(DATA_DIR, f"{client_id}*.csv")
    csv_files = sorted(glob.glob(csv_pattern))
    if csv_files:
        from adapters.csv_adapter import load_csv
        latest = csv_files[-1]
        log.info(f"[{client_id}] CSV読込: {latest}")
        return load_csv(latest)

    log.warning(f"[{client_id}] データなし")
    return None


def run_ads_audit(client_id, data, thresholds):
    """広告監査"""
    log.info(f"[{client_id}] 広告監査開始")
    try:
        from analyzers.ads_audit import run_audit
        return run_audit(client_id, data, thresholds)
    except Exception as e:
        log.error(f"[{client_id}] 広告監査エラー: {e}")
        return {"error": str(e)}


def run_anomaly_detection(client_id, data, thresholds):
    """異常検知"""
    log.info(f"[{client_id}] 異常検知開始")
    try:
        from analyzers.anomaly import detect_anomalies
        return detect_anomalies(client_id, data, thresholds)
    except Exception as e:
        log.error(f"[{client_id}] 異常検知エラー: {e}")
        return {"error": str(e)}


def run_waste_detection(client_id, data, thresholds):
    """低効率セグメント検出"""
    log.info(f"[{client_id}] 低効率セグメント検出開始")
    try:
        from analyzers.segment_waste import detect_waste
        return detect_waste(client_id, data, thresholds)
    except Exception as e:
        log.error(f"[{client_id}] 低効率検出エラー: {e}")
        return {"error": str(e)}


def run_seo_audit(client_id, client_cfg):
    """SEO監査"""
    seo_cfg = client_cfg.get("seo", {})
    if not seo_cfg.get("site_url"):
        log.info(f"[{client_id}] SEO設定なし、スキップ")
        return None
    log.info(f"[{client_id}] SEO監査開始")
    try:
        from seo.seo_audit import run_seo_audit as seo_run
        return seo_run(client_id, seo_cfg)
    except Exception as e:
        log.error(f"[{client_id}] SEO監査エラー: {e}")
        return {"error": str(e)}


def output_results(client_id, client_cfg, results):
    """結果出力: Slack, CRM, PDF"""
    # レポートディレクトリ
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(REPORTS_DIR, today)
    os.makedirs(report_dir, exist_ok=True)

    # JSON保存
    json_path = os.path.join(report_dir, f"{client_id}_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"[{client_id}] JSON保存: {json_path}")

    # Slack通知
    notif_cfg = client_cfg.get("notifications", {}).get("slack", {})
    if notif_cfg.get("webhook_env") or notif_cfg.get("webhook_url"):
        try:
            from outputs.slack_notify import send_notification
            send_notification(client_id, results, notif_cfg)
        except Exception as e:
            log.error(f"[{client_id}] Slack通知エラー: {e}")

    # CRM保存
    crm_cfg = client_cfg.get("crm", {}).get("twenty", {})
    if crm_cfg.get("enabled"):
        try:
            from outputs.crm_save import save_to_crm
            save_to_crm(client_id, results, crm_cfg)
        except Exception as e:
            log.error(f"[{client_id}] CRM保存エラー: {e}")

    # PDF生成
    try:
        from outputs.pdf_report import generate_pdf
        pdf_path = os.path.join(report_dir, f"{client_id}_report.pdf")
        generate_pdf(client_id, results, pdf_path)
    except Exception as e:
        log.error(f"[{client_id}] PDF生成エラー: {e}")


def run_client(client_id, client_cfg, thresholds):
    """1クライアント分の全パイプライン実行"""
    log.info(f"{'='*50}")
    log.info(f"[{client_id}] パイプライン開始: {client_cfg.get('name', '')}")
    log.info(f"{'='*50}")

    results = {
        "client_id": client_id,
        "client_name": client_cfg.get("name", ""),
        "timestamp": datetime.now().isoformat(),
        "ads_audit": None,
        "anomalies": None,
        "waste": None,
        "seo_audit": None,
    }

    # 1. データ取得
    data = fetch_data(client_id, client_cfg)
    if not data:
        log.warning(f"[{client_id}] データ取得失敗、スキップ")
        results["error"] = "No data available"
        output_results(client_id, client_cfg, results)
        return results

    # 2. 広告監査
    results["ads_audit"] = run_ads_audit(client_id, data, thresholds)

    # 3. 異常検知
    results["anomalies"] = run_anomaly_detection(client_id, data, thresholds)

    # 4. 低効率セグメント検出
    results["waste"] = run_waste_detection(client_id, data, thresholds)

    # 5. SEO監査
    results["seo_audit"] = run_seo_audit(client_id, client_cfg)

    # 6. 出力
    output_results(client_id, client_cfg, results)

    score = ""
    if results["ads_audit"] and "score" in results["ads_audit"]:
        score = f" Score: {results['ads_audit']['score']}"
    log.info(f"[{client_id}] パイプライン完了{score}")

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline.py run <client_id|all>")
        print("       python3 pipeline.py test")
        sys.exit(1)

    command = sys.argv[1]

    if command == "test":
        log.info("テストモード: config読み込みチェック")
        cfg = load_config()
        thr = load_thresholds()
        clients = cfg.get("clients", {})
        log.info(f"クライアント数: {len(clients)}")
        for cid, ccfg in clients.items():
            status = "ACTIVE" if ccfg.get("active") else "INACTIVE"
            log.info(f"  {cid}: {ccfg.get('name', '')} [{status}]")
        log.info("テスト完了")
        return

    if command != "run" or len(sys.argv) < 3:
        print("Usage: python3 pipeline.py run <client_id|all>")
        sys.exit(1)

    target = sys.argv[2]
    cfg = load_config()
    thr = load_thresholds()
    clients = cfg.get("clients", {})

    if target == "all":
        targets = {k: v for k, v in clients.items() if v.get("active")}
    elif target in clients:
        targets = {target: clients[target]}
    else:
        log.error(f"クライアント '{target}' が見つかりません")
        sys.exit(1)

    log.info(f"実行対象: {list(targets.keys())}")
    all_results = {}
    for cid, ccfg in targets.items():
        try:
            all_results[cid] = run_client(cid, ccfg, thr)
        except Exception as e:
            log.error(f"[{cid}] 致命的エラー: {e}")
            all_results[cid] = {"error": str(e)}

    # サマリー
    log.info(f"\n{'='*50}")
    log.info("全体サマリー")
    for cid, r in all_results.items():
        if "error" in r and isinstance(r["error"], str):
            log.info(f"  {cid}: ERROR - {r['error']}")
        else:
            score = r.get("ads_audit", {}).get("score", "N/A") if r.get("ads_audit") else "N/A"
            log.info(f"  {cid}: Score {score}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()
