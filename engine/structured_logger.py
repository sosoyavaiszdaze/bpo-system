"""構造化ログ (R4a: 5/7、JSON line 形式、opt-in)

責務: 既存の `logging.getLogger("bpo")` の人間向けログ出力を維持しつつ、
      機械処理しやすい JSON line 形式のログを **追加** で `logs/daily-chatwork.json.log`
      に書き出す。初動調査の grep / jq での検索性を上げる。

設計方針:
    - 既存 stdout/stderr / launchd 標準ログ (logs/daily-chatwork.{out,err}.log) は破壊しない
    - StructuredFileHandler を追加で root logger にアタッチ
    - run_id / client_id / step / status をコンテキストとして付与
    - 環境変数 STRUCTURED_LOGS=0 で完全 OFF (デフォルト ON)

使い方:
    from engine.structured_logger import StructuredLogContext, install_structured_handler

    install_structured_handler()                  # main 起動時 1 回
    with StructuredLogContext(run_id="...", client_id="pilotton", step="audit_fetch"):
        log.info("...")                           # JSON line に context が自動注入される

JSON line の例:
    {"ts":"2026-05-08T09:00:00+09:00","level":"INFO","logger":"bpo",
     "run_id":"R-20260508-0900-pilotton","client_id":"pilotton","step":"audit_fetch",
     "status":"in_progress","msg":"Meta API: 3キャンペーン取得完了"}
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_LOG_PATH = ROOT / "logs" / "daily-chatwork.json.log"

JST = timezone(timedelta(hours=9))


# ========== Context ==========

_run_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("run_id", default=None)
_client_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_id", default=None)
_step_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("step", default=None)
_status_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("status", default=None)


class StructuredLogContext:
    """run_id / client_id / step / status を with ブロック内で有効化する context manager"""

    def __init__(
        self,
        run_id: Optional[str] = None,
        client_id: Optional[str] = None,
        step: Optional[str] = None,
        status: str = "in_progress",
    ):
        self.run_id = run_id
        self.client_id = client_id
        self.step = step
        self.status = status
        self._tokens = []

    def __enter__(self):
        if self.run_id is not None:
            self._tokens.append(("run_id", _run_id_var.set(self.run_id)))
        if self.client_id is not None:
            self._tokens.append(("client_id", _client_id_var.set(self.client_id)))
        if self.step is not None:
            self._tokens.append(("step", _step_var.set(self.step)))
        self._tokens.append(("status", _status_var.set(self.status)))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 例外発生時は status=failed、正常終了時は呼び出し側で set_status("done") する想定
        if exc_type is not None:
            _status_var.set("failed")
        # token を逆順に reset
        for name, token in reversed(self._tokens):
            try:
                if name == "run_id":     _run_id_var.reset(token)
                elif name == "client_id": _client_id_var.reset(token)
                elif name == "step":      _step_var.reset(token)
                elif name == "status":    _status_var.reset(token)
            except (ValueError, LookupError):
                pass
        return False


def set_status(status: str) -> None:
    """現コンテキストの status を更新 ("done" / "failed" / "skipped" 等)"""
    _status_var.set(status)


def new_run_id(prefix: str = "R") -> str:
    """run_id を生成: R-YYYYMMDD-HHMM-{client_id}-{short_uuid}"""
    now = datetime.now(JST)
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{now.strftime('%Y%m%d-%H%M')}-{short}"


# ========== JSON Formatter ==========

class JsonLineFormatter(logging.Formatter):
    """LogRecord を 1 行 JSON にシリアライズ"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=JST).isoformat(timespec="seconds")
        payload = {
            "ts":        ts,
            "level":     record.levelname,
            "logger":    record.name,
            "msg":       record.getMessage(),
            "run_id":    _run_id_var.get(),
            "client_id": _client_id_var.get(),
            "step":      _step_var.get(),
            "status":    _status_var.get(),
        }
        # 例外がある場合は traceback を 1 行化
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info).replace("\n", " | ")
        # extra= で渡された任意フィールド
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_"):
                continue
            if k in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process",
                "getMessage", "asctime",
            ):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, ensure_ascii=False)


# ========== Handler installation ==========

_install_lock = threading.Lock()
_installed = False


def install_structured_handler(
    json_log_path: Optional[Path] = None,
    logger_name: str = "bpo",
    level: int = logging.INFO,
) -> None:
    """root の "bpo" logger に JSON line ファイルハンドラを追加

    既存 stdout/stderr ハンドラは触らない。複数回呼んでも 1 回だけ追加される。
    環境変数 STRUCTURED_LOGS=0 のときは何もしない。
    """
    if os.environ.get("STRUCTURED_LOGS", "1") == "0":
        return

    global _installed
    with _install_lock:
        if _installed:
            return
        path = json_log_path or DEFAULT_JSON_LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(JsonLineFormatter())
        handler.set_name("structured_json_handler")

        # "bpo" logger と "daily_chatwork" logger 両方に追加
        for name in (logger_name, "daily_chatwork", "preflight"):
            lg = logging.getLogger(name)
            # 既存 handler に同名があれば skip
            if not any(h.get_name() == "structured_json_handler" for h in lg.handlers):
                lg.addHandler(handler)
            if lg.level == logging.NOTSET:
                lg.setLevel(level)
        _installed = True
