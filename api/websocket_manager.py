"""WebSocket fan-out: one background task pushes real job snapshots to subscribers."""
from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Callable, Dict, Optional, Set

from fastapi import WebSocket


class WebSocketManager:
    """Tracks subscribers per job and broadcasts snapshots every ``interval`` seconds."""

    def __init__(self, interval: float = 0.4) -> None:
        self.interval = interval
        self._subs: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._task: Optional[asyncio.Task] = None

    async def connect(self, job_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._subs[job_id].add(ws)

    def disconnect(self, job_id: str, ws: WebSocket) -> None:
        self._subs[job_id].discard(ws)
        if not self._subs[job_id]:
            self._subs.pop(job_id, None)

    def start(self, snapshot_for: Callable[[str], Optional[dict]]) -> None:
        self._task = asyncio.create_task(self._loop(snapshot_for))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def push(self, job_id: str, ws: WebSocket, snap: dict) -> None:
        try:
            await ws.send_json({"type": "snapshot", "data": snap})
        except Exception:
            self.disconnect(job_id, ws)

    async def _loop(self, snapshot_for: Callable[[str], Optional[dict]]) -> None:
        while True:
            await asyncio.sleep(self.interval)
            for job_id, sockets in list(self._subs.items()):
                snap = snapshot_for(job_id)
                if snap is None:
                    continue
                for ws in list(sockets):
                    await self.push(job_id, ws, snap)
