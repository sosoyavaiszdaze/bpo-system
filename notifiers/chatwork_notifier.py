"""ChatWork 通知クライアント (ADR-005)

ChatWork API v2 を使ったメッセージ投稿・ファイル添付。
Day 1 範囲: API クライアント本体（テキスト投稿、ファイル添付、retry、rate limit、idempotency）

設計方針:
- トークン未設定でも import エラーにならない（環境変数は send 時に参照）
- 既存 outputs/slack_notify.py / lark_notify.py のパターン踏襲
- requests を使わず urllib（既存コードと統一、追加依存なし）
- multipart/form-data はファイル添付時に手組み（Python 標準のみ）

API 仕様:
- Base URL: https://api.chatwork.com/v2
- Header: X-ChatWorkToken: <token>
- Rate limit: 5 req/sec (WRITE), 100 req/5min (READ)
- メッセージ投稿: POST /rooms/{room_id}/messages
- ファイル添付: POST /rooms/{room_id}/files (multipart/form-data)

Idempotency:
- ChatWork API 自体には Idempotency-Key の概念がない
- (room_id, body のハッシュ) で送信済みを記録し、再送を防ぐ
- 記録は notifiers/_chatwork_sent.json（簡易 JSON ストア）
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

log = logging.getLogger("bpo")

CHATWORK_API_BASE = "https://api.chatwork.com/v2"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_RETRIES = 3
DEFAULT_RATE_LIMIT_PER_SEC = 5  # ChatWork WRITE 系の上限
SENT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "chatwork_sent.json",
)

# R2-#4: idempotency store の同時書込を防ぐ file lock
try:
    import fcntl as _fcntl
    _FCNTL_AVAILABLE = True
except ImportError:
    _FCNTL_AVAILABLE = False


class _LockTimeout(Exception):
    """_FileLock の取得タイムアウト (raise_on_timeout=True 時に発生)"""


class _FileLock:
    """fcntl.flock(LOCK_EX) ベースの排他ロック context manager

    Phase A 用途: launchd 9:00/9:15/9:30 の 3 連射が同時実行されたときの
    chatwork_sent.json の read-modify-write race + ChatWork POST 二重投稿を防ぐ。

    macOS / Linux 前提。Windows では fcntl が import できないため no-op。

    タイムアウト時挙動 (5/8 P2 修正):
        raise_on_timeout=True (デフォルト):
            timeout 経過時に _LockTimeout を raise → 呼出元で安全停止 (送信せず)。
            ChatWork POST フローで先行プロセスが HTTP 詰まりした場合、
            後続プロセスは送信せずに次回 launchd 再試行に委ねる。
        raise_on_timeout=False:
            旧挙動。warning ログ + ロック無しで続行 (二重投稿リスクあり)。
            既存呼出元の互換性確保用、新規利用は非推奨。

    timeout のデフォルト (5/8 P2 修正):
        90 秒。ChatWork API の最大リトライ時間 (timeout 15s × 3 retry + backoff
        ≒ 50-60s) より十分長く設定し、正常な HTTP 完了を待てる範囲にする。
    """

    DEFAULT_TIMEOUT_SEC = 90.0

    def __init__(
        self,
        lock_path: str,
        timeout_sec: Optional[float] = None,
        raise_on_timeout: bool = True,
    ):
        self.lock_path = lock_path
        self.timeout_sec = timeout_sec if timeout_sec is not None else self.DEFAULT_TIMEOUT_SEC
        self.raise_on_timeout = raise_on_timeout
        self._fd: Optional[int] = None

    def __enter__(self):
        if not _FCNTL_AVAILABLE:
            return self  # Windows 等は no-op
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)

        # blocking lock を timeout 付きで取得 (poll 方式)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > deadline:
                    msg = (
                        f"_FileLock: timeout waiting for {self.lock_path} "
                        f"({self.timeout_sec}s)"
                    )
                    if self.raise_on_timeout:
                        # P2 修正: 安全側 (送信せず raise、次回 launchd 再試行に委ねる)
                        log.warning(f"{msg}; raising _LockTimeout to abort send safely")
                        try:
                            os.close(self._fd)
                        except Exception:
                            pass
                        self._fd = None
                        raise _LockTimeout(msg)
                    log.warning(f"{msg}; proceeding without lock (legacy behavior)")
                    return self
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is None or not _FCNTL_AVAILABLE:
            return False
        try:
            _fcntl.flock(self._fd, _fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(self._fd)
        except Exception:
            pass
        self._fd = None
        return False


class ChatWorkError(Exception):
    """ChatWork API 呼び出し失敗"""


class ChatWorkClient:
    """ChatWork API クライアント

    Args:
        api_token: API トークン。None の場合は環境変数 CHATWORK_API_TOKEN を参照
        room_id: デフォルトのルームID（メソッド呼び出し時に上書き可）
        max_retries: 5xx / 429 時のリトライ回数
        rate_limit_per_sec: 秒間最大リクエスト数（簡易ローカル制御）
        sent_log_path: 送信済み記録ファイル（idempotency 用）
        dry_run: True の場合 HTTP 送信をスキップしログのみ
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        room_id: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        rate_limit_per_sec: int = DEFAULT_RATE_LIMIT_PER_SEC,
        sent_log_path: str = SENT_LOG_PATH,
        dry_run: bool = False,
    ):
        self._api_token = api_token  # None 許容、send 時に env 参照
        self._room_id = room_id
        self.max_retries = max_retries
        self.rate_limit_per_sec = max(1, rate_limit_per_sec)
        self.sent_log_path = sent_log_path
        self.dry_run = dry_run
        self._last_request_at: float = 0.0

    # ---------- 設定参照（遅延） ----------

    def _resolve_token(self) -> str:
        token = self._api_token or os.environ.get("CHATWORK_API_TOKEN", "")
        if not token:
            raise ChatWorkError(
                "CHATWORK_API_TOKEN 未設定: 環境変数または引数で渡してください"
            )
        return token

    def _resolve_room_id(self, room_id: Optional[str]) -> str:
        rid = room_id or self._room_id or os.environ.get("CHATWORK_ROOM_ID_PILOTTON", "")
        if not rid:
            raise ChatWorkError(
                "ChatWork room_id 未指定: 引数または CHATWORK_ROOM_ID_PILOTTON で指定してください"
            )
        return str(rid)

    # ---------- Idempotency ストア ----------

    @staticmethod
    def _content_hash(room_id: str, body: str) -> str:
        h = hashlib.sha256()
        h.update(f"{room_id}|{body}".encode("utf-8"))
        return h.hexdigest()

    def _load_sent(self) -> dict:
        if not os.path.exists(self.sent_log_path):
            return {}
        try:
            with open(self.sent_log_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            log.warning(f"chatwork_sent.json 読み込み失敗、空で再開: {self.sent_log_path}")
            return {}

    def _save_sent(self, store: dict) -> None:
        os.makedirs(os.path.dirname(self.sent_log_path), exist_ok=True)
        tmp = f"{self.sent_log_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.sent_log_path)

    def _is_already_sent(self, idempotency_key: str) -> bool:
        # R2-#4: file lock 不要 (read-only、最新を読み逃しても _record_sent 側で
        # exclusive lock 取得後に再確認するため二重投稿には繋がらない)
        store = self._load_sent()
        return idempotency_key in store

    def _record_sent(self, idempotency_key: str, meta: dict) -> None:
        """sha256 idempotency ストアへの read-modify-write を排他ロックで保護 (R2-#4)

        launchd 9:00 / 9:15 / 9:30 の 3 連射が同時実行されたとき、ロック無しだと
        2 つのプロセスが同時に load → 別々の store を save → 後勝ちで先の追記が消える
        race が発生し得る。fcntl.flock(LOCK_EX) で read-modify-write を 1 トランザクションに。

        macOS / Linux の launchd 環境前提。Windows 未対応 (本サービスは Mac mini 専用)。
        """
        os.makedirs(os.path.dirname(self.sent_log_path), exist_ok=True)
        with self._sent_store_lock():
            store = self._load_sent()
            # 二重防御: ロック取得後に再確認 (lock 待機中に別プロセスが先に書き込んだ可能性)
            if idempotency_key in store:
                return
            store[idempotency_key] = meta
            self._save_sent(store)

    def _sent_store_lock(self):
        """fcntl.flock(LOCK_EX) ベースの排他ロック context manager

        ロックファイルは `{sent_log_path}.lock`。プロセス終了時に自動解除。
        Windows 環境では fcntl が無いため no-op (開発用、本番は macOS launchd)。

        timeout 90s + raise_on_timeout=True (5/8 P2 修正):
            ChatWork API の最大リトライ時間 (約 50-60s) より長く待機し、
            正常な HTTP 完了を待つ。それでも取得できない場合は _LockTimeout で
            raise → 安全側 (送信せず) で次回 launchd 再試行に委ねる。
        """
        return _FileLock(self.sent_log_path + ".lock")  # default: 90s + raise_on_timeout=True

    # ---------- Rate Limit ----------

    def _throttle(self) -> None:
        """秒間 N 件の簡易レート制御（ローカルプロセス内のみ）"""
        min_interval = 1.0 / self.rate_limit_per_sec
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_at = time.monotonic()

    # ---------- HTTP 共通 ----------

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[bytes] = None,
        headers: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """リトライ付き HTTP リクエスト

        Returns:
            レスポンス JSON（パース失敗時は {"raw": text}）
        """
        url = f"{CHATWORK_API_BASE}{path}"
        token = self._resolve_token()
        req_headers = {"X-ChatWorkToken": token}
        if headers:
            req_headers.update(headers)

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        return {"raw": body, "status": resp.status}
            except urllib.error.HTTPError as e:
                last_err = e
                # 4xx は基本リトライ不要、429 は backoff
                if e.code == 429 or 500 <= e.code < 600:
                    backoff = min(2 ** attempt, 30)
                    log.warning(
                        f"ChatWork API {method} {path}: HTTP {e.code} attempt {attempt}/{self.max_retries}, "
                        f"backoff {backoff}s"
                    )
                    time.sleep(backoff)
                    continue
                raise ChatWorkError(f"ChatWork API HTTP {e.code}: {e.reason}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                backoff = min(2 ** attempt, 30)
                log.warning(
                    f"ChatWork API {method} {path}: network error attempt {attempt}/{self.max_retries}, "
                    f"backoff {backoff}s ({e})"
                )
                time.sleep(backoff)
                continue
        raise ChatWorkError(
            f"ChatWork API {method} {path}: max retries ({self.max_retries}) exceeded"
        ) from last_err

    # ---------- Public API ----------

    def post_message(
        self,
        body: str,
        room_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        self_unread: int = 0,
    ) -> dict:
        """メッセージを投稿 (5/7 P1-C: check + send + record を 1 つの flock で保護)

        以前は _record_sent() のみロック対象だったため、launchd 9:00/9:15/9:30 が
        重なった場合に 2 プロセスとも _is_already_sent==False と判定 → 両方が POST →
        片方だけ record (もう片方は skip) で **二重投稿** が発生し得た。

        本実装では post_message 全体を _sent_store_lock で囲み、check → POST → record
        を 1 トランザクション化する。HTTP リクエストの数秒間ロックを保持するが、
        3 連射の最短間隔 (15 分) と比較すると影響は無視できる。

        失敗時 (HTTP 例外) は _record_sent を呼ばないので、次回 launchd で同 key で
        再試行できる (rollback 相当)。

        Args:
            body: 投稿本文（Markdown 不可、ChatWork 独自記法 [info][/info] 等は可）
            room_id: 投稿先ルームID（省略時はインスタンス default → env）
            idempotency_key: 再送防止キー（省略時は (room_id, body) ハッシュから自動生成）
            self_unread: 自分宛て未読扱いするか（0/1、ChatWork 仕様）

        Returns:
            {"message_id": "..."} or dry_run の場合は {"dry_run": True, ...}
        """
        rid = self._resolve_room_id(room_id)
        key = idempotency_key or self._content_hash(rid, body)

        # P1-C + P2: check + send + record を flock で 1 トランザクション化。
        # 90s の lock 取得失敗時は _LockTimeout → ChatWorkError 化して呼出元に伝播
        # (送信せず次回 launchd 再試行に委ねる、二重投稿を確実に防ぐ)
        os.makedirs(os.path.dirname(self.sent_log_path), exist_ok=True)
        try:
            with self._sent_store_lock():
                # ロック取得後の最新状態で再確認 (lock 待機中に別プロセスが先送した可能性)
                if self._is_already_sent(key):
                    log.info(f"ChatWork: 送信済みスキップ key={key[:12]}…")
                    sent_rec = (self._load_sent() or {}).get(key) or {}
                    return {
                        "skipped": True,
                        "idempotency_key": key,
                        "message_id": sent_rec.get("message_id"),
                    }

                if self.dry_run:
                    # 5/8 修正: dry-run は本番 idempotency ストアに記録しない (副作用ゼロ原則)。
                    # 旧実装は dry_run=True のレコードを sent_log に残していたため、その後の
                    # 本番実行で同 idempotency_key が "送信済み" と判定され ChatWork 送信が
                    # 完全に塞がれる事故 (5/7 夜) を引き起こした。
                    log.info(f"ChatWork [dry_run] room={rid} len={len(body)} body[:60]={body[:60]!r}")
                    return {"dry_run": True, "idempotency_key": key, "room_id": rid}

                form = urllib.parse.urlencode({"body": body, "self_unread": str(self_unread)}).encode("utf-8")
                result = self._request(
                    "POST",
                    f"/rooms/{rid}/messages",
                    data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                message_id = result.get("message_id", "")
                store = self._load_sent()
                store[key] = {"room_id": rid, "message_id": message_id, "ts": time.time()}
                self._save_sent(store)
                log.info(f"ChatWork 投稿成功 room={rid} message_id={message_id}")
                return result
        except _LockTimeout as e:
            raise ChatWorkError(
                f"ChatWork post_message: lock timeout (90s), refusing to send to avoid "
                f"double-post (key={key[:12]}…). Will retry on next launchd run."
            ) from e

    def upload_file(
        self,
        file_path: str,
        room_id: Optional[str] = None,
        message: str = "",
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """ファイルを添付投稿（multipart/form-data）

        Args:
            file_path: アップロード対象ファイルの絶対パス
            room_id: 投稿先ルームID
            message: ファイルに付随するメッセージ（任意）
            idempotency_key: 再送防止キー（省略時は (room_id, ファイル名+サイズ+message) ハッシュ）
        """
        if not os.path.exists(file_path):
            raise ChatWorkError(f"ファイル不在: {file_path}")

        rid = self._resolve_room_id(room_id)
        size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)

        key = idempotency_key or self._content_hash(rid, f"FILE:{filename}:{size}:{message}")

        # P1-C + P2: check + upload + record を 1 つの flock で 1 トランザクション化
        os.makedirs(os.path.dirname(self.sent_log_path), exist_ok=True)
        try:
            with self._sent_store_lock():
                if self._is_already_sent(key):
                    log.info(f"ChatWork: ファイル送信済みスキップ key={key[:12]}…")
                    return {"skipped": True, "idempotency_key": key}

                if self.dry_run:
                    # 5/8 修正: dry-run は本番 idempotency ストアに記録しない (副作用ゼロ原則)
                    log.info(f"ChatWork [dry_run] upload room={rid} file={filename} size={size}B")
                    return {"dry_run": True, "idempotency_key": key, "file": filename}

                boundary = f"----BPOChatWorkBoundary{uuid.uuid4().hex}"
                with open(file_path, "rb") as f:
                    file_content = f.read()
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

                body_parts: list[bytes] = []
                if message:
                    body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
                    body_parts.append(b'Content-Disposition: form-data; name="message"\r\n\r\n')
                    body_parts.append(message.encode("utf-8"))
                    body_parts.append(b"\r\n")
                body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
                body_parts.append(
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
                )
                body_parts.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
                body_parts.append(file_content)
                body_parts.append(b"\r\n")
                body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
                body = b"".join(body_parts)

                result = self._request(
                    "POST",
                    f"/rooms/{rid}/files",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    timeout=60,  # ファイルアップロードは長め
                )
                file_id = result.get("file_id", "")
                store = self._load_sent()
                store[key] = {
                    "room_id": rid,
                    "file_id": file_id,
                    "filename": filename,
                    "size": size,
                    "ts": time.time(),
                }
                self._save_sent(store)
                log.info(f"ChatWork ファイル投稿成功 room={rid} file_id={file_id} {filename}")
                return result
        except _LockTimeout as e:
            raise ChatWorkError(
                f"ChatWork upload_file: lock timeout (90s), refusing to upload to avoid "
                f"double-upload (key={key[:12]}…). Will retry on next launchd run."
            ) from e


    # ---------- 5/8 v3: メッセージ取得 (ingestion 用) ----------

    def fetch_messages(
        self,
        room_id: Optional[str] = None,
        force: int = 1,
    ) -> list:
        """指定 room の最新メッセージを取得

        ChatWork API: GET /rooms/{room_id}/messages

        Args:
            room_id: 取得対象ルーム ID (省略時は self._room_id)
            force: 1 ならキャッシュを無視して最新取得 (default 1、ingestion 用途)

        Returns:
            messages 配列 (ChatWork API の戻り値そのまま、最大 100 件)
            各要素: {"message_id", "account", "body", "send_time", "update_time", ...}
            未送信時は空 list (HTTP 200 で空 list)。

        Raises:
            ChatWorkError: API エラー / トークン不正 / 権限不足等。
              5/8 P1-A 修正: 旧実装は例外を握りつぶして空 list を返していたが、
              これだと API 障害が "正常終了 + 0 件" として扱われ、回答取り込みが
              ブロックされていることに気づけない。例外は上位 (ingest スクリプト) に
              伝播させ、exit code を非ゼロにすることで運用が検知できるようにする。
        """
        rid = self._resolve_room_id(room_id)
        path = f"/rooms/{rid}/messages?force={int(force)}"
        # _request は HTTP エラー時に ChatWorkError を raise するので、ここでは
        # try/except せずそのまま伝播させる
        result = self._request("GET", path)
        # ChatWork API は messages を直接 list で返すか、{"messages": [...]} の形
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("messages", []) or result.get("raw", [])
        return []


# ---------- module-level helpers ----------

def send_chatwork_message(
    body: str,
    room_id: Optional[str] = None,
    api_token: Optional[str] = None,
    dry_run: bool = False,
    idempotency_key: Optional[str] = None,
) -> dict:
    """単発投稿の便利関数（既存 send_lark_notification と同パターン）

    トークン未設定や API エラーは ERROR ログを出力して空 dict を返す（pipeline 全体を落とさない）。
    """
    try:
        client = ChatWorkClient(api_token=api_token, room_id=room_id, dry_run=dry_run)
        return client.post_message(body, idempotency_key=idempotency_key)
    except ChatWorkError as e:
        log.error(f"ChatWork 投稿失敗: {e}")
        return {}
