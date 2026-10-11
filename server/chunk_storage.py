"""On-disk storage: staging file, persistent manifests, safe publishing."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from common.integrity import expected_chunk_length, sha256_file, sha256_hex
from common.protocol import SHA256_HEX_RE, TRANSFER_ID_RE
from server.config import ServerConfig
from server.models import TransferSession

log = logging.getLogger("mpft.storage")

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_SAFE = re.compile(r"[^A-Za-z0-9._ \-]")


def sanitize_filename(name: str) -> str:
    """Reduce a client-supplied name to a safe basename (no directories, no traversal)."""
    name = name.replace("\x00", "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE.sub("_", name).strip(" .")
    if not name or set(name) <= {"."}:
        name = "file.bin"
    stem, dot, ext = name.partition(".")
    if stem.lower() in _RESERVED:
        name = "_" + name
    if len(name) > 128:
        base, dot, ext = name.rpartition(".")
        name = (base[: 120 - len(ext)] + "." + ext) if dot and len(ext) < 16 else name[:128]
    return name


class ChunkStorage:
    """Owns every path the server writes to; nothing is built from client strings."""

    def __init__(self, config: ServerConfig) -> None:
        self.cfg = config
        for d in (config.received_dir, config.staging_dir, config.manifests_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- paths (transfer IDs are validated hex, so they cannot contain separators)
    def _check_id(self, tid: str) -> str:
        if not TRANSFER_ID_RE.match(tid):
            raise ValueError("invalid transfer id")
        return tid

    def part_path(self, tid: str) -> Path:
        return self.cfg.staging_dir / f"{self._check_id(tid)}.part"

    def manifest_path(self, tid: str) -> Path:
        return self.cfg.manifests_dir / f"{self._check_id(tid)}.json"

    # -- staging file
    def ensure_open(self, s: TransferSession) -> None:
        """Open (and pre-size) the staging file if it is not already open."""
        with s.lock:
            if s.fh is not None:
                return
            path = self.part_path(s.transfer_id)
            mode = "r+b" if path.exists() else "w+b"
            s.fh = open(path, mode, buffering=0)
            s.fh.truncate(s.file_size)

    def close(self, s: TransferSession) -> None:
        with s.lock:
            if s.jfh is not None:
                s.jfh.close()
                s.jfh = None
            if s.fh is not None:
                try:
                    s.fh.close()
                finally:
                    s.fh = None

    def write_chunk(self, s: TransferSession, offset: int, data: bytes) -> None:
        """Write one validated chunk at its byte offset (caller holds ``s.lock``)."""
        self.ensure_open(s)
        assert s.fh is not None
        s.fh.seek(offset)
        s.fh.write(data)

    def read_chunk(self, s: TransferSession, chunk_id: int) -> bytes:
        length = expected_chunk_length(s.file_size, s.chunk_size, chunk_id)
        with open(self.part_path(s.transfer_id), "rb") as fh:
            fh.seek(chunk_id * s.chunk_size)
            return fh.read(length)

    # -- journal: one tiny unbuffered write per validated chunk, so a killed process forgets nothing.
    # (An OS crash / power cut can still lose page-cache data; revalidate() re-hashes chunks on recovery.)
    def journal_path(self, tid: str) -> Path:
        return self.cfg.manifests_dir / f"{self._check_id(tid)}.journal"

    def append_journal(self, s: TransferSession, chunk_id: int, digest_hex: str) -> None:
        """Record a validated chunk (caller holds ``s.lock``)."""
        if s.jfh is None:
            s.jfh = open(self.journal_path(s.transfer_id), "ab", buffering=0)
        s.jfh.write(f"{chunk_id} {digest_hex}\n".encode("ascii"))

    def _drop_journal(self, s: TransferSession) -> None:
        if s.jfh is not None:
            s.jfh.close()
            s.jfh = None
        try:
            self.journal_path(s.transfer_id).unlink()
        except FileNotFoundError:
            pass

    def _replay_journal(self, s: TransferSession) -> int:
        """Add chunks recorded after the last manifest snapshot. A torn final line is ignored."""
        try:
            raw = self.journal_path(s.transfer_id).read_bytes()
        except FileNotFoundError:
            return 0
        added = 0
        for line in raw.split(b"\n")[:-1]:               # the last element is "" or a partial (torn) line
            parts = line.split(b" ")
            try:
                cid, digest = int(parts[0]), parts[1].decode("ascii")
            except (ValueError, IndexError, UnicodeDecodeError):
                continue
            if len(parts) == 2 and 0 <= cid < s.total_chunks and SHA256_HEX_RE.match(digest) and cid not in s.chunks:
                s.chunks[cid] = digest
                added += 1
        return added

    # -- manifest
    def save_manifest(self, s: TransferSession) -> None:
        """Atomically persist session state (write temp file, then os.replace)."""
        with s.lock:
            doc = {
                "format": 1, "transfer_id": s.transfer_id, "filename": s.filename,
                "file_size": s.file_size, "file_sha256": s.file_sha256,
                "chunk_size": s.chunk_size, "total_chunks": s.total_chunks,
                "status": s.status, "saved_as": s.saved_as, "final_sha256": s.final_sha256,
                "created_at": s.created_at, "updated_at": time.time(),
                "chunks": {str(k): v for k, v in sorted(s.chunks.items())},
            }
            path = self.manifest_path(s.transfer_id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, path)
            s.last_saved = time.monotonic()
            s.dirty = False
            self._drop_journal(s)          # the snapshot now contains everything the journal held

    def load_all(self) -> List[TransferSession]:
        """Reload sessions from manifests (server restart). Chunks need revalidation."""
        sessions: List[TransferSession] = []
        for path in sorted(self.cfg.manifests_dir.glob("*.json")):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                tid = self._check_id(d["transfer_id"])
                s = TransferSession(
                    transfer_id=tid, filename=sanitize_filename(d["filename"]),
                    file_size=int(d["file_size"]), file_sha256=d["file_sha256"],
                    chunk_size=int(d["chunk_size"]), total_chunks=int(d["total_chunks"]),
                    status=d.get("status", "receiving"), saved_as=d.get("saved_as"),
                    final_sha256=d.get("final_sha256"), created_at=d.get("created_at", time.time()),
                    chunks={int(k): v for k, v in d.get("chunks", {}).items()},
                )
                s.needs_revalidation = s.status != "complete"
                if s.status == "receiving":
                    n = self._replay_journal(s)
                    if n:
                        log.info("transfer %s: recovered %d chunk(s) from the journal", tid[:8], n)
                sessions.append(s)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("ignoring unreadable manifest %s: %s", path.name, exc)
        return sessions

    def revalidate(self, s: TransferSession) -> List[int]:
        """Re-hash every recorded chunk in the staging file; drop the ones that no longer match."""
        invalid: List[int] = []
        path = self.part_path(s.transfer_id)
        if not path.exists():
            invalid = sorted(s.chunks)
            s.chunks.clear()
            return invalid
        with s.lock:
            for cid, digest in list(s.chunks.items()):
                try:
                    ok = sha256_hex(self.read_chunk(s, cid)) == digest
                except (OSError, ValueError):
                    ok = False
                if not ok:
                    invalid.append(cid)
                    del s.chunks[cid]
        if invalid:
            log.warning("transfer %s: %d chunk(s) failed revalidation", s.transfer_id[:8], len(invalid))
        return invalid

    # -- finalisation
    def unique_final_name(self, s: TransferSession) -> str:
        """Pick a name in received/ that does not overwrite an existing file."""
        base = Path(s.filename)
        candidates = [base.name, f"{base.stem}_{s.transfer_id[:8]}{base.suffix}"]
        candidates += [f"{base.stem}_{s.transfer_id[:8]}_{i}{base.suffix}" for i in range(1, 1000)]
        for name in candidates:
            if not (self.cfg.received_dir / name).exists():
                return name
        raise OSError("could not find a free output name")

    def hash_staged(self, s: TransferSession) -> str:
        """SHA-256 of the staged file as it exists on disk."""
        if s.fh is not None:
            os.fsync(s.fh.fileno())
        return sha256_file(self.part_path(s.transfer_id))

    def publish(self, s: TransferSession) -> str:
        """Atomically move the verified staging file into received/. Returns the final name."""
        self.close(s)
        name = self.unique_final_name(s)
        os.replace(self.part_path(s.transfer_id), self.cfg.received_dir / name)
        return name

    def discard(self, s: TransferSession) -> None:
        """Delete staging data and manifest (restart / failed integrity)."""
        self.close(s)
        for p in (self.part_path(s.transfer_id), self.manifest_path(s.transfer_id), self.journal_path(s.transfer_id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def published_path(self, s: TransferSession) -> Optional[Path]:
        if not s.saved_as:
            return None
        p = self.cfg.received_dir / Path(s.saved_as).name
        return p if p.exists() else None
