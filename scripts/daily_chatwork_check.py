"""日次 ChatWork 指摘・解消通知ジョブ (ADR-005 / ADR-016 launchd 本番エントリ)

役割 (ADR-016 §2.1):
    launchd 9:00 / 9:15 / 9:30 の本番エントリポイント。クライアント別に最新監査を
    走らせ、ChatWork で運用指摘 / 完了通知 / auto_proposal (Layer 0-3 の 248 ルール)
    / AdTruth (灰/黒ゾーン) を投稿する。
    手動レポート生成 (PDF) は pipeline.py を使うこと、本ファイルは PDF を出さない。

(以下 ADR-005 当初の説明)


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
from engine.structured_logger import (
    install_structured_handler, StructuredLogContext, set_status, new_run_id,
)
from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError
from templates.chatwork import render

log = logging.getLogger("daily_chatwork")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# R4a: JSON line 構造化ログを追加 (logs/daily-chatwork.json.log)
# 既存 stdout / launchd ログには影響しない、opt-out は STRUCTURED_LOGS=0
install_structured_handler()


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
        "platform_diagnostics": data.get("platform_diagnostics") or {},
        "meta_rule_evidence": (
            (data.get("platform_diagnostics") or {})
            .get("meta", {})
            .get("rule_evidence", {})
        ),
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


def _to_completion_render_item(rec: dict, client_id: str = "", today_str: str = "") -> dict:
    """state record → completion_notice.md.j2 completions[] 要素"""
    payload = rec.get("payload") or {}
    followup = None
    if client_id and today_str:
        try:
            from engine.claude_hypothesis_engine import build_anomaly_followup
            followup = build_anomaly_followup(client_id, rec, today_str)
        except Exception as e:
            log.warning(f"anomaly followup 生成失敗: {rec.get('rule_id')} {e}")

    is_continued = bool(followup and followup.get("type") == "continued_issue")
    return {
        "title": payload.get("completion_title")
                 or payload.get("title")
                 or f"{rec.get('rule_id', '')} 解消",
        "rule_id": rec.get("rule_id", ""),
        "first_reported_at": rec.get("first_detected_date", ""),
        "resolved_at": rec.get("resolved_date", ""),
        "before_state": payload.get("before_state") or payload.get("fact") or "(指摘時状態の記録なし)",
        "after_state": (
            payload.get("after_state")
            or (
                "急変条件は3日連続で再発していません。ただし水準が戻ったとは限らないため、"
                "下記の継続課題を確認します。"
                if is_continued
                else "(解消後状態の記録なし — 指摘条件が3日連続で再現せず)"
            )
        ),
        "consecutive_clean_days": rec.get("consecutive_clean_days", 0),
        "achieved_effect": payload.get("achieved_effect") or {
            "minimum": "次回月次レポートにて算定",
            "realistic": "次回月次レポートにて算定",
        },
        "note": payload.get("note"),
        "followup": followup,
        "is_continued_issue": is_continued,
    }


# ---------- メインフロー ----------

def run_daily_check(
    client_id: str,
    dry_run: bool = False,
    test_prefix: str = "",
    today: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """日次チェックの本処理

    R4a: 構造化ログ context を関数全体に注入。run_id は呼出ごとに発行。
    各ステップでの step 付与は将来の段階的拡張で順次組込む (Phase B)。

    Returns:
        {"posted_indications": int, "posted_completions": int, "errors": [...]}
    """
    run_id = new_run_id()
    job_run_id = None
    db_start_error = None
    if not dry_run:
        try:
            from engine.stores.db import transaction
            from engine.stores.jobs import start_job
            with transaction(db_path) as conn:
                job_run_id = start_job(conn, "daily_chatwork_check", client_id)
        except Exception as e:
            db_start_error = f"operational_job_start: {e}"
            log.error(f"operational DB job start failed: {e}")

    with StructuredLogContext(run_id=run_id, client_id=client_id, step="run_daily_check"):
        try:
            result = _run_daily_check_impl(
                client_id, dry_run, test_prefix, today, run_id, db_path,
            )
        except Exception as e:
            if job_run_id and not dry_run:
                _finish_operational_job(db_path, job_run_id, "failed", [str(e)], {})
            raise

    if db_start_error:
        result.setdefault("errors", []).append(db_start_error)
    if job_run_id and not dry_run:
        status = "success" if not result.get("errors") else "partial_failure"
        metrics = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        _finish_operational_job(db_path, job_run_id, status, result.get("errors") or [], metrics)
    return result


def _run_daily_check_impl(
    client_id: str, dry_run: bool, test_prefix: str,
    today: Optional[str], run_id: str, db_path: Optional[Path] = None,
) -> dict:
    """run_daily_check の実装本体 (StructuredLogContext 内で呼ばれる)"""
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    state = IndicationState(client_id=client_id)

    log.info(f"日次 ChatWork チェック開始: client={client_id} dry_run={dry_run} prefix='{test_prefix}' run_id={run_id}")

    # 0. ChatWork 顧客回答の取り込み (5/7 P3: 通知前に必ず実行)
    #
    # ここで顧客の前日/当日朝までの返信を outputs/chatwork_responses/{client}.yaml に
    # 反映する。これがあって初めて、本日の TODO 生成 (build_daily_todo) で
    # confirmed_done な rule を本文から外し、wants_help な rule を [詳細案内]
    # プレフィクスに切り替える、という応答ループが成立する。
    #
    # ingest 失敗 (ChatWork API 障害 / 認証エラー) のときは、誤った前提
    # (= 古い回答状態) で通知を飛ばすのを避けるため、後段の通知に進まず errors を
    # 積んで早期 return する。launchd は次回 9:15 / 9:30 で再試行する設計。
    # dry_run 時は ingest も dry-run で実行し、yaml への保存も行わない (副作用ゼロ原則)。
    try:
        from scripts.ingest_chatwork_responses import ingest as _ingest_responses
        try:
            from engine.stores.db import DEFAULT_DB_PATH
            ingest_db_path = db_path or DEFAULT_DB_PATH
            ingest_summary = _ingest_responses(client_id, dry_run=dry_run, db_path=ingest_db_path)
        except TypeError:
            # Test doubles and older callers may not accept db_path yet.
            ingest_summary = _ingest_responses(client_id, dry_run=dry_run)
        log.info(
            f"ingest: fetched={ingest_summary.get('fetched_messages', 0)} "
            f"parsed={ingest_summary.get('parsed_answers', 0)} "
            f"saved={ingest_summary.get('saved_responses', 0)} "
            f"errors={len(ingest_summary.get('errors', []))}"
        )
        if not ingest_summary.get("ok"):
            log.error(f"ingest 失敗: {ingest_summary.get('errors')} — 通知に進まない")
            return {
                "errors": [f"ingest_failed: {ingest_summary.get('fetch_error', '')}"]
                          + ingest_summary.get("errors", []),
                "posted_indications": 0, "posted_completions": 0,
            }
    except Exception as e:
        log.error(f"ingest 実行例外: {e}\n{traceback.format_exc()}")
        return {
            "errors": [f"ingest_exception: {e}"],
            "posted_indications": 0, "posted_completions": 0,
        }

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

    # Meta APIで何が見えたかをDecision Traceに保存する。
    # 通知本文に出た/出ないに関係なく、M02等の発火根拠を後から追えるようにする。
    if not dry_run and audit.get("data_available"):
        try:
            from engine.rules.decision_trace import build_meta_api_evidence_traces
            from engine.stores.db import transaction
            from engine.stores.decision_traces import record_trace
            traces = build_meta_api_evidence_traces(
                client_id=client_id,
                evaluation_date=today_str,
                audit_results=audit,
            )
            with transaction(db_path) as conn:
                for trace in traces:
                    record_trace(conn, **trace)
            log.info(f"decision_trace: meta_api_evidence recorded={len(traces)}")
        except Exception as e:
            log.error(f"Meta API decision trace 記録失敗: {e}")
            errors.append(f"meta_decision_trace: {e}")

        try:
            from engine.stores.db import transaction
            from engine.stores.outcomes import refresh_rule_outcome_rollups, update_due_outcome_measurements
            current_kpis = _extract_current_outcome_kpis(audit, platform="meta")
            with transaction(db_path) as conn:
                updated = update_due_outcome_measurements(
                    conn,
                    client_id=client_id,
                    current_kpis=current_kpis,
                    today=today_str,
                )
                rollups = refresh_rule_outcome_rollups(conn)
            log.info(f"outcome_tracker: due measurements updated={updated}")
            log.info(f"outcome_tracker: rule rollups updated={rollups.get('rules_updated', 0)}")
        except Exception as e:
            log.error(f"outcome tracker 実測更新失敗: {e}")
            errors.append(f"outcome_measure: {e}")

    # 5. 完了通知 (resolved_confirmed && completion_notified_at IS NULL)
    pending_completion = state.list_pending_completion_notification()
    if pending_completion:
        log.info(f"完了通知対象: {len(pending_completion)} 件")
        completion_items = [
            _to_completion_render_item(r, client_id=client_id, today_str=today_str)
            for r in pending_completion
        ]
        try:
            body = render("completion_notice.md.j2", {
                "client_display_name": client_display,
                "date": today_str,
                "completions": completion_items,
            })
            result = chat.post_message(body)
            # 5/8 修正: 本番投稿成功時のみ state を進める。
            # dry_run / skipped (idempotency hit) は通知済みマークしない (副作用ゼロ原則)
            if result.get("dry_run"):
                log.info(f"完了通知 [dry_run]: state を進めません key={result.get('idempotency_key', '')[:12]}")
            elif result.get("skipped"):
                log.info(f"完了通知スキップ (idempotency hit): key={result.get('idempotency_key', '')[:12]}")
            else:
                posted_completions = len(pending_completion)
                for r in pending_completion:
                    state.mark_completion_notified(r["indication_id"], today=today_str)
                try:
                    from engine.stores.db import transaction
                    from engine.stores.outcomes import record_completion_outcome
                    with transaction() as conn:
                        recorded = 0
                        for r in pending_completion:
                            if record_completion_outcome(conn, r):
                                recorded += 1
                    log.info(f"outcome_tracker: completion outcomes recorded={recorded}")
                except Exception as e:
                    log.error(f"outcome tracker 記録失敗: {e}")
                    errors.append(f"outcome_record: {e}")
        except (ChatWorkError, Exception) as e:
            log.error(f"完了通知投稿失敗: {e}")
            errors.append(f"completion_post: {e}")

    # 6. 統合 TODO 通知 (5/8 v2: 旧 daily_indication 個別投稿 + auto_proposal 個別投稿を
    #    1 通の「本日の広告成果改善TODO」に集約)
    notify_targets = filter_indications(upserted, state, today=today_str)
    if notify_targets:
        log.info(f"Layer A indication candidates: {len(notify_targets)} 件 (filter 後、統合 TODO へ流入)")

    todo_summary = {
        "posted_indications": 0,
        "auto_proposal_attempted": 0,
        "auto_proposal_sent": 0,
        "auto_proposal_skipped": 0,
        "auto_proposal_dry_run": 0,
        "auto_proposal_failed": 0,
        "internal_unmapped_rules": [],
        "total_count": 0,
        "errors": [],
    }
    try:
        from engine.daily_todo_builder import post_daily_todo
        todo_summary = post_daily_todo(
            client_id=client_id,
            layer_a_notify_records=notify_targets,
            audit_results=audit,
            state=state,
            today_str=today_str,
            dry_run=dry_run,
        )
        posted_indications = todo_summary["posted_indications"]
        log.info(
            f"daily_todo: total={todo_summary.get('total_count', 0)} "
            f"layer_a_sent={todo_summary['posted_indications']} "
            f"auto_proposal_sent={todo_summary.get('auto_proposal_sent', 0)} "
            f"unmapped={len(todo_summary.get('internal_unmapped_rules', []))}"
        )
        errors.extend(todo_summary.get("errors", []))
    except Exception as e:
        log.error(f"daily_todo 投稿失敗: {e}")
        errors.append(f"daily_todo: {e}")

    # 7. state 保存 — 5/8 修正: dry_run 時は永続化しない (副作用ゼロ原則)
    if not dry_run:
        state.save()
    else:
        log.info("[dry_run] state.save() スキップ — indication_state は永続化されません")

    # 後方互換用 summary フィールド (旧 caller が auto_proposal_summary を期待)
    auto_proposal_summary = {
        "loaded_rules_count": 0, "eligible_count": 0,
        "attempted_count": todo_summary.get("auto_proposal_attempted", 0),
        "sent_count":      todo_summary.get("auto_proposal_sent", 0),
        "skipped_count":   todo_summary.get("auto_proposal_skipped", 0),
        "dry_run_count":   todo_summary.get("auto_proposal_dry_run", 0),
        "failed_count":    todo_summary.get("auto_proposal_failed", 0),
        "posted_count":    todo_summary.get("auto_proposal_sent", 0),
    }

    # 9. AdTruth 日次チェック (ADR-006/009/014)
    #    fraud_score を campaign 粒度で算出 → gray/black 検出時のみ ChatWork に判断要請
    #    0 件なら ChatWork は静寂、ログだけ残す (ノイズ防止)
    adtruth_summary = {"samples_count": 0, "gray_count": 0, "black_count": 0, "posted_count": 0}
    try:
        from engine.adtruth_runner import run_adtruth_check
        adtruth_summary = run_adtruth_check(
            client_id=client_id,
            dry_run=dry_run,
            today=today_str,
        )
    except Exception as e:
        log.error(f"adtruth_runner 失敗 (既存フローには影響なし): {e}")
        errors.append(f"adtruth: {e}")

    summary = {
        "posted_indications": posted_indications,
        "posted_completions": posted_completions,
        "auto_proposal_loaded":    auto_proposal_summary.get("loaded_rules_count", 0),
        "auto_proposal_eligible":  auto_proposal_summary.get("eligible_count", 0),
        # 5/8 改修: 結果カウントを sent / skipped / dry_run / failed で分離
        "auto_proposal_attempted": auto_proposal_summary.get("attempted_count", 0),
        "auto_proposal_sent":      auto_proposal_summary.get("sent_count", 0),
        "auto_proposal_skipped":   auto_proposal_summary.get("skipped_count", 0),
        "auto_proposal_dry_run":   auto_proposal_summary.get("dry_run_count", 0),
        "auto_proposal_failed":    auto_proposal_summary.get("failed_count", 0),
        # 後方互換: posted_count は sent_count と同値
        "auto_proposal_posted":    auto_proposal_summary.get("posted_count", 0),
        "adtruth_samples": adtruth_summary.get("samples_count", 0),
        "adtruth_gray":   adtruth_summary.get("gray_count", 0),
        "adtruth_black":  adtruth_summary.get("black_count", 0),
        "adtruth_posted": adtruth_summary.get("posted_count", 0),
        "errors": errors,
    }
    if not dry_run:
        try:
            summary["operational_cases_synced"] = _persist_operational_cases(
                client_id=client_id,
                state=state,
                today_str=today_str,
                audit_results=audit,
                db_path=db_path,
            )
        except Exception as e:
            log.error(f"operational case sync failed: {e}")
            errors.append(f"operational_case_sync: {e}")
            summary["operational_cases_synced"] = 0
    log.info(f"日次チェック完了: {summary}")
    return summary


def _finish_operational_job(db_path: Optional[Path], job_run_id: str, status: str, errors: list, metrics: dict) -> None:
    try:
        from engine.stores.db import transaction
        from engine.stores.jobs import finish_job
        with transaction(db_path) as conn:
            finish_job(conn, job_run_id, status, errors=errors, metrics=metrics)
    except Exception as e:
        log.error(f"operational DB job finish failed: {e}")


def _persist_operational_cases(
    client_id: str,
    state: IndicationState,
    today_str: str,
    audit_results: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Persist current indication state into operational_cases.

    The JSON state remains the runtime source of truth for now. This write makes
    the operations UI and PDCA loop observe the same state immediately after the
    production job runs, without waiting for a separate sync command.
    """
    from engine.stores.cases import upsert_case_from_indication
    from engine.stores.db import transaction

    records = list(state.all_indications().values())
    with transaction(db_path) as conn:
        for record in records:
            case_id = upsert_case_from_indication(conn, record)
            if record.get("notified_date") == today_str:
                _insert_operational_notification(conn, case_id, record, "daily_todo")
                _insert_outcome_baselines(conn, case_id, record, audit_results or {}, today_str)
            if record.get("completion_notified_date") == today_str:
                _insert_operational_notification(conn, case_id, record, "completion_notice")
        return len(records)


def _insert_operational_notification(conn, case_id: str, record: dict, notification_type: str) -> None:
    import hashlib
    from engine.stores.db import json_dumps

    event_date = record.get("completion_notified_date") if notification_type == "completion_notice" else record.get("notified_date")
    event_at = record.get("completion_notified_at") if notification_type == "completion_notice" else record.get("notified_at")
    raw = f"{case_id}|{notification_type}|{event_date or event_at or ''}"
    notification_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    payload = {
        "rule_id": record.get("rule_id"),
        "notification_type": notification_type,
        "source": "daily_chatwork_check",
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO notification_messages (
          notification_id, client_id, case_id, channel, status, sent_at, payload_json
        ) VALUES (?, ?, ?, 'chatwork', 'sent', ?, ?)
        """,
        (
            notification_id,
            record.get("client_id", ""),
            case_id,
            event_at,
            json_dumps(payload),
        ),
    )


def _insert_outcome_baselines(conn, case_id: str, record: dict, audit_results: dict, today_str: str) -> int:
    """Record CPA/CV/ROAS at notification time for later before/after checks."""
    from engine.stores.outcomes import record_outcome

    kpis = _extract_current_outcome_kpis(audit_results, platform="meta")
    if not kpis:
        return 0

    recorded = 0
    for metric, value in kpis.items():
        if value is None:
            continue
        record_outcome(
            conn,
            case_id=case_id,
            client_id=record.get("client_id", ""),
            metric=metric,
            baseline_value=float(value),
            measured_value=None,
            baseline_start=today_str,
            baseline_end=today_str,
            measurement_start=today_str,
            confidence="medium",
            notes="daily_todo notification baseline",
            payload={
                "rule_id": record.get("rule_id"),
                "source": "daily_todo_baseline",
                "platform": "meta",
            },
        )
        recorded += 1
    return recorded


def _extract_current_outcome_kpis(audit_results: dict, platform: str = "meta") -> dict:
    ads = (audit_results or {}).get("ads_audit") or {}
    summary = (ads.get("platform_summary") or {}).get(platform) or {}
    if not summary:
        return {}
    return {
        "cpa": summary.get("avg_cpa"),
        "cv_count": summary.get("conversions"),
        "roas": summary.get("avg_roas"),
    }


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
            # 5/7 P3 P2 fix: ChatWork を主監視にする運用で、ingest / audit 失敗時も
            # 顧客通知だけでなく自己監視通知も送る。run_daily_check 内の早期 return
            # (ingest_failed / audit_fetch / ingest_exception) では顧客通知が一切出ず、
            # かつ exit 3 では ChatWork に何も流れないため、launchd ログを見るまで
            # 障害に気づけない。non-dry-run 時は ChatWork に critical を投げる。
            #
            # post_self_alert の idempotency_key は (date, hash(message)) なので、
            # 9:00 / 9:15 / 9:30 の 3 連射でも同一エラーは 1 日 1 回に de-dupe される。
            if not args.dry_run:
                err_lines = "\n".join(f"・{e}" for e in result["errors"])
                post_self_alert(
                    f"日次 ChatWork チェックで失敗が発生しました (client={args.client}):\n"
                    f"{err_lines}\n\n"
                    "顧客通知は送信されていません。launchd の次回 retry または手動再実行を確認してください。",
                    dry_run=False,
                )
            return 3
        return 0
    except Exception as e:
        log.error(f"致命的失敗: {e}\n{traceback.format_exc()}")
        if not args.dry_run:
            post_self_alert(f"{e}\n\n{traceback.format_exc()[:1500]}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
