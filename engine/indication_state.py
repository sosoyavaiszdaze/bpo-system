"""指摘状態管理 (ADR-005 / Day 2 C1)

JSON ファイルベースの指摘状態 DB。analyzers/judgment_db.py のパターンを踏襲。

責務:
- indication レコードの CRUD
- indication_id の安定生成（client_id:rule_id:platform:target_id:first_detected_date）
- 状態遷移ヘルパ（mark_present / mark_clean / mark_resolved 等）
- 月次アーカイブ（resolved_confirmed → archive/{YYYY-MM}.json）

設計:
- 1 クライアントにつき 1 ファイル: outputs/chatwork_state/{client_id}_indications.json
- 完了確定済みは月次でアーカイブディレクトリへ移動
- indication_id に first_detected_date を含めることで「解消→再発」を別 ID 扱いにする
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("bpo")

STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "outputs",
    "chatwork_state",
)
ARCHIVE_SUBDIR = "{client_id}_indications.archive"  # 例: pilotton_indications.archive/

STATE_VERSION = 1

# Status enum
STATUS_OPEN = "open"
STATUS_RESOLVED_PENDING = "resolved_pending"
STATUS_RESOLVED_CONFIRMED = "resolved_confirmed"
STATUS_ARCHIVED = "archived"

VALID_STATUSES = {STATUS_OPEN, STATUS_RESOLVED_PENDING, STATUS_RESOLVED_CONFIRMED, STATUS_ARCHIVED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today_str(today: Optional[str] = None) -> str:
    if today:
        return today
    return datetime.now().strftime("%Y-%m-%d")


def _slugify(value: str) -> str:
    """target_id 用のサニタイズ（英数字 / アンダースコア / ハイフン以外を _ に）"""
    if value is None:
        return "none"
    s = str(value).strip()
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "none"


def build_indication_id(
    client_id: str,
    rule_id: str,
    platform: str,
    target_id: str,
    first_detected_date: str,
) -> str:
    """安定 indication_id を生成

    形式: {client_id}:{rule_id}:{platform}:{target_id}:{YYYY-MM-DD}

    target_id が長すぎる場合は SHA256 8 文字に圧縮（パス長安全）。
    """
    sl_target = _slugify(target_id)
    if len(sl_target) > 64:
        sl_target = "h_" + hashlib.sha256(sl_target.encode("utf-8")).hexdigest()[:12]
    return f"{client_id}:{rule_id}:{platform}:{sl_target}:{first_detected_date}"


class IndicationState:
    """指摘状態 DB（クライアント単位）

    Args:
        client_id: クライアント識別子（pilotton 等）
        state_dir: 状態保存ルート（テスト用に上書き可）
    """

    def __init__(self, client_id: str, state_dir: str = STATE_DIR):
        self.client_id = client_id
        self.state_dir = state_dir
        self.state_path = os.path.join(state_dir, f"{client_id}_indications.json")
        self.archive_dir = os.path.join(state_dir, ARCHIVE_SUBDIR.format(client_id=client_id))
        os.makedirs(self.state_dir, exist_ok=True)
        self._cache: Optional[dict] = None

    # ---------- 基本 IO ----------

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not os.path.exists(self.state_path):
            self._cache = self._empty_state()
            return self._cache
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"indication state 読み込み失敗 {self.state_path}: {e}")
            data = self._empty_state()
        if "indications" not in data:
            data["indications"] = {}
        self._cache = data
        return data

    def _empty_state(self) -> dict:
        return {
            "client_id": self.client_id,
            "version": STATE_VERSION,
            "updated_at": _now_iso(),
            "indications": {},
        }

    def save(self) -> None:
        data = self._load()
        data["updated_at"] = _now_iso()
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def reload(self) -> None:
        """ディスクから強制再読み込み（テスト用）"""
        self._cache = None
        self._load()

    # ---------- レコード CRUD ----------

    def all_indications(self) -> dict[str, dict]:
        return dict(self._load()["indications"])

    def get(self, indication_id: str) -> Optional[dict]:
        return self._load()["indications"].get(indication_id)

    def upsert_detected(
        self,
        rule_id: str,
        platform: str,
        target_id: str,
        severity: str,
        payload: dict[str, Any],
        today: Optional[str] = None,
    ) -> dict:
        """検知された指摘を upsert

        - 同じ (rule_id, platform, target_id) で open / resolved_pending の record があれば更新
        - resolved_confirmed / archived があっても、cooldown 判定はフィルター層で実施（ここでは別 ID として作成）
        - 新規なら open で作成

        Returns:
            indication record
        """
        date = _today_str(today)
        # 既存 active レコードを探す（resolved_confirmed/archived は別 ID で再発扱い）
        existing = self._find_active(rule_id, platform, target_id)
        if existing is not None:
            existing["last_detected_at"] = _now_iso()
            existing["last_detected_date"] = date
            existing["consecutive_clean_days"] = 0
            # 一度 resolved_pending に進んでいたら open に巻き戻す
            if existing["status"] == STATUS_RESOLVED_PENDING:
                existing["status"] = STATUS_OPEN
                self._append_history(existing, "regressed", date=date)
            else:
                self._append_history(existing, "still_present", date=date)
            # severity / payload 更新
            existing["severity"] = severity
            existing["payload"] = payload
            self._cache["indications"][existing["indication_id"]] = existing
            return existing

        # 新規作成
        ind_id = build_indication_id(self.client_id, rule_id, platform, target_id, date)
        record = {
            "indication_id": ind_id,
            "client_id": self.client_id,
            "rule_id": rule_id,
            "platform": platform,
            "target_id": target_id,
            "severity": severity,
            "status": STATUS_OPEN,
            "first_detected_at": _now_iso(),
            "first_detected_date": date,
            "last_detected_at": _now_iso(),
            "last_detected_date": date,
            "last_clean_date": None,
            "consecutive_clean_days": 0,
            "notified_at": None,
            "notified_date": None,
            "completion_notified_at": None,
            "completion_notified_date": None,
            "resolved_at": None,
            "resolved_date": None,
            "payload": payload,
            "history": [
                {"at": _now_iso(), "date": date, "event": "detected"}
            ],
        }
        self._cache["indications"][ind_id] = record
        return record

    def mark_clean(
        self,
        indication_id: str,
        today: Optional[str] = None,
        data_available: bool = True,
        clean_threshold: int = 3,
    ) -> Optional[dict]:
        """clean 観測を記録し、status 遷移を判定

        - data_available=False の場合は consecutive_clean_days を進めない（一時欠損ガード）
        - clean_threshold 連続日達成で resolved_confirmed に遷移
        - その日のうちに同じ indication_id に対して複数回 mark_clean しても 1 日 1 カウント
        """
        record = self.get(indication_id)
        if record is None:
            return None
        if record["status"] in (STATUS_RESOLVED_CONFIRMED, STATUS_ARCHIVED):
            return record  # 確定済みは触らない

        date = _today_str(today)

        if not data_available:
            self._append_history(record, "data_unavailable", date=date)
            self._cache["indications"][indication_id] = record
            return record

        # 同日多重カウント抑止
        if record.get("last_clean_date") == date:
            return record

        record["last_clean_date"] = date
        record["consecutive_clean_days"] = (record.get("consecutive_clean_days") or 0) + 1
        consec = record["consecutive_clean_days"]

        if consec >= clean_threshold:
            record["status"] = STATUS_RESOLVED_CONFIRMED
            record["resolved_at"] = _now_iso()
            record["resolved_date"] = date
            self._append_history(record, "clean", date=date, consecutive=consec)
            self._append_history(record, "resolved_confirmed", date=date)
        else:
            record["status"] = STATUS_RESOLVED_PENDING
            self._append_history(record, "clean", date=date, consecutive=consec)

        self._cache["indications"][indication_id] = record
        return record

    def mark_completion_notified(
        self, indication_id: str, today: Optional[str] = None
    ) -> Optional[dict]:
        record = self.get(indication_id)
        if record is None:
            return None
        date = _today_str(today)
        record["completion_notified_at"] = _now_iso()
        record["completion_notified_date"] = date
        self._append_history(record, "completion_notified", date=date)
        self._cache["indications"][indication_id] = record
        return record

    def mark_indication_notified(
        self, indication_id: str, today: Optional[str] = None
    ) -> Optional[dict]:
        record = self.get(indication_id)
        if record is None:
            return None
        date = _today_str(today)
        record["notified_at"] = _now_iso()
        record["notified_date"] = date
        self._append_history(record, "indication_notified", date=date)
        self._cache["indications"][indication_id] = record
        return record

    # ---------- 検索系 ----------

    def list_by_status(self, status: str) -> list[dict]:
        return [r for r in self._load()["indications"].values() if r["status"] == status]

    def list_open_or_pending(self) -> list[dict]:
        return [
            r for r in self._load()["indications"].values()
            if r["status"] in (STATUS_OPEN, STATUS_RESOLVED_PENDING)
        ]

    def list_pending_completion_notification(self) -> list[dict]:
        """resolved_confirmed かつ完了通知未送のレコード"""
        return [
            r for r in self._load()["indications"].values()
            if r["status"] == STATUS_RESOLVED_CONFIRMED and not r.get("completion_notified_at")
        ]

    def find_by_rule_target(
        self, rule_id: str, platform: str, target_id: str, statuses: Optional[set] = None
    ) -> list[dict]:
        statuses = statuses or VALID_STATUSES
        return [
            r for r in self._load()["indications"].values()
            if r["rule_id"] == rule_id
            and r["platform"] == platform
            and r["target_id"] == target_id
            and r["status"] in statuses
        ]

    def latest_resolved_for(
        self, rule_id: str, platform: str, target_id: str
    ) -> Optional[dict]:
        """同 (rule, platform, target) で最後に resolved_confirmed/archived になったレコード（cooldown 判定用）"""
        candidates = self.find_by_rule_target(
            rule_id, platform, target_id,
            statuses={STATUS_RESOLVED_CONFIRMED, STATUS_ARCHIVED},
        )
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.get("resolved_at") or r.get("last_detected_at") or "")

    # ---------- アーカイブ ----------

    def archive_resolved(self, archive_month: Optional[str] = None) -> int:
        """resolved_confirmed → archived に遷移し、別ファイルへ書き出し

        Args:
            archive_month: 'YYYY-MM' 文字列。省略時は今月。

        Returns:
            アーカイブした件数
        """
        month = archive_month or datetime.now().strftime("%Y-%m")
        os.makedirs(self.archive_dir, exist_ok=True)
        archive_path = os.path.join(self.archive_dir, f"{month}.json")

        existing_archive: list[dict] = []
        if os.path.exists(archive_path):
            try:
                with open(archive_path, "r", encoding="utf-8") as f:
                    existing_archive = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing_archive = []

        data = self._load()
        targets = [
            (k, v) for k, v in data["indications"].items()
            if v["status"] == STATUS_RESOLVED_CONFIRMED
        ]
        if not targets:
            return 0

        archived_count = 0
        for ind_id, record in targets:
            record["status"] = STATUS_ARCHIVED
            self._append_history(record, "archived", month=month)
            existing_archive.append(record)
            del data["indications"][ind_id]
            archived_count += 1

        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(existing_archive, f, ensure_ascii=False, indent=2)
        return archived_count

    # ---------- 内部 ----------

    def _find_active(self, rule_id: str, platform: str, target_id: str) -> Optional[dict]:
        """active = open or resolved_pending"""
        active_statuses = {STATUS_OPEN, STATUS_RESOLVED_PENDING}
        for r in self._load()["indications"].values():
            if (
                r["rule_id"] == rule_id
                and r["platform"] == platform
                and r["target_id"] == target_id
                and r["status"] in active_statuses
            ):
                return r
        return None

    def _append_history(self, record: dict, event: str, **extra) -> None:
        entry = {"at": _now_iso(), "event": event}
        entry.update(extra)
        record.setdefault("history", []).append(entry)
