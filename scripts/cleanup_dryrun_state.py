"""dry-run 由来の状態汚染を除去する補正スクリプト (5/8)

経緯:
    chatwork_notifier.post_message() / upload_file() が dry_run=True 時にも
    state/chatwork_sent.json へ "dry_run: True" のエントリを記録していたため、
    その後の本番実行で同 idempotency_key が "送信済み" 判定され、ChatWork 投稿が
    完全に塞がれる事故が発生 (5/7 夜)。

    同時に auto_proposal_engine の history、indication_state の notified_at も
    dry-run 由来データで汚染されている可能性があり、本スクリプトでまとめて補正する。

補正対象 (バックアップ取得後に実施):
    1. state/chatwork_sent.json:
       - dry_run=True のエントリを削除
       - 本番 (message_id を持つ) エントリは保持
    2. outputs/auto_proposal_history/{client}.yaml:
       - result.skipped=True / result.dry_run=True のエントリを削除
       - 本番送信 (message_id 等) のエントリは保持
    3. outputs/chatwork_state/{client}_indications.json:
       - notified_at の補正は同定が難しいため、デフォルトでは触らない
       - --reset-notified-at で全件 None にリセット (今回の事故では推奨)

呼び出し:
    venv/bin/python3 scripts/cleanup_dryrun_state.py --dry-run         # 影響確認
    venv/bin/python3 scripts/cleanup_dryrun_state.py --apply           # 補正実行 (バックアップ自動取得)
    venv/bin/python3 scripts/cleanup_dryrun_state.py --apply --reset-notified-at
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
SENT_LOG_PATH = ROOT / "state" / "chatwork_sent.json"
AUTO_PROPOSAL_HISTORY_DIR = ROOT / "outputs" / "auto_proposal_history"
INDICATIONS_DIR = ROOT / "outputs" / "chatwork_state"


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".dryrun-cleanup-bak.{ts}")
    shutil.copy2(path, bak)
    return bak


def cleanup_chatwork_sent(apply: bool) -> dict:
    """state/chatwork_sent.json から dry_run エントリを削除"""
    if not SENT_LOG_PATH.exists():
        return {"path": str(SENT_LOG_PATH), "exists": False}
    data = json.loads(SENT_LOG_PATH.read_text(encoding="utf-8") or "{}")
    before = len(data)
    survivors = {
        k: v for k, v in data.items()
        if not (isinstance(v, dict) and v.get("dry_run"))
    }
    removed = before - len(survivors)
    info = {
        "path": str(SENT_LOG_PATH),
        "before": before,
        "after": len(survivors),
        "removed_dry_run": removed,
    }
    if apply and removed > 0:
        bak = _backup_file(SENT_LOG_PATH)
        info["backup"] = str(bak) if bak else None
        tmp = SENT_LOG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(survivors, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SENT_LOG_PATH)
    return info


def cleanup_auto_proposal_history(apply: bool) -> list[dict]:
    """outputs/auto_proposal_history/*.yaml から skipped/dry_run エントリを削除"""
    out = []
    if not AUTO_PROPOSAL_HISTORY_DIR.exists():
        return out
    for yml in sorted(AUTO_PROPOSAL_HISTORY_DIR.glob("*.yaml")):
        if yml.suffix.endswith(".tmp"):
            continue
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            out.append({"path": str(yml), "error": "yaml parse failed"})
            continue
        before = len(data)
        survivors = {}
        removed_keys = []
        for rule_id, rec in data.items():
            result = (rec or {}).get("result") or {}
            is_dry = bool(result.get("dry_run"))
            is_skipped = bool(result.get("skipped"))
            if is_dry or is_skipped:
                removed_keys.append(rule_id)
                continue
            survivors[rule_id] = rec
        info = {
            "path": str(yml),
            "before": before,
            "after": len(survivors),
            "removed": len(removed_keys),
            "removed_keys": removed_keys[:10],
        }
        if apply and removed_keys:
            bak = _backup_file(yml)
            info["backup"] = str(bak) if bak else None
            tmp = yml.with_suffix(".yaml.tmp")
            tmp.write_text(yaml.safe_dump(survivors, allow_unicode=True), encoding="utf-8")
            tmp.replace(yml)
        out.append(info)
    return out


def reset_indication_notified_at(apply: bool) -> list[dict]:
    """outputs/chatwork_state/*_indications.json の notified_at を全件 None リセット

    本番実行で再通知が必要な場合に呼ぶ。完了通知 (completion_notified_at) は触らない
    (resolved_confirmed の状態は維持)。
    """
    out = []
    if not INDICATIONS_DIR.exists():
        return out
    for js in sorted(INDICATIONS_DIR.glob("*_indications.json")):
        try:
            data = json.loads(js.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            out.append({"path": str(js), "error": "json parse failed"})
            continue
        inds = data.get("indications", {})
        reset_count = 0
        for rec in inds.values():
            if rec.get("notified_at"):
                rec["notified_at"] = None
                rec["notified_date"] = None
                reset_count += 1
        info = {
            "path": str(js),
            "indications_total": len(inds),
            "reset_notified_at": reset_count,
        }
        if apply and reset_count > 0:
            bak = _backup_file(js)
            info["backup"] = str(bak) if bak else None
            tmp = js.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(js)
        out.append(info)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="dry-run 由来 state 汚染補正")
    parser.add_argument("--apply", action="store_true",
                        help="実際に補正を実行 (バックアップ自動取得)。指定なしは --dry-run と同等")
    parser.add_argument("--dry-run", action="store_true",
                        help="影響範囲のみ表示、ファイルは触らない")
    parser.add_argument("--reset-notified-at", action="store_true",
                        help="indications の notified_at を全件 None にリセット")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run

    print("=" * 60)
    print(f"  dry-run 由来 state 汚染補正  ({'APPLY' if apply else 'DRY-RUN'})")
    print("=" * 60)

    print("\n[1] state/chatwork_sent.json")
    info = cleanup_chatwork_sent(apply)
    print(json.dumps(info, ensure_ascii=False, indent=2))

    print("\n[2] outputs/auto_proposal_history/*.yaml")
    for info in cleanup_auto_proposal_history(apply):
        print(json.dumps(info, ensure_ascii=False, indent=2))

    if args.reset_notified_at:
        print("\n[3] outputs/chatwork_state/*_indications.json — reset notified_at")
        for info in reset_indication_notified_at(apply):
            print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print("\n[3] indications notified_at — skipped (use --reset-notified-at to reset)")

    print("")
    if apply:
        print("✓ 補正実行完了 (バックアップは *.dryrun-cleanup-bak.<timestamp> に保存)")
    else:
        print("→ --apply をつけると実行します")
    return 0


if __name__ == "__main__":
    sys.exit(main())
