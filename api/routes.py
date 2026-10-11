"""REST + WebSocket routes (control plane). File *data* never flows through here."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from api.job_manager import JobManager
from api.schemas import ApiError, StartBenchmark, StartTransfer
from common.protocol import MAX_CHUNKS

router = APIRouter(prefix="/api")


def jm(request: Request) -> JobManager:
    return request.app.state.jobs


# ------------------------------------------------------------------- general
@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    return {"status": "ok", "receiver": "embedded" if jm(request).embedded else "external"}


@router.get("/config")
def config(request: Request) -> Dict[str, Any]:
    m = jm(request)
    return {"default_host": "127.0.0.1", "default_port": m.default_port, "embedded_receiver": m.embedded is not None,
            "connection_options": [1, 2, 4, 8], "chunk_size_options": [262144, 1048576, 4194304],
            "max_connections": 16, "max_chunks": MAX_CHUNKS, "max_upload_bytes": m.max_upload,
            "received_dir": "data/received" if m.embedded else None,
            "remote_targets_allowed": m.allow_remote_targets, "encrypted": False}


# --------------------------------------------------------------------- files
@router.put("/files", status_code=201)
async def upload_file(request: Request, name: str = Query(min_length=1, max_length=255)) -> Dict[str, Any]:
    """Stage a file for the *sender* on this machine (browsers cannot expose real file paths).

    This is NOT the transfer: the transfer itself uses raw TCP sockets from client/ to server/.
    """
    m = jm(request)
    file_id, path = m.new_upload(name)
    h, size = hashlib.sha256(), 0
    try:
        with open(path, "wb") as fh:
            async for part in request.stream():
                size += len(part)
                if size > m.max_upload:
                    raise ApiError(413, "file_too_large", f"limit is {m.max_upload} bytes")
                h.update(part)
                await asyncio.to_thread(fh.write, part)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return m.register_upload(file_id, name, size, h.hexdigest())


@router.get("/files")
def list_files(request: Request) -> List[Dict[str, Any]]:
    return jm(request).list_files()


@router.delete("/files/{file_id}", status_code=204)
def delete_file(request: Request, file_id: str) -> None:
    jm(request).delete_file(file_id)


# ----------------------------------------------------------------- transfers
@router.post("/transfers", status_code=201)
def start_transfer(request: Request, body: StartTransfer) -> Dict[str, Any]:
    job = jm(request).start_transfer(body)
    return {"job_id": job.job_id}


@router.get("/transfers")
def list_transfers(request: Request) -> List[Dict[str, Any]]:
    """Transfers started since the API process launched (live and finished)."""
    return sorted(jm(request).live_summaries(), key=lambda s: s.get("started_at") or 0, reverse=True)


@router.get("/transfers/{job_id}")
def get_transfer(request: Request, job_id: str) -> Dict[str, Any]:
    snap = jm(request).snapshot_any(job_id)
    if snap is None:
        raise ApiError(404, "job_not_found", "no such transfer")
    return snap


@router.get("/transfers/{job_id}/connections")
def get_connections(request: Request, job_id: str) -> List[Dict[str, Any]]:
    snap = jm(request).snapshot_any(job_id)
    if snap is None:
        raise ApiError(404, "job_not_found", "no such transfer")
    return snap["connections"]


@router.get("/transfers/{job_id}/events")
def get_events(request: Request, job_id: str, since: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000)):
    m = jm(request)
    if job_id in m.jobs:
        return m.jobs[job_id].events_since(since, limit)
    snap = m.snapshot_any(job_id)
    if snap is None:
        raise ApiError(404, "job_not_found", "no such transfer")
    return [e for e in snap["events"] if e["seq"] > since][-limit:]


@router.post("/transfers/{job_id}/pause")
def pause(request: Request, job_id: str) -> Dict[str, Any]:
    if not jm(request).get_job(job_id).pause():
        raise ApiError(409, "not_pausable", "only a running transfer can be paused")
    return {"ok": True}


@router.post("/transfers/{job_id}/continue")
def unpause(request: Request, job_id: str) -> Dict[str, Any]:
    if not jm(request).get_job(job_id).unpause():
        raise ApiError(409, "not_paused", "the transfer is not paused")
    return {"ok": True}


@router.post("/transfers/{job_id}/cancel")
def cancel(request: Request, job_id: str) -> Dict[str, Any]:
    job = jm(request).get_job(job_id)
    if job.status in ("completed", "incomplete", "failed", "integrity_failed", "cancelled"):
        raise ApiError(409, "already_finished", "the transfer already finished")
    job.cancel()
    return {"ok": True}


@router.post("/transfers/{job_id}/resume", status_code=201)
def resume(request: Request, job_id: str) -> Dict[str, Any]:
    return {"job_id": jm(request).resume(job_id).job_id}


@router.get("/transfers/{job_id}/download")
def download(request: Request, job_id: str) -> FileResponse:
    path, name = jm(request).completed_output(job_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


# ------------------------------------------------------------------- history
@router.get("/history")
def history(request: Request, limit: int = Query(100, ge=1, le=1000)) -> List[Dict[str, Any]]:
    return jm(request).history(limit)


@router.get("/history/{job_id}")
def history_item(request: Request, job_id: str) -> Dict[str, Any]:
    return jm(request).history_record(job_id)


# ---------------------------------------------------------------- benchmarks
@router.post("/benchmarks", status_code=201)
def start_benchmark(request: Request, body: StartBenchmark) -> Dict[str, Any]:
    return jm(request).bench_public(jm(request).start_benchmark(body))


@router.get("/benchmarks")
def list_benchmarks(request: Request) -> List[Dict[str, Any]]:
    return jm(request).list_benchmarks()


@router.get("/benchmarks/{bench_id}")
def get_benchmark(request: Request, bench_id: str) -> Dict[str, Any]:
    return jm(request).get_bench(bench_id)


@router.post("/benchmarks/{bench_id}/cancel")
def cancel_benchmark(request: Request, bench_id: str) -> Dict[str, Any]:
    jm(request).cancel_benchmark(bench_id)
    return {"ok": True}


# ----------------------------------------------------------------- websocket
ws_router = APIRouter()


@ws_router.websocket("/ws/transfers/{job_id}")
async def transfer_ws(ws: WebSocket, job_id: str) -> None:
    """Pushes the real job snapshot ~2.5x per second until the client disconnects."""
    app = ws.app
    await app.state.ws.connect(job_id, ws)
    try:
        snap = app.state.jobs.snapshot_any(job_id)
        if snap is not None:
            await app.state.ws.push(job_id, ws, snap)
        while True:
            await ws.receive_text()     # keeps the socket open; client messages are ignored
    except WebSocketDisconnect:
        pass
    finally:
        app.state.ws.disconnect(job_id, ws)
