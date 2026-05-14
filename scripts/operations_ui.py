#!/usr/bin/env python3
"""Run the read-only operations console.

Install optional UI dependencies first:
  venv/bin/pip install fastapi uvicorn
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.stores.db import DEFAULT_DB_PATH


def create_app(db_path: Path | str | None = None):
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import HTMLResponse
        from fastapi.templating import Jinja2Templates
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by operator environment.
        raise SystemExit("FastAPI UI dependencies are missing. Run: venv/bin/pip install fastapi uvicorn") from exc

    from engine.operations_ui.queries import build_console_context

    # With postponed annotations on Python 3.9, FastAPI resolves Request from
    # module globals rather than this function's local import.
    globals()["Request"] = Request
    templates = Jinja2Templates(directory=str(ROOT / "templates" / "operations_ui"))
    app = FastAPI(title="Zynect Operations Console", version="0.1.0")
    selected_db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        context = build_console_context(selected_db_path, root=ROOT)
        return templates.TemplateResponse(request, "dashboard.html", context)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "db_path": str(selected_db_path)}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zynect read-only operations console.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by operator environment.
        raise SystemExit("uvicorn is missing. Run: venv/bin/pip install fastapi uvicorn") from exc

    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
