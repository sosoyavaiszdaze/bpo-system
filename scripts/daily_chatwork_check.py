"""日次 ChatWork 指摘・解消通知ジョブ (ADR-005 / Day 3 D1)

実行: venv/bin/python3 scripts/daily_chatwork_check.py [--client pilotton] [--dry-run] [--prefix "[テスト]"]

処理フロー:
  1. クライアント設定 (clients.yaml) と最新監査結果 (pipeline) を取得
  2. analyzer 出力 → indication_detector で統一 indication 候補へ変換
  3. IndicationState.upsert_detected で前回状態と差分マージ
  4. 検知されなかった既存 active 指摘 → mark_clean (data_available フラグ付き)
  5. resolved_confirmed に到達した分 → completion_notice 投稿
  6. 新規 indication → indication_filter (severity/cap/cooldown) → daily_indication 投稿
  7. すべての投稿後に state を save、失敗時は ChatWork に critical 自己監視通知

エラーハンドリング方針:
- analyzer / API 失敗時はログに critical 出力 + ChatWork へ自己監視メッセージ
- 部分失敗（例: 1 件投稿失敗）でも残り件数は処理を続行
- state は最後にまとめて save。例外時も保存試行（finally）

呼び出し方:
- 手動: scripts/daily_chatwork_check.py --client pilotton
- スケジューラから: integrations/scheduler.py が import して呼び出し
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from engine.indication_state import IndicationState
from engine.indication_detector import detect_and_upsert, reconcile_clean
from engine.indication_filter import filter_indications
from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError
from templates.chatwork import render

log = logging.getLogger("daily_chatwork")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ---------- analyzer 結果取得 ----------

def fetch_audit_results(client_id: str) -> dict:
    """指定クライアントの監査結果を取得 (pipeline の主要ステップを呼び出し)

    pipeline.py の run_client は副作用（ファイル出力）が大きいため、
    必要な analyzer 関数だけを直接呼ぶ。
    """
    from pipeline import (
        load_config,
        load_thresholds,
        fetch_data,
        run_ads_audit,
        run_anomaly_detection,
        run_fraud_audit,
    )
    cfg = load_config()
    thr = load_thresholds()
    client_cfg = cfg.get("clients", {}).get(client_id)
    if not client_cfg:
        raise ValueError(f"clients.yaml に {client_id} 未定義")

    data = fetch_data(client_id, client_cfg)
    if not data:
        log.warning(f"{client_id}: データ取得失敗 (data_available=False)")
        return {"data_available": False}

    return {
        "data_available": True,
        "ads_audit": run_ads_audit(client_id, data, thr),
        "anomalies": run_anomaly_detection(client_id, data, thr),
        "fraud_audit": run_fraud_audit(client_id, data, thr),
    }


# ---------- 通知用ヘルパ ----------

def _severity_label(sev: str) -> str:
    return {
        "critical": "緊急", "high": "高",
        "medium": "中", "low": "低",
    }.get(sev, sev)


def _to_indication_render_item(rec: dict) -> dict:
    """state record → daily_indication.md.j2 indications[] 要素"""
    payload = rec.get("payload") or {}
    return {
        "title": payload.get("title") or rec.get("rule_id", ""),
        "rule_id": rec.get("rule_id", ""),
        "severity_label": _severity_label(rec.get("severity", "medium")),
        "fact": payload.get("fact") or "(事実情報なし)",
        "impact": payload.get("impact") or "(影響情報なし)",
        "expected_effect": payload.get("expected_effect"),
        "scenario_label": payload.get("scenario_label", "現実シナリオ"),
        "payload": payload,
    }


def _to_completion_render_item(rec: dict) -> dict:
    """state record → completion_notice.md.j2 completions[] 要素"""
    payload = rec.get("payload") or {}
    return {
        "title": payload.get("completion_title")
                 or payload.get("title")
                 or f"{rec.get('rule_id', '')} 解消",
        "rule_id": rec.get("rule_id", ""),
        "first_reported_at": rec.get("first_detected_date", ""),
        "resolved_at": rec.get("resolved_date", ""),
        "before_state": payload.get("before_state") or payload.get("fact") or "(指摘時状態の記録なし)",
        "after_state": payload.get("after_state") or "(解消後状態の記録なし — 指摘条件が3日連続で再現せず)",
        "consecutive_clean_days": rec.get("consecutive_clean_days", 0),
        "achieved_effect": payload.get("achieved_effect") or {
            "minimum": "次回月次レポートにて算定",
            "realistic": "次回月次レポートにて算定",
        },
        "note": payload.get("note"),
    }


# ---------- メインフロー ----------

def run_daily_check(
    client_id: str,
    dry_run: bool = False,
    test_prefix: str = "",
    today: Optional[str] = None,
) -> dict:
    """日次チェックの本処理

    Returns:
        {"posted_indications": int, "posted_completions": int, "errors": [...]}
    """
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    state = IndicationState(client_id=client_id)

    log.info(f"日次 ChatWork チェック開始: client={client_id} dry_run={dry_run} prefix='{test_prefix}'")

    # 1. analyzer 結果取得
    try:
        audit = fetch_audit_results(client_id)
    except Exception as e:
        log.error(f"監査データ取得失敗: {e}\n{traceback.format_exc()}")
        return {"errors": [f"audit_fetch: {e}"], "posted_indications": 0, "posted_completions": 0}

    data_available = audit.get("data_available", False)
    if not data_available:
        log.warning("データ未取得につき clean カウントは進めません (data_available=False)")

    # 2-3. detect & upsert
    upserted, clean_candidates = detect_and_upsert(audit, state, today=today_str)
    log.info(f"検知: {len(upserted)} 件 upsert / clean 候補: {len(clean_candidates)} 件")

    # 4. clean 反映
    cleaned = reconcile_clean(
        clean_candidates, state, today=today_str,
        data_available=data_available,
    )
    log.info(f"clean 反映: {len(cleaned)} 件")

    # ChatWork クライアント
    client_display = test_prefix + ("株式会社パイロットン" if client_id == "pilotton" else client_id)
    client_display = client_display.strip()
    chat = ChatWorkClient(dry_run=dry_run)

    posted_indications = 0
    posted_completions = 0
    errors: list[str] = []

    # 5. 完了通知 (resolved_confirmed && completion_notified_at IS NULL)
    pending_completion = state.list_pending_completion_notification()
    if pending_completion:
        log.info(f"完了通知対象: {len(pending_completion)} 件")
        completion_items = [_to_completion_render_item(r) for r in pending_completion]
        try:
            body = render("completion_notice.md.j2", {
                "client_display_name": client_display,
                "date": today_str,
                "completions": completion_items,
            })
            result = chat.post_message(body)
            if not result.get("skipped"):
                posted_completions = len(pending_completion)
                # 通知済みマーク
                for r in pending_completion:
                    state.mark_completion_notified(r["indication_id"], today=today_str)
            else:
                log.info(f"完了通知スキップ (idempotency hit): key={result.get('idempotency_key', '')[:12]}")
        except (ChatWorkError, Exception) as e:
            log.error(f"完了通知投稿失敗: {e}")
            errors.append(f"completion_post: {e}")

    # 6. 新規指摘通知 (filter 適用)
    notify_targets = filter_indications(upserted, state, today=today_str)
    if notify_targets:
        log.info(f"新規指摘通知対象: {len(notify_targets)} 件 (filter 後)")
        indication_items = [_to_indication_render_item(r) for r in notify_targets]
        try:
            body = render("daily_indication.md.j2", {
                "client_display_name": client_display,
                "date": today_str,
                "greeting": None,
                "indications": indication_items,
                "footer_note": None,
            })
            result = chat.post_message(body)
            if not result.get("skipped"):
                posted_indications = len(notify_targets)
                for r in notify_targets:
                    state.mark_indication_notified(r["indication_id"], today=today_str)
            else:
                log.info(f"指摘通知スキップ (idempotency hit): key={result.get('idempotency_key', '')[:12]}")
        except (ChatWorkError, Exception) as e:
            log.error(f"指摘通知投稿失敗: {e}")
            errors.append(f"indication_post: {e}")

    # 7. state 保存
    state.save()

    # 8. ADR-013 多層ルール (Layer 0/1/2/3 = 248 ルール) の自動提案サイクル
    #    既存フロー (Layer A 277 ルール) と独立して走らせる。テンプレ未整備のルールは
    #    templates.chatwork.render() の generic フォールバックで通知される。
    auto_proposal_summary = {"posted_count": 0, "eligible_count": 0, "loaded_rules_count": 0}
    try:
        from engine.auto_proposal_engine import run_auto_proposal
        auto_proposal_summary = run_auto_proposal(
            client_id=client_id,
            dry_run=dry_run,
            today=today_str,
        )
        log.info(
            f"auto_proposal: loaded={auto_proposal_summary['loaded_rules_count']} "
            f"eligible={auto_proposal_summary['eligible_count']} "
            f"posted={auto_proposal_summary['posted_count']}"
        )
    except Exception as e:
        log.error(f"auto_proposal 失敗 (既存フローには影響なし): {e}")
        errors.append(f"auto_proposal: {e}")

    summary = {
        "posted_indications": posted_indications,
        "posted_completions": posted_completions,
        "auto_proposal_loaded": auto_proposal_summary.get("loaded_rules_count", 0),
        "auto_proposal_eligible": auto_proposal_summary.get("eligible_count", 0),
        "auto_proposal_posted": auto_proposal_summary.get("posted_count", 0),
        "errors": errors,
    }
    log.info(f"日次チェック完了: {summary}")
    return summary


# ---------- 自己監視 ----------

def post_self_alert(message: str, dry_run: bool = False) -> None:
    """致命的失敗時の自己監視 critical 投稿

    トークン未設定や ChatWork 自体が落ちている場合は失敗するが、
    その場合は呼び出し元でログに残るので追加処理は不要。
    """
    try:
        chat = ChatWorkClient(dry_run=dry_run)
        body = (
            "[info][title]🚨 BPO System 自己監視アラート[/title]"
            f"日次 ChatWork チェックが致命的に失敗しました。\n\n"
            f"発生時刻: {datetime.now().isoformat(timespec='seconds')}\n"
            f"内容:\n{message}\n\n"
            "対応: ログを確認し原因を特定してください。"
            "[/info]"
        )
        # idempotency_key を時刻で作成（同じエラーは1日1回まで）
        key = f"self_alert:{datetime.now().strftime('%Y-%m-%d')}:{hash(message) & 0xFFFF:x}"
        chat.post_message(body, idempotency_key=key)
    except Exception as e:
        log.error(f"自己監視通知さえ失敗: {e}")


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="日次 ChatWork 指摘・解消通知ジョブ")
    parser.add_argument("--client", default="pilotton", help="クライアントID")
    parser.add_argument("--dry-run", action="store_true", help="ChatWork に投稿しない")
    parser.add_argument(
        "--prefix", default="",
        help='client_display_name のプレフィクス (例: "[テスト] ")',
    )
    parser.add_argument("--today", default=None, help="シミュレーション日 (YYYY-MM-DD)")
    args = parser.parse_args()

    if not os.environ.get("CHATWORK_API_TOKEN"):
        log.error("CHATWORK_API_TOKEN 未設定。.env を確認してください。")
        return 1
    if not os.environ.get("CHATWORK_ROOM_ID_PILOTTON"):
        log.error("CHATWORK_ROOM_ID_PILOTTON 未設定。")
        return 1

    try:
        result = run_daily_check(
            client_id=args.client,
            dry_run=args.dry_run,
            test_prefix=args.prefix,
            today=args.today,
        )
        if result["errors"]:
            log.warning(f"部分失敗あり: {result['errors']}")
            return 3
        return 0
    except Exception as e:
        log.error(f"致命的失敗: {e}\n{traceback.format_exc()}")
        if not args.dry_run:
            post_self_alert(f"{e}\n\n{traceback.format_exc()[:1500]}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
