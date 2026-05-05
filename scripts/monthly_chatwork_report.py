"""月次 ChatWork レポート投稿ジョブ (ADR-005 / Day 3 E2)

実行: venv/bin/python3 scripts/monthly_chatwork_report.py
       [--client pilotton] [--period 2026-04] [--dry-run] [--prefix "[テスト] "]

処理フロー:
  1. period (省略時は前月) を決定
  2. IndicationState + archive から月次集計
  3. 該当 period の v3 PDF を reports/{period_end}/{client}_report_v3.pdf から探索
  4. monthly_report.md.j2 をレンダリングして ChatWork へ投稿
  5. PDF をファイル添付投稿（5MB Free プラン制約をログ警告）
  6. 投稿完了後、resolved_confirmed → archived に移行（state.archive_resolved）

注意:
- Free プラン月 5MB 制約のため PDF サイズが 5MB 超なら警告を出す
- 投稿は 1 件ずつ idempotency 効くため、再実行で重複しない
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
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
from engine.monthly_aggregator import aggregate_month
from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError
from templates.chatwork import render

log = logging.getLogger("monthly_chatwork")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

CHATWORK_FREE_MONTHLY_QUOTA_MB = 5.0
ATTACHMENT_WARNING_THRESHOLD_MB = 3.0  # ChatWork Free 5MB の余裕枠を残すための警告閾値


def _previous_month(today: Optional[str] = None) -> str:
    """今日 (YYYY-MM-DD) → 前月 'YYYY-MM'"""
    base = datetime.fromisoformat(today).date() if today else datetime.now().date()
    first_of_this_month = base.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    return last_of_prev_month.strftime("%Y-%m")


def _find_latest_pdf(client_id: str, period: str) -> Optional[Path]:
    """指定期間の最終日に近い v3 PDF を探す。なければ reports/ 全体から最新を返す"""
    reports_dir = ROOT / "reports"
    pattern = f"{client_id}_report_v3.pdf"

    # 1. period 内 (YYYY-MM-*) のディレクトリで最新
    candidates = sorted(
        (p for p in reports_dir.glob(f"{period}-*/{pattern}")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    # 2. fallback: 全期間の最新
    all_candidates = sorted(
        reports_dir.rglob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return all_candidates[0] if all_candidates else None


def run_monthly_report(
    client_id: str,
    period: Optional[str] = None,
    dry_run: bool = False,
    test_prefix: str = "",
    today: Optional[str] = None,
    archive_after: bool = True,
) -> dict:
    """月次レポート投稿のメイン処理

    Returns:
        {"posted": bool, "attached_pdf": str | None, "archived": int, "errors": [...]}
    """
    today_str = today or datetime.now().strftime("%Y-%m-%d")
    target_period = period or _previous_month(today_str)
    log.info(f"月次レポート投稿: client={client_id} period={target_period} dry_run={dry_run}")

    state = IndicationState(client_id=client_id)
    pdf_path = _find_latest_pdf(client_id, target_period)
    pdf_filename = pdf_path.name if pdf_path else None
    pdf_size_mb = (pdf_path.stat().st_size / (1024 * 1024)) if pdf_path else 0.0

    if pdf_path:
        log.info(f"添付 PDF: {pdf_path.relative_to(ROOT)} ({pdf_size_mb:.2f} MB)")
        if pdf_size_mb > CHATWORK_FREE_MONTHLY_QUOTA_MB:
            log.warning(
                f"🚨 PDF サイズ {pdf_size_mb:.2f} MB が Free プラン月次容量 {CHATWORK_FREE_MONTHLY_QUOTA_MB} MB を超過。"
                " アップロード自体が失敗する可能性が高い。Business プラン以上への移行が必要。"
            )
        elif pdf_size_mb > ATTACHMENT_WARNING_THRESHOLD_MB:
            log.warning(
                f"⚠️ PDF サイズ {pdf_size_mb:.2f} MB が警告閾値 {ATTACHMENT_WARNING_THRESHOLD_MB} MB を超過。"
                f" Free プラン残枠 {CHATWORK_FREE_MONTHLY_QUOTA_MB - pdf_size_mb:.2f} MB のみ。"
                " 翌月以降に追加添付の余地が小さい。PDF 軽量化または Business プラン検討推奨。"
            )
    else:
        log.warning(f"v3 PDF 未検出 (period={target_period})、添付なしで投稿します")

    # 1. 集計
    client_display = test_prefix + ("株式会社パイロットン" if client_id == "pilotton" else client_id)
    client_display = client_display.strip()
    context = aggregate_month(
        state,
        period=target_period,
        client_display_name=client_display,
        today=today_str,
        attached_pdf=pdf_filename,
        formula_note=(
            "※確実値は pixel_health 連動係数を適用し、現実値は重複排除 (duplicate_factor) 済"
        ),
    )

    # 2. テキスト投稿
    chat = ChatWorkClient(dry_run=dry_run)
    posted = False
    archived_count = 0
    errors: list[str] = []
    try:
        body = render("monthly_report.md.j2", context)
        result = chat.post_message(body)
        if result.get("skipped"):
            log.info(f"月次レポート投稿スキップ (idempotency hit): key={result.get('idempotency_key', '')[:12]}")
        else:
            posted = True
            log.info(f"月次レポート投稿成功: message_id={result.get('message_id')}")
    except (ChatWorkError, Exception) as e:
        log.error(f"月次レポート本文投稿失敗: {e}")
        errors.append(f"text_post: {e}")

    # 3. PDF 添付（あれば）
    if pdf_path:
        try:
            res = chat.upload_file(
                str(pdf_path),
                message=(
                    f"[月次レポート] {target_period} {client_display}\n"
                    f"添付: {pdf_filename} ({pdf_size_mb:.2f} MB)"
                ),
            )
            if res.get("skipped"):
                log.info(f"PDF 添付スキップ (idempotency hit): key={res.get('idempotency_key', '')[:12]}")
            else:
                log.info(f"PDF 添付成功: file_id={res.get('file_id')}")
        except (ChatWorkError, Exception) as e:
            log.error(f"PDF 添付失敗: {e}")
            errors.append(f"pdf_upload: {e}")

    # 4. archive (resolved_confirmed → archived)
    if archive_after and not dry_run:
        try:
            archived_count = state.archive_resolved(archive_month=target_period)
            log.info(f"アーカイブ: {archived_count} 件 → {state.archive_dir}/{target_period}.json")
        except Exception as e:
            log.error(f"アーカイブ失敗: {e}")
            errors.append(f"archive: {e}")

    state.save()

    summary = {
        "posted": posted,
        "attached_pdf": pdf_filename,
        "archived": archived_count,
        "errors": errors,
    }
    log.info(f"月次レポート完了: {summary}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="月次 ChatWork レポート投稿ジョブ")
    parser.add_argument("--client", default="pilotton", help="クライアントID")
    parser.add_argument("--period", default=None, help="集計対象月 YYYY-MM (省略時は前月)")
    parser.add_argument("--dry-run", action="store_true", help="ChatWork に投稿しない")
    parser.add_argument("--prefix", default="", help='client_display_name プレフィクス')
    parser.add_argument("--today", default=None, help="シミュレーション日 YYYY-MM-DD")
    parser.add_argument("--no-archive", action="store_true", help="resolved_confirmed のアーカイブをスキップ")
    args = parser.parse_args()

    if not os.environ.get("CHATWORK_API_TOKEN"):
        log.error("CHATWORK_API_TOKEN 未設定。")
        return 1
    if not os.environ.get("CHATWORK_ROOM_ID_PILOTTON"):
        log.error("CHATWORK_ROOM_ID_PILOTTON 未設定。")
        return 1

    result = run_monthly_report(
        client_id=args.client,
        period=args.period,
        dry_run=args.dry_run,
        test_prefix=args.prefix,
        today=args.today,
        archive_after=not args.no_archive,
    )
    return 0 if not result["errors"] else 3


if __name__ == "__main__":
    sys.exit(main())
