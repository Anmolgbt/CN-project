"""Session registry and the rules for accepting chunks and finishing transfers."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Tuple

from common.integrity import expected_chunk_length, sha256_hex, total_chunks
from common.protocol import (
    E_BAD_CHUNK, E_BAD_METADATA, E_BAD_STATE, E_CHECKSUM, E_DUP_MISMATCH, E_UNKNOWN_TRANSFER,
    SHA256_HEX_RE, ProtocolError, derive_transfer_id,
)
from server.chunk_storage import ChunkStorage, sanitize_filename
from server.config import ServerConfig
from server.models import TransferSession

log = logging.getLogger("mpft.manager")


class ChunkRejected(Exception):
    """A chunk failed validation; becomes a NACK with ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class TransferManager:
    """Thread-safe owner of all sessions."""

    def __init__(self, config: ServerConfig, storage: ChunkStorage) -> None:
        self.cfg = config
        self.storage = storage
        self.sessions: Dict[str, TransferSession] = {}
        self._lock = threading.RLock()
        for s in storage.load_all():
            self.sessions[s.transfer_id] = s
        if self.sessions:
            log.info("recovered %d session(s) from manifests", len(self.sessions))

    # ------------------------------------------------------------------ offer
    def validate_offer(self, m: Dict[str, Any]) -> Dict[str, Any]:
        """Validate every OFFER field. Raises ProtocolError(BAD_METADATA)."""
        c = self.cfg
        def bad(msg: str) -> ProtocolError:
            return ProtocolError(E_BAD_METADATA, msg)
        name = m.get("filename")
        if not isinstance(name, str) or not 1 <= len(name) <= 255:
            raise bad("filename must be a string of 1-255 characters")
        size, csize, count = m.get("file_size"), m.get("chunk_size"), m.get("total_chunks")
        if not _is_int(size) or not 0 <= size <= c.max_file_size:
            raise bad(f"file_size must be 0..{c.max_file_size}")
        if not _is_int(csize) or not c.min_chunk_size <= csize <= c.max_chunk_size:
            raise bad(f"chunk_size must be {c.min_chunk_size}..{c.max_chunk_size}")
        if count != total_chunks(size, csize):
            raise bad("total_chunks does not match file_size/chunk_size")
        if count > min(c.max_chunks, 65_536):
            raise bad("too many chunks; choose a larger chunk size")
        sha = m.get("file_sha256")
        if not isinstance(sha, str) or not SHA256_HEX_RE.match(sha):
            raise bad("file_sha256 must be 64 lowercase hex characters")
        tid = m.get("transfer_id")
        if tid != derive_transfer_id(name, size, sha, csize):
            raise bad("transfer_id does not match file metadata")
        return {"filename": name, "file_size": size, "chunk_size": csize,
                "total_chunks": count, "file_sha256": sha, "transfer_id": tid,
                "restart": bool(m.get("restart", False))}

    def offer(self, raw: Dict[str, Any]) -> Tuple[TransferSession, List[int]]:
        """Create or resume a session. Returns it and the chunk IDs already held."""
        m = self.validate_offer(raw)
        with self._lock:
            s = self.sessions.get(m["transfer_id"])
            if s is not None and m["restart"]:
                log.info("transfer %s: restart requested, discarding old state", s.transfer_id[:8])
                self.storage.discard(s)
                del self.sessions[s.transfer_id]
                s = None
            if s is not None and (s.file_size, s.file_sha256, s.chunk_size) != (
                    m["file_size"], m["file_sha256"], m["chunk_size"]):
                raise ProtocolError(E_BAD_METADATA, "metadata differs from the stored transfer")
            if s is not None and s.status == "complete" and self.storage.published_path(s) is None:
                self.storage.discard(s)          # published file was removed; start over
                del self.sessions[s.transfer_id]
                s = None
            if s is None:
                s = TransferSession(
                    transfer_id=m["transfer_id"], filename=sanitize_filename(m["filename"]),
                    file_size=m["file_size"], file_sha256=m["file_sha256"],
                    chunk_size=m["chunk_size"], total_chunks=m["total_chunks"])
                self.sessions[s.transfer_id] = s
                self.storage.ensure_open(s)
                self.storage.save_manifest(s)
                log.info("transfer %s: new session for %r (%d chunks)", s.transfer_id[:8], s.filename, s.total_chunks)
            else:
                if s.status == "integrity_failed":
                    s.status = "receiving"
                    s.chunks.clear()
                if s.status == "receiving":
                    if s.needs_revalidation:
                        self.storage.revalidate(s)
                        s.needs_revalidation = False
                    self.storage.ensure_open(s)
                    self.storage.save_manifest(s)
                log.info("transfer %s: resumed, %d/%d chunks held", s.transfer_id[:8], len(s.chunks), s.total_chunks)
            with s.lock:
                have = list(range(s.total_chunks)) if s.status == "complete" else sorted(s.chunks)
        return s, have

    def get(self, transfer_id: Any) -> TransferSession:
        with self._lock:
            s = self.sessions.get(transfer_id) if isinstance(transfer_id, str) else None
        if s is None:
            raise ProtocolError(E_UNKNOWN_TRANSFER, "no such transfer")
        return s

    # ------------------------------------------------------------ connections
    def attach(self, s: TransferSession) -> None:
        with s.lock:
            if s.active_connections >= self.cfg.max_connections_per_transfer:
                raise ProtocolError("LIMIT_EXCEEDED", "too many connections for this transfer")
            s.active_connections += 1
            s.peak_connections = max(s.peak_connections, s.active_connections)
            if s.status == "receiving":
                self.storage.ensure_open(s)

    def detach(self, s: TransferSession) -> None:
        """A connection of this session closed; flush state, release the file when idle."""
        with s.lock:
            s.active_connections = max(0, s.active_connections - 1)
            if s.status == "receiving":
                self.storage.save_manifest(s)
                if s.active_connections == 0:
                    self.storage.close(s)

    # ----------------------------------------------------------------- chunks
    def store_chunk(self, s: TransferSession, chunk_id: int, offset: int, length: int,
                    digest: bytes, payload: bytes) -> bool:
        """Validate and store a chunk. True = newly stored, False = duplicate (idempotent)."""
        hexdigest = digest.hex()
        try:
            if s.status == "complete":
                if s.chunks.get(chunk_id) == hexdigest or chunk_id < s.total_chunks:
                    return False  # transfer already finished; late duplicate is harmless
                raise ChunkRejected(E_BAD_STATE, "transfer already complete")
            if not 0 <= chunk_id < s.total_chunks:
                raise ChunkRejected(E_BAD_CHUNK, f"chunk_id {chunk_id} out of range")
            if offset != chunk_id * s.chunk_size:
                raise ChunkRejected(E_BAD_CHUNK, "offset does not match chunk_id")
            want = expected_chunk_length(s.file_size, s.chunk_size, chunk_id)
            if length != want or len(payload) != length:
                raise ChunkRejected(E_BAD_CHUNK, f"length must be {want}")
            if sha256_hex(payload) != hexdigest:
                raise ChunkRejected(E_CHECKSUM, "payload does not match its SHA-256")
            with s.lock:
                existing = s.chunks.get(chunk_id)
                if existing is not None:
                    if existing == hexdigest:
                        s.duplicates += 1
                        return False
                    raise ChunkRejected(E_DUP_MISMATCH, "chunk already stored with a different checksum")
                self.storage.write_chunk(s, offset, payload)
                s.chunks[chunk_id] = hexdigest
                self.storage.append_journal(s, chunk_id, hexdigest)
                s.dirty = True
                if time.monotonic() - s.last_saved >= self.cfg.manifest_flush_interval:
                    self.storage.save_manifest(s)
            return True
        except ChunkRejected:
            s.rejected += 1
            raise

    # ----------------------------------------------------------------- finish
    def finish(self, s: TransferSession) -> Dict[str, Any]:
        """Verify completeness and the final SHA-256, then publish atomically."""
        with s.lock:
            base = {"transfer_id": s.transfer_id, "original_sha256": s.file_sha256,
                    "expected_chunks": s.total_chunks, "file_size": s.file_size}
            if s.status == "complete":
                return {**base, "status": "verified", "received_sha256": s.final_sha256,
                        "validated_chunks": s.total_chunks, "missing_count": 0,
                        "missing_chunks": [], "saved_as": s.saved_as}
            missing = [i for i in range(s.total_chunks) if i not in s.chunks]
            if missing:
                self.storage.save_manifest(s)
                return {**base, "status": "incomplete", "validated_chunks": len(s.chunks),
                        "missing_count": len(missing), "missing_chunks": missing[:200],
                        "received_sha256": None, "saved_as": None}
            self.storage.ensure_open(s)
            received = self.storage.hash_staged(s)
            if received != s.file_sha256:
                log.error("transfer %s: FINAL CHECKSUM MISMATCH", s.transfer_id[:8])
                invalid = len(s.chunks)
                s.chunks.clear()
                s.status = "integrity_failed"
                self.storage.close(s)
                self.storage.save_manifest(s)
                try:
                    self.storage.part_path(s.transfer_id).unlink()
                except FileNotFoundError:
                    pass
                return {**base, "status": "integrity_failed", "received_sha256": received,
                        "validated_chunks": 0, "missing_count": invalid, "missing_chunks": [],
                        "saved_as": None}
            s.saved_as = self.storage.publish(s)
            s.final_sha256 = received
            s.status = "complete"
            self.storage.save_manifest(s)
            log.info("transfer %s: verified and published as %s", s.transfer_id[:8], s.saved_as)
            return {**base, "status": "verified", "received_sha256": received,
                    "validated_chunks": s.total_chunks, "missing_count": 0, "missing_chunks": [],
                    "saved_as": s.saved_as}

    def flush_all(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            with s.lock:
                if s.status == "receiving":
                    self.storage.save_manifest(s)
                    self.storage.close(s)
