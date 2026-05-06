"""ChatWork 顧客回答の取り込みスクリプト (5/8 v3 ingestion)

責務: ChatWork API でルーム内メッセージを取得し、A/B/C 形式の回答をパースして
      outputs/chatwork_responses/{client_id}.yaml に永続化する。
      launchd の朝の本投稿の "前段" で実行することで、当日の通知選定に反映可能。

呼び出し:
    venv/bin/python3 scripts/ingest_chatwork_responses.py --client pilotton
    venv/bin/python3 scripts/ingest_chatwork_responses.py --client pilotton --dry-run

オプション:
    --client     クライアント ID (default: pilotton)
    --dry-run    パース結果のみ表示、yaml には書き込まない
    --since-id   指定 message_id 以降を取り込み (default: 全件、最大 100 件)

ingestion フロー:
    1. clients.yaml から chatwork_rooms.main を解決
    2. ChatWorkClient.fetch_messages(room_id, force=1) で最新メッセージ取得
    3. engine.daily_todo_builder.load_messaging() で rule_messaging を取得
    4. engine.chatwork_response_parser.parse_messages_bulk() でパース
    5. engine.chatwork_response_store.save_response() で永続化

副作用ゼロ原則 (本ファイル):
    - --dry-run なら yaml に書き込まない
    - --dry-run なら ChatWork に ACK 投稿しない
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# 5/8 P3: テストから monkeypatch できるよう module-level import に統一
from notifiers.chatwork_notifier import ChatWorkClient
from engine.daily_todo_builder import load_messaging
from engine.chatwork_response_parser import parse_messages_bulk
from engine.chatwork_response_store import save_response
from engine.chatwork_reply_context_store import load_latest_context
from engine.chatwork_response_ack_store import (
    load_acked_message_ids,
    mark_acked_message_ids,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ingest")


def ingest(client_id: str, dry_run: bool = False, since_id: str = "") -> dict:
    """1 client 分の ChatWork 回答取り込み

    Returns:
        {
            "client_id": str,
            "fetched_messages": int,
            "parsed_answers":   int,
            "saved_responses":  int,
            "skipped_by_since_id": int,
            "errors": [...],
            "ack_sent": int,
            "ack_skipped": int,
            "ack_errors": [...],
            "answers_summary": [...]    # rule_id + status のサマリ
        }
    """
    summary = {
        "ok": True,                    # 5/8 P1-A: API 障害時に False に変更
        "client_id": client_id,
        "fetched_messages": 0,
        "parsed_answers":   0,
        "saved_responses":  0,
        "skipped_by_since_id": 0,
        "errors": [],
        "ack_sent": 0,
        "ack_skipped": 0,
        "ack_errors": [],
        "answers_summary": [],
    }

    # 1. clients.yaml から room_id 解決
    clients_yaml = ROOT / "config" / "clients.yaml"
    cfg = yaml.safe_load(clients_yaml.read_text(encoding="utf-8")) or {}
    client_cfg = (cfg.get("clients") or {}).get(client_id) or {}
    room_id = (client_cfg.get("chatwork_rooms") or {}).get("main")
    if not room_id:
        msg = f"chatwork_rooms.main 未設定: {client_id}"
        log.error(msg)
        summary["errors"].append(msg)
        return summary

    # 5/7 P3: Bot 自身の自動通知本文を回答として誤取り込みしないため、Bot account_id を解決。
    # 優先順位: clients.yaml の bot_account_ids > .env の CHATWORK_BOT_ACCOUNT_ID > なし。
    # account_id が無くても parser 側の本文 marker (BOT_BODY_MARKERS) で 2 段防御している。
    bot_account_ids: set = set()
    cfg_ids = client_cfg.get("chatwork_bot_account_ids") or []
    for v in cfg_ids:
        try:
            bot_account_ids.add(int(v))
        except (TypeError, ValueError):
            pass
    env_id = os.environ.get("CHATWORK_BOT_ACCOUNT_ID")
    if env_id:
        try:
            bot_account_ids.add(int(env_id))
        except ValueError:
            log.warning(f"CHATWORK_BOT_ACCOUNT_ID は数値である必要があります: {env_id!r}")

    # 2. メッセージ取得 — 5/8 P1-A: ChatWork API エラーは「成功 0 件」扱いせず、
    # ok=False / error 詳細を summary に記録して呼出元で exit code 非ゼロにできるようにする
    client = ChatWorkClient(room_id=str(room_id), dry_run=False)
    try:
        messages = client.fetch_messages(room_id=str(room_id))
        summary["ok"] = True
    except Exception as e:
        msg = f"fetch_messages failed: {e.__class__.__name__}: {e}"
        log.error(msg)
        summary["errors"].append(msg)
        summary["ok"] = False
        summary["fetch_error"] = str(e)
        # 朝ジョブ前段で取り込みが落ちているのに通知が走るのを防ぐため、
        # API 障害は呼出元で検知できるよう ok=False で早期 return
        return summary
    summary["fetched_messages"] = len(messages)

    # 5/8 P3: send_time / message_id 昇順 (古い順) にソートしてから parse / save。
    # これにより同 rule_id への複数回答が時系列順に処理され、最後に保存されるのが
    # 最も新しい回答になる。store 側にも単調性チェックがあるが二重防御。
    def _sort_key(m):
        try:
            return (int(m.get("send_time", 0) or 0), int(m.get("message_id", 0) or 0))
        except (ValueError, TypeError):
            return (0, 0)
    messages.sort(key=_sort_key)

    # since_id フィルタ (numeric 比較)
    if since_id and messages:
        try:
            threshold = int(since_id)
            before = len(messages)
            messages = [m for m in messages if int(m.get("message_id", 0)) > threshold]
            summary["skipped_by_since_id"] = before - len(messages)
        except (ValueError, TypeError):
            log.warning(f"--since-id 不正形式: {since_id}、無視して全件処理")

    # 3-4. パース (Bot 自動通知を本文 marker / account_id で除外してから regex 抽出)
    # 5/7 P4: 直近通知文脈があれば、`C、C` のようなルールIDなし返信を
    # 表示順に割り当てる。
    rule_messaging = load_messaging()
    reply_context = load_latest_context(client_id)
    parsed = parse_messages_bulk(
        messages, rule_messaging,
        bot_account_ids=bot_account_ids or None,
        reply_context=reply_context,
    )
    summary["parsed_answers"] = len(parsed)

    # 5. 永続化 (dry-run なら skip)
    saved_records = []
    for ans in parsed:
        record = ans.to_dict()
        summary["answers_summary"].append({
            "rule_id":       ans.rule_id,
            "answer_code":   ans.answer_code,
            "answer_label":  ans.answer_label,
            "status":        ans.status,
            "message_id":    ans.chatwork_message_id,
        })
        if not dry_run:
            try:
                save_response(client_id, record)
                summary["saved_responses"] += 1
                saved_records.append(record)
            except Exception as e:
                err = f"save_response failed for {ans.rule_id}: {e}"
                log.error(err)
                summary["errors"].append(err)

    # 6. 受領 ACK 投稿 (非 dry-run のみ)。保存済み回答だけを対象にし、
    #    message_id 単位で ACK 済み管理することで二重返信を防ぐ。
    if not dry_run and saved_records:
        ack_result = _post_ack_for_new_responses(
            client_id=client_id,
            client=client,
            responses=saved_records,
            rule_messaging=rule_messaging,
        )
        summary["ack_sent"] = ack_result["sent"]
        summary["ack_skipped"] = ack_result["skipped"]
        summary["ack_errors"].extend(ack_result["errors"])

    log.info(
        f"[{client_id}] ingestion 完了: fetched={summary['fetched_messages']} "
        f"parsed={summary['parsed_answers']} saved={summary['saved_responses']} "
        f"ack_sent={summary['ack_sent']} ack_skipped={summary['ack_skipped']} "
        f"errors={len(summary['errors'])} ack_errors={len(summary['ack_errors'])}"
    )
    return summary


def _post_ack_for_new_responses(
    client_id: str, client: ChatWorkClient, responses: list[dict], rule_messaging: dict,
) -> dict:
    """未 ACK の顧客メッセージに対して受領返信を 1 通投稿する。"""
    result = {"sent": 0, "skipped": 0, "errors": []}
    acked_ids = load_acked_message_ids(client_id)

    new_responses = [
        r for r in responses
        if r.get("chatwork_message_id") and str(r.get("chatwork_message_id")) not in acked_ids
    ]
    skipped_ids = {
        str(r.get("chatwork_message_id")) for r in responses
        if r.get("chatwork_message_id") and str(r.get("chatwork_message_id")) in acked_ids
    }
    result["skipped"] = len(skipped_ids)
    if not new_responses:
        return result

    source_ids = sorted(
        {str(r["chatwork_message_id"]) for r in new_responses},
        key=_message_sort_key,
    )
    body = _render_ack_body(new_responses, rule_messaging)
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    idempotency_key = f"chatwork_response_ack:{client_id}:{','.join(source_ids)}:{body_hash}"

    try:
        post_result = client.post_message(body, idempotency_key=idempotency_key)
        mark_acked_message_ids(client_id, source_ids)
        result["sent"] = 0 if post_result.get("skipped") else 1
        if post_result.get("skipped"):
            result["skipped"] += len(source_ids)
    except Exception as e:
        msg = f"post_ack failed: {e.__class__.__name__}: {e}"
        log.warning(msg)
        result["errors"].append(msg)
    return result


def _render_ack_body(responses: list[dict], rule_messaging: dict) -> str:
    """顧客向け受領返信本文を生成する。"""
    rules = rule_messaging.get("rules") or {}
    lines = [
        "[info][title]ご回答ありがとうございます[/title]",
        "以下の内容で受け取りました。",
        "",
    ]
    for rec in responses:
        rid = rec.get("rule_id", "")
        title = (rules.get(rid) or {}).get("customer_title") or rid
        label = rec.get("answer_label") or rec.get("answer_code") or "回答あり"
        lines.append(f"・{title} → {label}")
    lines.extend([
        "",
        "次回のTODO通知に反映します。確認が必要な項目は、Zynect側で手順を整理してご案内します。",
        "[/info]",
    ])
    return "\n".join(lines)


def _message_sort_key(v: str) -> tuple[int, str]:
    try:
        return (int(v), v)
    except (TypeError, ValueError):
        return (0, str(v))


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatWork 顧客回答取り込み")
    parser.add_argument("--client", default="pilotton")
    parser.add_argument("--dry-run", action="store_true",
                        help="パース結果のみ表示、yaml に書き込まない")
    parser.add_argument("--since-id", default="",
                        help="指定 message_id より新しいメッセージのみ取込")
    args = parser.parse_args()

    summary = ingest(args.client, dry_run=args.dry_run, since_id=args.since_id)

    print("=== ingestion summary ===")
    import json
    summary_no_answers = {k: v for k, v in summary.items() if k != "answers_summary"}
    print(json.dumps(summary_no_answers, ensure_ascii=False, indent=2))

    if summary["answers_summary"]:
        print("\n=== parsed answers ===")
        for a in summary["answers_summary"]:
            print(f"  {a['rule_id']:18} code={a['answer_code']:2} status={a['status']:18} label={a['answer_label']}")

    # 5/8 P1-A: API 障害 (ok=False) または save 失敗時は非ゼロ exit
    # 朝ジョブの前段で気づけるよう、運用ログに「ingest 失敗」が明示される
    if not summary.get("ok") or summary["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
