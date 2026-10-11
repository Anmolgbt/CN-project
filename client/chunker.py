"""Split a file into numbered chunks and checksum each one."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Callable, Optional

from client.models import ChunkInfo, FileManifest


def scan_file(path: Path, chunk_size: int, display_name: Optional[str] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> FileManifest:
    """Single pass over the file: whole-file SHA-256 plus (id, offset, length, SHA-256) per chunk.

    The last chunk may be shorter than ``chunk_size``; an empty file yields zero chunks.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    size = path.stat().st_size
    whole = hashlib.sha256()
    chunks = []
    with open(path, "rb") as fh:
        offset, cid = 0, 0
        while offset < size:
            data = fh.read(min(chunk_size, size - offset))
            if len(data) == 0:
                raise OSError("file shrank while scanning")
            whole.update(data)
            chunks.append(ChunkInfo(cid, offset, len(data), hashlib.sha256(data).hexdigest()))
            offset += len(data)
            cid += 1
            if progress:
                progress(offset, size)
    return FileManifest(display_name or path.name, size, whole.hexdigest(), chunk_size, chunks)


def read_chunk(fh: BinaryIO, chunk: ChunkInfo) -> bytes:
    """Read exactly one chunk from an open file handle."""
    fh.seek(chunk.offset)
    data = fh.read(chunk.length)
    if len(data) != chunk.length:
        raise OSError(f"short read for chunk {chunk.chunk_id}")
    return data
