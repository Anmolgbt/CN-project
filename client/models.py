"""Plain data models and a small throughput tracker for the sender."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Tuple

# Chunk states (one letter each in snapshots).
PENDING, IN_PROGRESS, COMPLETED, FAILED, RETRYING = "pending", "in_progress", "completed", "failed", "retrying"
STATE_LETTER = {PENDING: "P", IN_PROGRESS: "I", COMPLETED: "C", FAILED: "F", RETRYING: "R"}


@dataclass(frozen=True)
class ChunkInfo:
    """Immutable description of one chunk of the source file."""

    chunk_id: int
    offset: int
    length: int
    sha256: str   # hex

    @property
    def digest(self) -> bytes:
        return bytes.fromhex(self.sha256)


@dataclass
class FileManifest:
    """Everything the sender knows about the file before transferring."""

    name: str
    size: int
    sha256: str
    chunk_size: int
    chunks: list


class RateTracker:
    """Sliding-window byte rate. Thread-safe."""

    def __init__(self, window: float = 2.0) -> None:
        self.window = window
        self._events: Deque[Tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def add(self, n: int) -> None:
        with self._lock:
            self._events.append((time.monotonic(), n))

    def rate(self) -> float:
        """Bytes per second over the last ``window`` seconds."""
        now = time.monotonic()
        with self._lock:
            while self._events and now - self._events[0][0] > self.window:
                self._events.popleft()
            if not self._events:
                return 0.0
            total = sum(n for _, n in self._events)
            span = max(now - self._events[0][0], 0.25)
            return total / min(max(span, 0.25), self.window)


@dataclass
class ConnectionStats:
    """Per-socket statistics shown in the dashboard."""

    conn_id: int
    status: str = "idle"          # idle | connecting | active | paused | reconnecting | closed | dead
    bytes_acked: int = 0
    chunks_acked: int = 0
    retries: int = 0
    reconnects: int = 0
    current_chunk: Optional[int] = None
    last_error: Optional[str] = None
    started: Optional[float] = None
    ended: Optional[float] = None
    rate: RateTracker = field(default_factory=RateTracker, repr=False)
