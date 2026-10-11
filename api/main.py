"""FastAPI application: dashboard API + optional embedded receiver.

Run:  uvicorn api.main:app --port 8000      (or: python scripts/run_dashboard.py)
Env:  MPFT_DATA_DIR, MPFT_RECEIVER_PORT (default 9000), MPFT_EMBEDDED_RECEIVER=0 to use an external server,
      MPFT_ALLOW_REMOTE_TARGETS=1 to let the dashboard send to non-loopback hosts.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.job_manager import JobManager
from api.routes import router, ws_router
from api.schemas import ApiError
from api.websocket_manager import WebSocketManager
from server.config import ServerConfig
from server.socket_server import FileTransferServer

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MPFT_DATA_DIR", ROOT / "data")))
    embedded_receiver: bool = os.environ.get("MPFT_EMBEDDED_RECEIVER", "1") != "0"
    receiver_port: int = int(os.environ.get("MPFT_RECEIVER_PORT", "9000"))
    allow_remote_targets: bool = os.environ.get("MPFT_ALLOW_REMOTE_TARGETS", "0") == "1"
    serve_frontend: bool = True


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    st = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        receiver: Optional[FileTransferServer] = None
        scfg: Optional[ServerConfig] = None
        port = st.receiver_port
        if st.embedded_receiver:
            scfg = ServerConfig(host="127.0.0.1", port=st.receiver_port, data_dir=st.data_dir)
            receiver = FileTransferServer(scfg)
            try:
                receiver.start()
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot start the embedded receiver on 127.0.0.1:{st.receiver_port} ({exc}). Another receiver may be "
                    "running: stop it, set MPFT_RECEIVER_PORT to a free port, or set MPFT_EMBEDDED_RECEIVER=0.") from exc
            port = receiver.port
        app.state.receiver = receiver
        app.state.jobs = JobManager(st.data_dir, scfg, port, st.allow_remote_targets)
        app.state.ws = WebSocketManager()
        app.state.ws.start(app.state.jobs.snapshot_any)
        yield
        await app.state.ws.stop()
        for j in app.state.jobs.jobs.values():
            j.cancel()
        if receiver:
            receiver.stop()

    app = FastAPI(title="Multipath File Transfer API", version="1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": {"code": "invalid_request", "message": msg}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": {"code": "http_error", "message": str(exc.detail)}}, status_code=exc.status_code)

    app.include_router(router)
    app.include_router(ws_router)
    dist = ROOT / "frontend" / "dist"
    if st.serve_frontend and dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
