"""In-memory model of one receiving session (one transfer)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import BinaryIO, Dict, Optional


@dataclass
class TransferSession:
    """State of a transfer on the server. ``chunks`` maps chunk_id -> SHA-256 hex."""

    transfer_id: str
    filename: str               # already sanitized
    file_size: int
    file_sha256: str
    chunk_size: int
    total_chunks: int
    status: str = "receiving"   # receiving | complete | integrity_failed
    chunks: Dict[int, str] = field(default_factory=dict)
    saved_as: Optional[str] = None
    final_sha256: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    needs_revalidation: bool = False
    fh: Optional[BinaryIO] = None
    jfh: Optional[BinaryIO] = None      # append-only journal of validated chunks
    active_connections: int = 0
    peak_connections: int = 0
    duplicates: int = 0
    rejected: int = 0
    last_saved: float = 0.0
    dirty: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def received_bytes(self) -> int:
        """Bytes covered by validated chunks."""
        if not self.chunks:
            return 0
        last = self.total_chunks - 1
        total = len(self.chunks) * self.chunk_size
        if last in self.chunks:
            total -= self.chunk_size - (self.file_size - last * self.chunk_size)
        return total
