"""Shared chunk scheduler: hands each chunk to exactly one worker at a time."""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Set

from client.models import COMPLETED, FAILED, IN_PROGRESS, PENDING, RETRYING, ChunkInfo


class ChunkScheduler:
    """Thread-safe work queue with explicit per-chunk state.

    ``claim`` pops from a deque under a lock, so two workers can never receive the
    same chunk. A failed chunk is put back (RETRYING) and any worker may take it.
    """

    def __init__(self, chunks: List[ChunkInfo], already_done: Set[int], max_retries: int) -> None:
        self.chunks = chunks
        self.max_retries = max_retries
        self._cond = threading.Condition()
        self.state: List[str] = [COMPLETED if c.chunk_id in already_done else PENDING for c in chunks]
        self.attempts: List[int] = [0] * len(chunks)          # failed attempts so far
        self.owner: Dict[int, int] = {}                         # chunk_id -> conn_id while in progress
        self._queue: Deque[int] = deque(c.chunk_id for c in chunks if c.chunk_id not in already_done)

    def remaining(self) -> int:
        """Chunks not yet completed or permanently failed."""
        with self._cond:
            return sum(1 for s in self.state if s in (PENDING, RETRYING, IN_PROGRESS))

    def claim(self, conn_id: int, should_stop: Callable[[], bool]) -> Optional[ChunkInfo]:
        """Return the next chunk for ``conn_id``; block while others may still re-queue work.

        Returns None when there is nothing left to do (or ``should_stop()`` is true).
        """
        with self._cond:
            while True:
                if should_stop():
                    return None
                if self._queue:
                    cid = self._queue.popleft()
                    self.state[cid] = IN_PROGRESS
                    self.owner[cid] = conn_id
                    return self.chunks[cid]
                if not self.owner:      # nothing queued, nothing in flight
                    return None
                self._cond.wait(0.1)

    def complete(self, chunk_id: int) -> None:
        with self._cond:
            self.state[chunk_id] = COMPLETED
            self.owner.pop(chunk_id, None)
            self._cond.notify_all()

    def release(self, chunk_id: int, penalize: bool) -> bool:
        """Return an unfinished chunk to the queue. True if it will be retried.

        ``penalize`` counts the failure against the chunk's retry budget (NACKs);
        connection failures are bounded by the worker's reconnect budget instead.
        """
        with self._cond:
            self.owner.pop(chunk_id, None)
            if penalize:
                self.attempts[chunk_id] += 1
            if penalize and self.attempts[chunk_id] > self.max_retries:
                self.state[chunk_id] = FAILED
                self._cond.notify_all()
                return False
            self.state[chunk_id] = RETRYING
            self._queue.append(chunk_id)
            self._cond.notify_all()
            return True

    def fail(self, chunk_id: int) -> None:
        """Permanently fail a chunk (fatal NACK)."""
        with self._cond:
            self.owner.pop(chunk_id, None)
            self.state[chunk_id] = FAILED
            self._cond.notify_all()

    def counts(self) -> Dict[str, int]:
        with self._cond:
            out = {PENDING: 0, IN_PROGRESS: 0, COMPLETED: 0, FAILED: 0, RETRYING: 0}
            for s in self.state:
                out[s] += 1
            return out

    def wake_all(self) -> None:
        with self._cond:
            self._cond.notify_all()
