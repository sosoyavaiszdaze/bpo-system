"""ChatWork API 疎通テスト (ADR-005 / Day 2 後半)

実行: venv/bin/python3 scripts/chatwork_smoke_test.py

3 ステップを順次実行:
  a. daily_indication 投稿（pilotton 真値サンプル）
  b. 同スクリプト 2 回目実行で idempotency スキップが起きること（手動再実行で確認）
  c. completion_notice 投稿
  d. ファイル添付投稿（最新の v3 PDF）

すべて [テスト] プレフィクスを client_display_name に付与し、本番ルーム
(rid 435851481, パイロットン ad通知パイプライン) に投稿。

セキュリティ:
- API トークンは ChatWorkClient が env から自動取得（コードに値を持たない）
- ログにトークンは絶対に出力されない（chatwork_notifier 側で制御済み）

idempotency ストア:
- 場所: state/chatwork_sent.json
- クリア: rm state/chatwork_sent.json （次回再実行時に全件再送される）
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env 読込（venv に dotenv がある）
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except ImportError:
    print("[WARN] python-dotenv 未インストール: 環境変数を直接参照します", file=sys.stderr)

from notifiers.chatwork_notifier import ChatWorkClient, ChatWorkError  # noqa: E402
from templates.chatwork import render  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("smoke")

# pilotton 真値（Day 5.2 / 直近30日）
PILOTTON_REAL_CV = 156
PILOTTON_REAL_CPA = "¥9,251"
PILOTTON_REAL_SPEND = "¥1,443,150"
TEST_DISPLAY_NAME = "[テスト] 株式会社パイロットン"

CHATWORK_FREE_MONTHLY_QUOTA_MB = 5.0


def _human_size(bytes_n: int) -> str:
    return f"{bytes_n / (1024 * 1024):.2f} MB ({bytes_n / 1024:.1f} KB / {bytes_n} B)"


def find_latest_pdf() -> Path:
    """reports/ 配下の最新 PDF を返す（pptx より優先）"""
    pdfs = sorted(
        (ROOT / "reports").rglob("*.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not pdfs:
        # PDF が無ければ pptx
        pptxs = sorted(
            (ROOT / "reports").rglob("*.pptx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not pptxs:
            raise FileNotFoundError("reports/ 配下に PDF/pptx がありません")
        return pptxs[0]
    return pdfs[0]


def _summarize_result(label: str, result: dict) -> str:
    if result.get("skipped"):
        return f"[{label}] SKIPPED (idempotency hit) key={result.get('idempotency_key', '')[:12]}…"
    if result.get("dry_run"):
        return f"[{label}] DRY-RUN"
    if "message_id" in result:
        return f"[{label}] POSTED message_id={result['message_id']}"
    if "file_id" in result:
        return f"[{label}] POSTED file_id={result['file_id']}"
    return f"[{label}] result={result}"


def step_a_daily_indication(client: ChatWorkClient) -> dict:
    log.info("=" * 60)
    log.info("Step a: daily_indication.md.j2 投稿")
    body = render("daily_indication.md.j2", {
        "client_display_name": TEST_DISPLAY_NAME,
        "date": "2026-05-04",
        "greeting": (
            "本投稿は ChatWork API 疎通テストです（ADR-005 Day 2 後半 A-T）。"
            "実運用ではありません。確認後に削除いただいて構いません。"
        ),
        "indications": [{
            "title": "CAPI（Conversion API）未実装",
            "rule_id": "DQ-CAPI-MISSING",
            "severity_label": "重要度高",
            "fact": (
                f"直近30日の Pixel 計測 CV は {PILOTTON_REAL_CV} 件、"
                f"CPA {PILOTTON_REAL_CPA}（消化額 {PILOTTON_REAL_SPEND}）。"
                " iOS14.5+ の SKAN 影響で計測欠損が推定 30-40% 発生中。"
            ),
            "impact": (
                "CV 計測欠損により学習シグナルが減衰、CPA 改善余地が顕在化していない。"
                " 結果として広告配信最適化が本来の到達点に届いていない。"
            ),
            "scenario_label": "現実シナリオ",
            "expected_effect": "月次 CPA -8% (¥9,251 → ¥8,510 程度、CV +12〜15件)",
            "payload": {},  # ECフォース / 自社開発 両方の手順を本文展開 (pilotton 想定)
        }],
        "footer_note": "本指摘は smoke test 用のサンプルです（[テスト] プレフィクス）。",
    })
    log.info(f"投稿本文長: {len(body)} 文字")
    result = client.post_message(body)
    log.info(_summarize_result("a", result))
    return result


def step_c_completion_notice(client: ChatWorkClient) -> dict:
    log.info("=" * 60)
    log.info("Step c: completion_notice.md.j2 投稿")
    body = render("completion_notice.md.j2", {
        "client_display_name": TEST_DISPLAY_NAME,
        "date": "2026-05-20",
        "completions": [{
            "title": "CAPI 実装完了による計測精度回復",
            "rule_id": "M02",
            "first_reported_at": "2026-05-04",
            "resolved_at": "2026-05-20",
            "before_state": (
                f"Pixel のみで計測、直近30日 CV {PILOTTON_REAL_CV} 件 / CPA {PILOTTON_REAL_CPA}。"
                " EMQ スコア 4.2 で SKAN 計測欠損が顕在化していた。"
            ),
            "after_state": (
                "CAPI Gateway 有効化、purchase / lead 両イベントを GTM ssgtm 経由で転送。"
                " EMQ 7.8 達成、Pixel + CAPI dedup 動作確認済み。"
            ),
            "consecutive_clean_days": 3,
            "achieved_effect": {
                "minimum": "¥-58,000 / 月（保守: pixel_health 連動係数適用）",
                "realistic": "¥-115,500 / 月（現実）",
                "optimistic": "¥-173,000 / 月（上限: 独立施策仮定）",
            },
            "note": (
                "本投稿は smoke test です。CV 重複排除は conversion_mapping.yaml により"
                "自動化済み（同事象の二重計上を防止）。"
            ),
        }],
    })
    log.info(f"投稿本文長: {len(body)} 文字")
    result = client.post_message(body)
    log.info(_summarize_result("c", result))
    return result


def step_d_file_attach(client: ChatWorkClient) -> dict:
    log.info("=" * 60)
    log.info("Step d: ファイル添付投稿")
    file_path = find_latest_pdf()
    size = file_path.stat().st_size
    size_mb = size / (1024 * 1024)
    log.info(f"添付ファイル: {file_path.relative_to(ROOT)}")
    log.info(f"ファイルサイズ: {_human_size(size)}")

    quota_remaining = CHATWORK_FREE_MONTHLY_QUOTA_MB - size_mb
    quota_status = "OK" if quota_remaining >= 0 else "OVER"
    log.info(
        f"Free プラン月次容量試算: {CHATWORK_FREE_MONTHLY_QUOTA_MB:.2f} MB - "
        f"{size_mb:.2f} MB = 残 {quota_remaining:.2f} MB [{quota_status}]"
    )
    log.info("※実際の残容量は ChatWork 設定 → ファイル管理 で要確認（複数ルーム合算）")

    result = client.upload_file(
        str(file_path),
        message=(
            "[テスト] BPO System ChatWork API 疎通確認 — ファイル添付テスト\n"
            f"添付: {file_path.name} ({size_mb:.2f} MB)\n"
            "本投稿は ADR-005 Day 2 後半の smoke test です。"
        ),
    )
    log.info(_summarize_result("d", result))
    return result


def main() -> int:
    log.info("ChatWork API 疎通テスト開始")
    log.info("投稿先: rid 435851481 (パイロットン ad通知パイプライン)")
    log.info("注意: 本投稿は全て [テスト] プレフィクス付き、後で削除可能")

    if not os.environ.get("CHATWORK_API_TOKEN"):
        log.error("CHATWORK_API_TOKEN 未設定。.env を確認してください。")
        return 1
    room_id: Optional[str] = os.environ.get("CHATWORK_ROOM_ID_PILOTTON")
    if not room_id:
        log.error("CHATWORK_ROOM_ID_PILOTTON 未設定。.env を確認してください。")
        return 1
    log.info(f"投稿ルーム ID: {room_id}")
    log.info(f"トークン設定: ✅set ({len(os.environ['CHATWORK_API_TOKEN'])} 文字、値非表示)")

    client = ChatWorkClient(room_id=room_id)
    # api_token は引数で渡さず、ChatWorkClient が env から取得（コードに値を持たない）

    results: dict = {}
    try:
        results["a"] = step_a_daily_indication(client)
        results["c"] = step_c_completion_notice(client)
        results["d"] = step_d_file_attach(client)
    except ChatWorkError as e:
        log.error(f"ChatWork API エラー: {e}")
        log.error(
            "デバッグ: idempotency ストアをクリアして再実行する場合は "
            "`rm state/chatwork_sent.json`"
        )
        return 2

    log.info("=" * 60)
    log.info("疎通テスト完了 — サマリー")
    skipped_count = sum(1 for r in results.values() if r.get("skipped"))
    posted_count = sum(1 for r in results.values() if not r.get("skipped"))
    log.info(f"  投稿: {posted_count} 件 / スキップ: {skipped_count} 件")
    for label, res in results.items():
        log.info(f"  {_summarize_result(label, res)}")

    if skipped_count == 0:
        log.info("→ 初回実行（全件投稿）。同じスクリプトを再実行すると 3 件すべて SKIPPED になります（Step b 検証）")
    elif skipped_count == len(results):
        log.info("→ 2 回目以降の実行: idempotency が正しく動作 ✅")
    else:
        log.info("→ 一部のみスキップ: idempotency ストアの不整合の可能性、要確認")

    log.info("idempotency ストアをクリア: rm state/chatwork_sent.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
