"""SHA-256 helpers and chunk-geometry arithmetic shared by client and server."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

READ_BLOCK = 1024 * 1024


def sha256_bytes(data: bytes) -> bytes:
    """Return the raw 32-byte SHA-256 digest of ``data``."""
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, progress: Optional[Callable[[int], None]] = None) -> str:
    """Hash a whole file in 1 MiB blocks and return the hex digest."""
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(READ_BLOCK)
            if not block:
                break
            h.update(block)
            done += len(block)
            if progress:
                progress(done)
    return h.hexdigest()


def total_chunks(file_size: int, chunk_size: int) -> int:
    """Number of chunks needed for a file. An empty file has zero chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if file_size < 0:
        raise ValueError("file_size must not be negative")
    return (file_size + chunk_size - 1) // chunk_size


def expected_chunk_length(file_size: int, chunk_size: int, chunk_id: int) -> int:
    """Length chunk ``chunk_id`` must have (the last chunk may be shorter)."""
    count = total_chunks(file_size, chunk_size)
    if not 0 <= chunk_id < count:
        raise ValueError("chunk_id out of range")
    return min(chunk_size, file_size - chunk_id * chunk_size)
