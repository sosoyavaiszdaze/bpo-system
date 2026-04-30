#!/usr/bin/env python3
"""BPO System - Main Pipeline Orchestrator"""
import os
import sys
import json
import yaml
import logging
import glob
from datetime import datetime

# .env ファイルから環境変数を読み込み
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未インストール時はスキップ

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# ログ設定
os.makedirs(LOGS_DIR, exist_ok=True)
log = logging.getLogger("bpo")


def _setup_logging():
    """ロガーを初期化（二重登録防止）"""
    if log.handlers:
        return
    log_file = os.path.join(LOGS_DIR, f"{datetime.now():%Y-%m-%d}.log")
    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    log.addHandler(fh)
    log.addHandler(sh)


def load_config():
    path = os.path.join(CONFIG_DIR, "clients.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_thresholds():
    path = os.path.join(CONFIG_DIR, "thresholds.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_data(client_id, client_cfg):
    """データ取得: 3媒体を全て取得しmerge → CSV fallback"""
    log.info(f"[{client_id}] データ取得開始")

    ads_cfg = client_cfg.get("ads", {})
    all_campaigns = []
    sources = []
    pixel_statuses = {}

    # Google Ads API
    google_cfg = ads_cfg.get("google", {})
    if google_cfg.get("customer_id") and google_cfg.get("customer_id") != "XXX-XXX-XXXX":
        try:
            from adapters.google_adapter import fetch_google_ads
            data = fetch_google_ads(google_cfg)
            if data and data.get("campaigns"):
                sources.append("google_api")
                all_campaigns.extend(data["campaigns"])
                log.info(f"[{client_id}] Google Ads API: {len(data['campaigns'])}キャンペーン取得")
        except Exception as e:
            log.warning(f"[{client_id}] Google Ads API失敗: {e}")

    # Meta API
    meta_cfg = ads_cfg.get("meta", {})
    if meta_cfg.get("account_id") and meta_cfg.get("account_id") != "act_XXXXXXXXX":
        try:
            from adapters.meta_adapter import fetch_meta_ads
            data = fetch_meta_ads(meta_cfg)
            if data and data.get("campaigns"):
                sources.append("meta_api")
                all_campaigns.extend(data["campaigns"])
                if data.get("pixel_status"):
                    pixel_statuses["meta"] = data["pixel_status"]
                log.info(f"[{client_id}] Meta API: {len(data['campaigns'])}キャンペーン取得")
        except Exception as e:
            log.warning(f"[{client_id}] Meta API失敗: {e}")

    # TikTok API
    tiktok_cfg = ads_cfg.get("tiktok", {})
    if tiktok_cfg.get("advertiser_id") and tiktok_cfg.get("advertiser_id") != "XXXXXXXXX":
        try:
            from adapters.tiktok_adapter import fetch_tiktok_ads
            data = fetch_tiktok_ads(tiktok_cfg)
            if data and data.get("campaigns"):
                sources.append("tiktok_api")
                all_campaigns.extend(data["campaigns"])
                if data.get("pixel_status"):
                    pixel_statuses["tiktok"] = data["pixel_status"]
                log.info(f"[{client_id}] TikTok API: {len(data['campaigns'])}キャンペーン取得")
        except Exception as e:
            log.warning(f"[{client_id}] TikTok API失敗: {e}")

    # API経由でデータ取得できた場合はmergeして返す
    if all_campaigns:
        merged = {
            "source": "+".join(sources),
            "campaigns": all_campaigns,
            "pixel_statuses": pixel_statuses,
        }
        return _validate(merged)

    # CSV fallback（APIが全て失敗した場合のみ）
    csv_pattern = os.path.join(DATA_DIR, f"{client_id}*.csv")
    csv_files = sorted(glob.glob(csv_pattern))
    if csv_files:
        from adapters.csv_adapter import load_csv
        latest = csv_files[-1]
        log.info(f"[{client_id}] CSV読込: {latest}")
        return _validate(load_csv(latest))

    log.warning(f"[{client_id}] データなし")
    return None


def _validate(data):
    """データバリデーション"""
    try:
        from adapters.validator import validate_data
        return validate_data(data)
    except Exception as e:
        log.warning(f"バリデーションエラー: {e}")
        return data


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



def run_fraud_audit(client_id, data, thresholds):
    """AdTruth不正検知"""
    log.info(f"[{client_id}] AdTruth不正検知開始")
    try:
        from analyzers.fraud_audit import run_fraud_audit as fraud_run
        return fraud_run(client_id, data, thresholds)
    except Exception as e:
        log.error(f"[{client_id}] AdTruth監査エラー: {e}")
        return {"error": str(e)}

def run_seo_audit(client_id, client_cfg, thresholds):
    """SEO監査"""
    seo_cfg = client_cfg.get("seo", {})
    if not seo_cfg.get("site_url"):
        log.info(f"[{client_id}] SEO設定なし、スキップ")
        return None
    log.info(f"[{client_id}] SEO監査開始")
    try:
        from seo.seo_audit import run_seo_audit as seo_run
        return seo_run(client_id, seo_cfg, thresholds)
    except Exception as e:
        log.error(f"[{client_id}] SEO監査エラー: {e}")
        return {"error": str(e)}


def output_results(client_id, client_cfg, results):
    """結果出力: Slack, CRM, PDF, JSON。各ステップの成否をstep_statusに記録。"""
    step_status = results.get("step_status", {})

    # レポートディレクトリ
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(REPORTS_DIR, today)
    os.makedirs(report_dir, exist_ok=True)

    # Slack通知
    notif_cfg = client_cfg.get("notifications", {}).get("slack", {})
    if notif_cfg.get("webhook_env") or notif_cfg.get("webhook_url"):
        try:
            from outputs.slack_notify import send_notification
            send_notification(client_id, results, notif_cfg)
            step_status["slack"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] Slack通知エラー: {e}")
            step_status["slack"] = "error"
    else:
        step_status["slack"] = "skipped"

    # CRM保存（TwentyCRM統合版）
    crm_cfg = client_cfg.get("crm", {}).get("twenty", {})
    if crm_cfg.get("enabled"):
        try:
            from outputs.crm_twenty import TwentyCRM
            crm = TwentyCRM()
            crm.save_health_snapshot(client_id, results)
            step_status["crm"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] CRM保存エラー: {e}")
            step_status["crm"] = "error"
    else:
        step_status["crm"] = "skipped"

    # PDF/HTML生成
    try:
        from outputs.pdf_report import generate_pdf
        pdf_path = os.path.join(report_dir, f"{client_id}_report.pdf")
        generate_pdf(client_id, results, pdf_path)
        step_status["pdf"] = "ok"
    except Exception as e:
        log.error(f"[{client_id}] PDF生成エラー: {e}")
        step_status["pdf"] = "error"

    # JSON保存（最後に実行: step_statusが全て揃った状態で保存）
    json_path = os.path.join(report_dir, f"{client_id}_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"[{client_id}] JSON保存: {json_path}")


def _phase_extract(client_id, client_cfg):
    """Phase 1: Extract — データ取得"""
    data = fetch_data(client_id, client_cfg)
    return data


def _phase_analyze(client_id, client_cfg, data, thresholds):
    """Phase 2: Analyze — 監査・異常検知・Fraud・トレードオフ・SEO"""
    results = {}
    step_status = {}

    # 2a. 広告監査
    results["ads_audit"] = run_ads_audit(client_id, data, thresholds)
    step_status["ads_audit"] = "ok" if results["ads_audit"] and not results["ads_audit"].get("error") else "error"

    # 2b. 異常検知
    results["anomalies"] = run_anomaly_detection(client_id, data, thresholds)
    step_status["anomaly"] = "ok" if results["anomalies"] and not results["anomalies"].get("error") else "error"

    # 2c. 低効率セグメント検出
    results["waste"] = run_waste_detection(client_id, data, thresholds)
    step_status["waste"] = "ok" if results["waste"] and not results["waste"].get("error") else "error"

    # 2d. Fraud 監査 + Ingest + アクション
    results["fraud_audit"] = run_fraud_audit(client_id, data, thresholds)
    step_status["fraud_audit"] = "ok" if results["fraud_audit"] and not results["fraud_audit"].get("error") else "error"

    if step_status["fraud_audit"] == "ok":
        try:
            from analyzers.fraud_ingest import ingest_fraud_data
            fraud_data = ingest_fraud_data(client_id, client_cfg, data)
            # fraud_audit の fraud_rate を fraud_data に統合（fraud_action が参照）
            if results["fraud_audit"].get("fraud_rate"):
                fraud_data["fraud_rate"] = results["fraud_audit"]["fraud_rate"]
            from analyzers.fraud_action import run_fraud_action as fraud_action_run
            results["fraud_action"] = fraud_action_run(client_id, fraud_data, client_cfg, thresholds)
            step_status["fraud_action"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] Fraud Action エラー: {e}")
            step_status["fraud_action"] = "error"
    else:
        results["fraud_action"] = None
        step_status["fraud_action"] = "skipped"

    # 2e. トレードオフ検出
    results["conflicts"] = None
    if step_status["ads_audit"] == "ok":
        try:
            from engine.conflict_detector import detect_conflicts, resolve_conflicts
            conflicts = detect_conflicts(results["ads_audit"], client_cfg)
            if conflicts:
                results["conflicts"] = resolve_conflicts(conflicts, client_cfg)
                log.info(f"[{client_id}] トレードオフ検出: {len(conflicts)}件")
            step_status["conflicts"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] トレードオフ検出エラー: {e}")
            step_status["conflicts"] = "error"
    else:
        step_status["conflicts"] = "skipped"

    # 2f. Claude API 分析
    results["claude_analysis"] = None
    if step_status["ads_audit"] == "ok":
        try:
            from engine.claude_analyzer import run_claude_analysis
            results["claude_analysis"] = run_claude_analysis(client_id, data, results["ads_audit"])
            step_status["claude"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] Claude分析エラー: {e}")
            step_status["claude"] = "error"
    else:
        step_status["claude"] = "skipped"

    # 2g. SEO監査
    results["seo_audit"] = run_seo_audit(client_id, client_cfg, thresholds)
    step_status["seo"] = "ok" if results["seo_audit"] and not results["seo_audit"].get("error") else "skipped"

    results["step_status"] = step_status
    return results


def _phase_report(client_id, client_cfg, results):
    """Phase 3: Report — 結果出力 + Twenty CRM保存"""
    output_results(client_id, client_cfg, results)


def run_client(client_id, client_cfg, thresholds):
    """1クライアント分の全パイプライン実行（3フェーズ: Extract → Analyze → Report）"""
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
        "fraud_audit": None,
        "fraud_action": None,
        "conflicts": None,
        "claude_analysis": None,
        "seo_audit": None,
        "step_status": {},
    }

    # Phase 1: Extract
    data = _phase_extract(client_id, client_cfg)
    if not data:
        log.warning(f"[{client_id}] データ取得失敗、スキップ")
        results["error"] = "No data available"
        results["step_status"]["extract"] = "error"
        _phase_report(client_id, client_cfg, results)
        return results
    results["step_status"]["extract"] = "ok"

    # Phase 2: Analyze
    analyze_results = _phase_analyze(client_id, client_cfg, data, thresholds)
    results.update(analyze_results)

    # Phase 3: Report
    _phase_report(client_id, client_cfg, results)

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
    _setup_logging()
    main()
