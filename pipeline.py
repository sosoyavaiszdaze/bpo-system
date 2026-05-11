#!/usr/bin/env python3
"""BPO System — 手動運用 / 単発レポート生成エントリポイント (ADR-016)

役割:
    手動でクライアント別の深い監査 + PDF レポート (v3) + Slack/Lark 通知 +
    CRM 同期を一気通貫で行う。月次提案資料、kickoff、要望対応に使う。

Phase A の launchd 自動運用には scripts/daily_chatwork_check.py を使うこと
(本ファイルは launchd から直接呼ばれない、ADR-016 §2.1)。

主要コマンド:
    python3 pipeline.py run all              # 全クライアント (v2 デフォルト)
    python3 pipeline.py run pilotton         # 特定クライアント
    python3 pipeline.py run pilotton --report-version v3   # v3 PDF
    python3 pipeline.py run pilotton --report-version both # v2+v3 並行
    python3 pipeline.py test                 # 設定チェック

共有モジュール:
    fetch_data / run_ads_audit / run_anomaly_detection / run_fraud_audit /
    run_seo_audit / run_waste_detection は scripts/daily_chatwork_check.py
    からも import される (ADR-016 §2.1 の共有規約)。
"""
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


def load_client_config(client_id):
    """クライアント設定をロード（CRM優先、YAMLフォールバック）"""
    from engine.models import ClientConfig

    # 1. CRM接続を試行
    if os.environ.get("TWENTY_API_URL") and os.environ.get("TWENTY_API_KEY"):
        try:
            from notifiers.crm_twenty import TwentyCRM
            crm = TwentyCRM()
            client = crm.get_client(client_id)
            if client:
                log.info(f"[{client_id}] クライアント設定: Twenty CRM から読み込み")
                return client
            log.debug(f"[{client_id}] CRMにクライアント未登録、YAMLフォールバック")
        except Exception as e:
            log.warning(f"Twenty CRM読み取り失敗: {e}、YAMLフォールバック")

    # 2. YAMLフォールバック
    cfg = load_config()
    client_data = cfg.get("clients", {}).get(client_id)
    if client_data:
        log.info(f"[{client_id}] クライアント設定: clients.yaml から読み込み")
        return ClientConfig.from_yaml(client_id, client_data)

    log.warning(f"[{client_id}] クライアント設定が見つかりません")
    return ClientConfig(client_id=client_id, name=client_id, source="default")


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
    platform_diagnostics = {}

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
                platform_diagnostics["meta"] = {
                    "connection_audit": data.get("meta_connection_audit") or {},
                    "rule_evidence": data.get("meta_rule_evidence") or {},
                    "rule_groups": data.get("meta_rule_groups") or {},
                    "account_id": data.get("account_id"),
                    "date_range": data.get("date_range") or {},
                }
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
            "platform_diagnostics": platform_diagnostics,
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


def _percent_to_fraction(value):
    """0-100 のパーセント表現を fraud_action 用の 0-1 率に正規化する。"""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return value
    return rate / 100 if rate > 1 else rate


def output_results(client_id, client_cfg, results, report_version="v2"):
    """結果出力: Slack, CRM, PDF, JSON。各ステップの成否をstep_statusに記録。

    report_version: "v2" | "v3" | "both"
        v2 のみ生成 / v3 のみ生成 / 両方生成
    """
    step_status = results.get("step_status", {})

    # レポートディレクトリ
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(REPORTS_DIR, today)
    os.makedirs(report_dir, exist_ok=True)

    # 通知（Slack or Lark）
    notif_cfg = client_cfg.get("notifications", {})
    notification_platform = notif_cfg.get("platform", "slack")
    slack_cfg = notif_cfg.get("slack", {})
    lark_cfg = notif_cfg.get("lark", {})

    if notification_platform == "lark":
        if not (lark_cfg.get("webhook_env") or lark_cfg.get("lark_webhook_env")):
            log.info(f"[{client_id}] Lark通知未設定のためスキップ")
            step_status["lark"] = "skipped"
            step_status["slack"] = "skipped"
        else:
            try:
                from notifiers.lark_notify import send_lark_notification
                ok = send_lark_notification(client_id, results, lark_cfg)
                if ok is True:
                    step_status["lark"] = "ok"
                elif ok is False:
                    step_status["lark"] = "error"
                else:
                    step_status["lark"] = "skipped"
            except Exception as e:
                log.error(f"[{client_id}] Lark通知エラー: {e}")
                step_status["lark"] = "error"
            step_status["slack"] = "skipped"
    elif slack_cfg.get("webhook_env") or slack_cfg.get("webhook_url"):
        try:
            from notifiers.slack_notify import send_notification
            ok = send_notification(client_id, results, slack_cfg)
            if ok is True:
                step_status["slack"] = "ok"
            elif ok is False:
                step_status["slack"] = "error"
            else:
                step_status["slack"] = "skipped"
        except Exception as e:
            log.error(f"[{client_id}] Slack通知エラー: {e}")
            step_status["slack"] = "error"
        step_status["lark"] = "skipped"
    else:
        step_status["slack"] = "skipped"
        step_status["lark"] = "skipped"

    # CRM保存（TwentyCRM統合版）
    crm_cfg = client_cfg.get("crm", {}).get("twenty", {})
    if crm_cfg.get("enabled"):
        try:
            from notifiers.crm_twenty import TwentyCRM
            crm = TwentyCRM()
            if not crm.api_url or not crm.api_key:
                log.info(f"[{client_id}] CRM未設定（PhaseB以降のためスキップ）")
                step_status["crm"] = "skipped"
            else:
                note_id = crm.save_health_snapshot(client_id, results)
                if note_id:
                    step_status["crm"] = "ok"
                else:
                    log.warning(f"[{client_id}] CRM保存失敗")
                    step_status["crm"] = "error"
        except Exception as e:
            log.error(f"[{client_id}] CRM保存エラー: {e}")
            step_status["crm"] = "error"
    else:
        step_status["crm"] = "skipped"

    # PDF/HTML生成 — report_version に応じて v2 / v3 / 両方を生成
    if report_version in ("v2", "both"):
        try:
            from engine.pdf_report import generate_pdf
            pdf_path = os.path.join(report_dir, f"{client_id}_report.pdf")
            generate_pdf(client_id, results, pdf_path)
            step_status["pdf_v2"] = "ok"
        except Exception as e:
            log.error(f"[{client_id}] v2 PDF生成エラー: {e}")
            step_status["pdf_v2"] = "error"

    if report_version in ("v3", "both"):
        try:
            from engine.pdf_report_v3 import generate_pdf_v3
            pdf_path_v3 = os.path.join(report_dir, f"{client_id}_report_v3.pdf")
            ok = generate_pdf_v3(client_id, client_cfg, results, pdf_path_v3)
            step_status["pdf_v3"] = "ok" if ok else "error"
        except Exception as e:
            log.error(f"[{client_id}] v3 PDF生成エラー: {e}")
            step_status["pdf_v3"] = "error"

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
            if not isinstance(fraud_data, dict):
                log.warning(f"[{client_id}] fraud_ingest が dict を返さないためスキップ")
                results["fraud_action"] = None
                step_status["fraud_action"] = "skipped"
            else:
                fraud_rate = results["fraud_audit"].get("fraud_rate")
                if fraud_rate is not None:
                    fraud_data["fraud_rate"] = _percent_to_fraction(fraud_rate)
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


def _phase_report(client_id, client_cfg, results, report_version="v2"):
    """Phase 3: Report — 結果出力 + Twenty CRM保存"""
    output_results(client_id, client_cfg, results, report_version=report_version)


def run_client(client_id, client_cfg, thresholds, report_version="v2"):
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
        _phase_report(client_id, client_cfg, results, report_version=report_version)
        return results
    results["step_status"]["extract"] = "ok"

    # Phase 2: Analyze
    analyze_results = _phase_analyze(client_id, client_cfg, data, thresholds)
    results.update(analyze_results)

    # Phase 3: Report
    _phase_report(client_id, client_cfg, results, report_version=report_version)

    score = ""
    if results["ads_audit"] and "score" in results["ads_audit"]:
        score = f" Score: {results['ads_audit']['score']}"
    log.info(f"[{client_id}] パイプライン完了{score}")

    return results


def _parse_args(argv):
    """超軽量な引数パーサ。argparse を使うほどでもないため自作。

    Usage:
        pipeline.py test
        pipeline.py run <client_id|all> [--report-version v2|v3|both] [--dry-run]
    """
    args = list(argv[1:])
    report_version = "v2"  # 後方互換のためデフォルトは v2
    dry_run = False
    if "--dry-run" in args:
        dry_run = True
        args.remove("--dry-run")
    if "--report-version" in args:
        idx = args.index("--report-version")
        if idx + 1 < len(args):
            report_version = args[idx + 1].lower()
            del args[idx : idx + 2]
        else:
            print("Error: --report-version の値が指定されていません")
            sys.exit(1)
    if report_version not in ("v2", "v3", "both"):
        print(f"Error: --report-version='{report_version}' は v2|v3|both のいずれかで指定してください")
        sys.exit(1)
    return args, report_version, dry_run


def run_client_dry(client_id: str, client_cfg: dict) -> dict:
    """ドライラン: 設定検証 + Meta API 疎通のみ実施。データ取得・監査・レポート生成は行わない。"""
    log.info(f"[{client_id}] DRY-RUN 開始")
    out = {"client_id": client_id, "checks": {}}

    # 必須フィールド検証
    name = client_cfg.get("name") or (client_cfg.get("company") or {}).get("name", "")
    out["checks"]["name"] = "ok" if name else "missing"

    # Ads 設定確認
    ads_cfg = client_cfg.get("ads", {})
    out["checks"]["ads_platforms"] = list(ads_cfg.keys()) if ads_cfg else []

    # Meta API 疎通（access_token_env が設定されていれば）
    meta_cfg = ads_cfg.get("meta", {})
    if meta_cfg.get("account_id") and meta_cfg.get("access_token_env"):
        token = os.environ.get(meta_cfg["access_token_env"], "")
        if not token:
            out["checks"]["meta_api"] = f"token_env_unset: {meta_cfg['access_token_env']}"
        else:
            try:
                import urllib.request, urllib.parse, json as _json
                url = (
                    f"https://graph.facebook.com/v22.0/{meta_cfg['account_id']}?"
                    + urllib.parse.urlencode({"fields": "name,account_status", "access_token": token})
                )
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                out["checks"]["meta_api"] = f"ok / name={data.get('name')} / status={data.get('account_status')}"
            except Exception as e:
                out["checks"]["meta_api"] = f"error: {e}"

    # CRM / 通知の存在確認のみ
    out["checks"]["notification"] = (
        client_cfg.get("notifications", {}).get("platform") or
        ("slack" if client_cfg.get("notifications", {}).get("slack") else None) or
        "(none)"
    )
    out["checks"]["v3_company_block"] = "ok" if client_cfg.get("company", {}).get("name") else "missing(任意)"

    log.info(f"[{client_id}] DRY-RUN 結果: {out['checks']}")
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline.py run <client_id|all> [--report-version v2|v3|both]")
        print("       python3 pipeline.py test")
        sys.exit(1)

    args, report_version, dry_run = _parse_args(sys.argv)
    if not args:
        print("Usage: python3 pipeline.py run <client_id|all> [--report-version v2|v3|both]")
        sys.exit(1)
    command = args[0]

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

    if command != "run" or len(args) < 2:
        print("Usage: python3 pipeline.py run <client_id|all> [--report-version v2|v3|both]")
        sys.exit(1)

    target = args[1]
    cfg = load_config()
    thr = load_thresholds()
    clients = cfg.get("clients", {})

    if target == "all":
        target_ids = [k for k, v in clients.items() if v.get("active")]
    elif target in clients:
        target_ids = [target]
    else:
        loaded = load_client_config(target)
        if loaded.source == "default":
            log.error(f"クライアント '{target}' が見つかりません")
            sys.exit(1)
        target_ids = [target]

    targets = {}
    for cid in target_ids:
        ccfg = load_client_config(cid)
        if ccfg.source == "default":
            log.error(f"クライアント '{cid}' が見つかりません")
            sys.exit(1)
        targets[cid] = ccfg

    log.info(f"実行対象: {list(targets.keys())} / report_version={report_version} / dry_run={dry_run}")
    all_results = {}
    for cid, ccfg in targets.items():
        try:
            if dry_run:
                all_results[cid] = run_client_dry(cid, ccfg)
            else:
                all_results[cid] = run_client(cid, ccfg, thr, report_version=report_version)
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
