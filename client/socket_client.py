"""The sender: runs one transfer over several independent TCP connections."""
from __future__ import annotations

import logging
import random
import socket
import struct
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from client.chunker import read_chunk, scan_file
from client.config import ClientConfig
from client.models import (COMPLETED, FAILED, IN_PROGRESS, PENDING, RETRYING, STATE_LETTER, ChunkInfo,
                           ConnectionStats, FileManifest, RateTracker)
from client.scheduler import ChunkScheduler
from common.protocol import (E_BAD_FRAME, PROTOCOL_VERSION, RETRYABLE_NACKS, SUPPORTED_VERSIONS, ConnectionClosed,
                             Frame, MsgType, ProtocolError, decode_ranges, derive_transfer_id, read_frame,
                             send_chunk, send_json)

log = logging.getLogger("mpft.client")
_NET_ERRORS = (OSError, ConnectionClosed, ProtocolError, struct.error)
MB = 1_000_000  # throughput is reported in decimal MB/s

# Job statuses: created scanning connecting transferring paused verifying
#               completed | incomplete | failed | integrity_failed | cancelled
TERMINAL = {"completed", "incomplete", "failed", "integrity_failed", "cancelled"}


class TransferJob:
    """One file transfer. ``run()`` blocks; ``snapshot()`` is safe to call from other threads.

    ``corrupt_hook(chunk_id, failed_attempts, data) -> bytes`` is a *test-only* fault
    injector that alters the payload after its checksum was computed.
    """

    def __init__(self, path: Path, config: ClientConfig, *, display_name: Optional[str] = None,
                 restart: bool = False, job_id: Optional[str] = None,
                 on_event: Optional[Callable[[dict], None]] = None,
                 corrupt_hook: Optional[Callable[[int, int, bytes], bytes]] = None) -> None:
        self.path = Path(path)
        self.cfg = config
        self.display_name = display_name
        self.restart = restart
        self.job_id = job_id or uuid.uuid4().hex[:12]
        self.on_event = on_event
        self.corrupt_hook = corrupt_hook

        self.status = "created"
        self.error: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.manifest: Optional[FileManifest] = None
        self.transfer_id: Optional[str] = None
        self.scheduler: Optional[ChunkScheduler] = None
        self.conn: Dict[int, ConnectionStats] = {}
        self.rate = RateTracker()
        self.peak_rate = 0.0
        self.bytes_acked = 0
        self.bytes_sent = 0
        self.retries = 0
        self.resumed_chunks = 0
        self.resumed_bytes = 0
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self._t0: Optional[float] = None       # monotonic: transfer start (after scan)
        self._t_end: Optional[float] = None
        self._data_end: Optional[float] = None
        self.events: Deque[dict] = deque(maxlen=5000)
        self._seq = 0
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._resume_evt = threading.Event()
        self._resume_evt.set()
        self._socks: Dict[int, socket.socket] = {}
        self._stop_sampler = threading.Event()
        self.scan_progress = 0.0

    # ------------------------------------------------------------------ control
    def pause(self) -> bool:
        """Stop claiming new chunks and close sockets once the in-flight chunk is done."""
        with self._lock:
            if self.status != "transferring":
                return False
            self._resume_evt.clear()
            self.status = "paused"
        self._emit("paused", "Transfer paused (workers finish their current chunk, then close sockets)")
        return True

    def unpause(self) -> bool:
        """Continue a paused transfer; workers reconnect and carry on."""
        with self._lock:
            if self.status != "paused":
                return False
            self.status = "transferring"
            self._resume_evt.set()
        self._emit("unpaused", "Transfer continued")
        return True

    def cancel(self) -> None:
        """Abort. Chunks the server already validated are kept, so the transfer can be resumed."""
        self._cancel.set()
        self._resume_evt.set()
        with self._lock:
            socks = list(self._socks.values())
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.scheduler:
            self.scheduler.wake_all()
        self._emit("cancel", "Cancel requested")

    # --------------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        """Execute the whole transfer and return the result dictionary."""
        self.started_at = time.time()
        sampler = threading.Thread(target=self._sample_loop, daemon=True, name="mpft-sampler")
        sampler.start()
        try:
            self._run()
        except Exception as exc:  # pragma: no cover - last-resort guard
            log.exception("unexpected error")
            self._finalize("failed", error=f"unexpected error: {exc}")
        finally:
            self._stop_sampler.set()
            sampler.join(timeout=1)
        assert self.result is not None
        return self.result

    def _run(self) -> None:
        try:
            self.cfg.validate()
            self._set("scanning")
            name = self.display_name or self.path.name
            self.manifest = scan_file(self.path, self.cfg.chunk_size, name,
                                      progress=lambda d, t: setattr(self, "scan_progress", d / t if t else 1.0))
        except (OSError, ValueError) as exc:
            return self._finalize("failed", error=f"cannot read file: {exc}")
        m = self.manifest
        self.transfer_id = derive_transfer_id(m.name, m.size, m.sha256, m.chunk_size)
        self._emit("scan_complete", f"{m.name}: {m.size} bytes, {len(m.chunks)} chunks, sha256 {m.sha256[:12]}...")
        self._set("connecting")
        self._t0 = time.monotonic()
        try:
            sock0, have = self._open_offer()
        except _NET_ERRORS as exc:
            return self._finalize("failed", error=f"cannot start transfer: {exc}")
        self.resumed_chunks = len(have)
        self.resumed_bytes = sum(m.chunks[i].length for i in have)
        if have:
            self._emit("resume", f"Server already holds {len(have)} of {len(m.chunks)} chunks; sending the rest")
        self.scheduler = ChunkScheduler(m.chunks, set(have), self.cfg.max_retries)
        n = min(self.cfg.connections, self.scheduler.remaining())
        if n == 0:
            self._close(sock0)
        else:
            self._set("transferring")
            barrier = threading.Barrier(n, timeout=10)
            threads = []
            for cid in range(n):
                self.conn[cid] = ConnectionStats(cid)
                t = threading.Thread(target=self._worker, args=(cid, sock0 if cid == 0 else None, barrier),
                                     name=f"mpft-worker-{cid}", daemon=True)
                threads.append(t)
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self._data_end = time.monotonic()
        if self._cancel.is_set():
            return self._finalize("cancelled", error="cancelled by user")
        counts = self.scheduler.counts()
        if counts[COMPLETED] < len(m.chunks):
            return self._finalize(
                "incomplete", error=f"{len(m.chunks) - counts[COMPLETED]} chunk(s) not delivered "
                f"({counts[FAILED]} failed permanently); resume to send the rest")
        self._set("verifying")
        self._emit("verifying", "All chunks acknowledged; asking the server to verify the final SHA-256")
        try:
            reply = self._finish_remote()
        except _NET_ERRORS as exc:
            return self._finalize("incomplete", error=f"could not complete verification: {exc}")
        mapping = {"verified": "completed", "integrity_failed": "integrity_failed", "incomplete": "incomplete"}
        status = mapping.get(reply.get("status"), "failed")
        self._finalize(status, server=reply)

    # ------------------------------------------------------------ connections
    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=self.cfg.connect_timeout)
        try:
            sock.settimeout(self.cfg.socket_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            send_json(sock, MsgType.HELLO, {"versions": [PROTOCOL_VERSION], "client": "mpft-client"})
            version = self._expect(sock, MsgType.HELLO_OK).json().get("version")
            if version not in SUPPORTED_VERSIONS:
                raise ProtocolError(E_BAD_FRAME, f"server chose unsupported version {version}")
            return sock
        except BaseException:
            self._close(sock)
            raise

    @staticmethod
    def _close(sock: Optional[socket.socket]) -> None:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _expect(sock: socket.socket, *types: MsgType) -> Frame:
        frame = read_frame(sock)
        if frame.msg_type == MsgType.ERROR and MsgType.ERROR not in types:
            info = frame.json()
            raise ProtocolError(str(info.get("code", "ERROR")), str(info.get("message", "server error")))
        if frame.msg_type not in types:
            raise ProtocolError(E_BAD_FRAME, f"unexpected {frame.msg_type.name}")
        return frame

    def _open_offer(self):
        m = self.manifest
        assert m is not None
        sock = self._connect()
        try:
            send_json(sock, MsgType.OFFER, {
                "transfer_id": self.transfer_id, "filename": m.name, "file_size": m.size,
                "file_sha256": m.sha256, "chunk_size": m.chunk_size, "total_chunks": len(m.chunks),
                "restart": self.restart})
            reply = self._expect(sock, MsgType.OFFER_OK).json()
            have = decode_ranges(reply.get("have", []), len(m.chunks))
        except BaseException:
            self._close(sock)
            raise
        self._emit("offer_ok", f"Server accepted transfer {self.transfer_id[:8]} (state: {reply.get('state')})")
        return sock, have

    def _connect_attach(self, conn_id: int) -> socket.socket:
        sock = self._connect()
        try:
            send_json(sock, MsgType.ATTACH, {"transfer_id": self.transfer_id, "conn_id": conn_id})
            self._expect(sock, MsgType.ATTACH_OK)
        except BaseException:
            self._close(sock)
            raise
        return sock

    def _finish_remote(self) -> Dict[str, Any]:
        sock = self._connect_attach(-1)
        try:
            sock.settimeout(self.cfg.finish_timeout)
            send_json(sock, MsgType.FINISH, {"transfer_id": self.transfer_id})
            return self._expect(sock, MsgType.COMPLETE).json()
        finally:
            self._close(sock)

    # ----------------------------------------------------------------- worker
    def _backoff(self, n: int) -> float:
        return min(self.cfg.backoff_max, self.cfg.backoff_base * 2 ** max(0, n - 1)) + random.uniform(0, self.cfg.backoff_base)

    def _register(self, conn_id: int, sock: Optional[socket.socket]) -> None:
        with self._lock:
            if sock is None:
                self._socks.pop(conn_id, None)
            else:
                self._socks[conn_id] = sock

    def _drop(self, conn_id: int, sock: Optional[socket.socket]) -> None:
        self._register(conn_id, None)
        self._close(sock)

    def _worker(self, conn_id: int, sock: Optional[socket.socket], barrier: threading.Barrier) -> None:
        st = self.conn[conn_id]
        sched = self.scheduler
        assert sched is not None
        st.started = time.monotonic()
        fh = None
        failures = 0
        try:
            fh = open(self.path, "rb")
            if sock is None:
                st.status = "connecting"
                try:
                    sock = self._connect_attach(conn_id)
                except _NET_ERRORS as exc:
                    failures += 1
                    self._net_error(st, exc, None)
            if sock is not None:
                self._register(conn_id, sock)
                st.status = "active"
                self._emit("connected", f"Connection {conn_id} established", conn_id=conn_id)
            try:
                barrier.wait()   # all sockets are up before any chunk is claimed
            except threading.BrokenBarrierError:
                pass
            while True:
                if not self._resume_evt.is_set():
                    self._drop(conn_id, sock)
                    sock = None
                    st.status = "paused"
                    while not self._resume_evt.wait(0.2):
                        pass
                if self._cancel.is_set():
                    break
                chunk = sched.claim(conn_id, self._cancel.is_set)
                if chunk is None:
                    break
                st.current_chunk = chunk.chunk_id
                self._emit("chunk_started", f"Connection {conn_id} sending chunk {chunk.chunk_id}",
                           conn_id=conn_id, chunk_id=chunk.chunk_id)
                try:
                    if sock is None:
                        st.status = "reconnecting"
                        sock = self._connect_attach(conn_id)
                        self._register(conn_id, sock)
                        self._emit("connected", f"Connection {conn_id} re-established", conn_id=conn_id)
                    st.status = "active"
                    data = read_chunk(fh, chunk)
                    payload = self.corrupt_hook(chunk.chunk_id, sched.attempts[chunk.chunk_id], data) \
                        if self.corrupt_hook else data
                    send_chunk(sock, chunk.chunk_id, chunk.offset, payload, chunk.digest)
                    with self._lock:
                        self.bytes_sent += len(payload)
                    frame = self._expect(sock, MsgType.ACK, MsgType.NACK)
                    reply = frame.json()
                    if reply.get("chunk_id") != chunk.chunk_id:
                        raise ProtocolError(E_BAD_FRAME, "reply refers to a different chunk")
                except _NET_ERRORS as exc:
                    self._drop(conn_id, sock)
                    sock = None
                    sched.release(chunk.chunk_id, penalize=False)
                    st.current_chunk = None
                    if self._cancel.is_set():       # we shut the socket ourselves: not a failure, not a retry
                        break
                    failures += 1
                    self._net_error(st, exc, chunk.chunk_id)
                    if failures > self.cfg.max_reconnects:
                        st.status = "dead"
                        self._emit("conn_dead", f"Connection {conn_id} gave up after {failures} failures",
                                   conn_id=conn_id)
                        break
                    self._cancel.wait(self._backoff(failures))
                    continue
                failures = 0
                if frame.msg_type == MsgType.ACK:
                    self._on_ack(st, chunk, bool(reply.get("duplicate")))
                else:
                    self._on_nack(st, chunk, str(reply.get("code")), str(reply.get("message")))
                st.current_chunk = None
        finally:
            if st.status != "dead":
                st.status = "closed"
            st.current_chunk = None
            st.ended = time.monotonic()
            self._drop(conn_id, sock)
            if fh:
                fh.close()

    def _net_error(self, st: ConnectionStats, exc: BaseException, chunk_id: Optional[int]) -> None:
        with self._lock:
            st.reconnects += 1
            st.retries += 1 if chunk_id is not None else 0
            self.retries += 1 if chunk_id is not None else 0
        st.last_error = f"{type(exc).__name__}: {exc}"
        if not self._cancel.is_set():
            where = f" (chunk {chunk_id} re-queued)" if chunk_id is not None else ""
            self._emit("conn_error", f"Connection {st.conn_id} failed: {st.last_error}{where}", conn_id=st.conn_id,
                       chunk_id=chunk_id)

    def _on_ack(self, st: ConnectionStats, chunk: ChunkInfo, duplicate: bool) -> None:
        assert self.scheduler is not None
        with self._lock:
            self.bytes_acked += chunk.length
            st.bytes_acked += chunk.length
            st.chunks_acked += 1
        self.rate.add(chunk.length)
        st.rate.add(chunk.length)
        self.scheduler.complete(chunk.chunk_id)
        note = " (server already had it)" if duplicate else ""
        self._emit("chunk_acked", f"Chunk {chunk.chunk_id} acknowledged on connection {st.conn_id}{note}",
                   conn_id=st.conn_id, chunk_id=chunk.chunk_id)

    def _on_nack(self, st: ConnectionStats, chunk: ChunkInfo, code: str, message: str) -> None:
        assert self.scheduler is not None
        self._emit("chunk_nack", f"Chunk {chunk.chunk_id} rejected ({code}: {message})",
                   conn_id=st.conn_id, chunk_id=chunk.chunk_id)
        if code in RETRYABLE_NACKS:
            with self._lock:
                st.retries += 1
                self.retries += 1
            will_retry = self.scheduler.release(chunk.chunk_id, penalize=True)
            if will_retry:
                self._cancel.wait(self._backoff(self.scheduler.attempts[chunk.chunk_id]))
            else:
                self._emit("chunk_failed", f"Chunk {chunk.chunk_id} failed after {self.cfg.max_retries} retries",
                           conn_id=st.conn_id, chunk_id=chunk.chunk_id)
        else:
            self.scheduler.fail(chunk.chunk_id)
            self._emit("chunk_failed", f"Chunk {chunk.chunk_id} permanently rejected ({code})",
                       conn_id=st.conn_id, chunk_id=chunk.chunk_id)

    # ------------------------------------------------------------ bookkeeping
    def _set(self, status: str) -> None:
        with self._lock:
            self.status = status
        self._emit("status", f"Status: {status}")

    def _emit(self, type_: str, message: str, **extra: Any) -> None:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": time.time(), "type": type_, "message": message, **extra}
            self.events.append(ev)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:  # never let a listener break the transfer
                log.exception("event listener failed")

    def events_since(self, seq: int = 0, limit: int = 500) -> List[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > seq][-limit:]

    def _sample_loop(self) -> None:
        while not self._stop_sampler.wait(0.25):
            self.peak_rate = max(self.peak_rate, self.rate.rate())

    def _elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        end = self._t_end if self._t_end is not None else time.monotonic()
        return max(end - self._t0, 0.0)

    def _finalize(self, status: str, error: Optional[str] = None, server: Optional[dict] = None) -> None:
        self._t_end = time.monotonic()
        self.finished_at = time.time()
        m = self.manifest
        self.error = error
        data_sec = (self._data_end - self._t0) if (self._data_end and self._t0) else self._elapsed()
        avg_bps = (self.bytes_acked / data_sec) if data_sec > 0 else 0.0
        peak = max(self.peak_rate, self.rate.rate(), avg_bps)   # a peak can never be below the average
        server = server or {}
        counts = self.scheduler.counts() if self.scheduler else {}
        validated = server.get("validated_chunks", counts.get(COMPLETED, 0))
        self.result = {
            "status": status, "error": error, "job_id": self.job_id, "transfer_id": self.transfer_id,
            "file_name": m.name if m else None, "file_size": m.size if m else None,
            "original_sha256": m.sha256 if m else None, "received_sha256": server.get("received_sha256"),
            "checksum_match": (server.get("received_sha256") == m.sha256) if server.get("received_sha256") and m else None,
            "expected_chunks": len(m.chunks) if m else 0, "validated_chunks": validated,
            "missing_count": server.get("missing_count", (len(m.chunks) - counts.get(COMPLETED, 0)) if m else 0),
            "missing_chunks": server.get("missing_chunks", []),
            "failed_chunks": counts.get(FAILED, 0),
            "final_size": m.size if status == "completed" and m else None,
            "saved_as": server.get("saved_as"),
            "elapsed": self._elapsed(), "data_seconds": data_sec,
            "avg_mb_s": (self.bytes_acked / data_sec / MB) if data_sec > 0 else 0.0,
            "peak_mb_s": peak / MB, "bytes_acked": self.bytes_acked, "bytes_sent": self.bytes_sent,
            "resumed_chunks": self.resumed_chunks, "resumed_bytes": self.resumed_bytes,
            "retries": self.retries, "connections_requested": self.cfg.connections,
            "connections_used": len(self.conn), "chunk_size": self.cfg.chunk_size,
        }
        with self._lock:
            self.status = status
        self._emit("finished", f"Transfer {status}" + (f": {error}" if error else ""))

    # --------------------------------------------------------------- snapshot
    def snapshot(self, events_tail: int = 60) -> Dict[str, Any]:
        """JSON-serialisable view of the live state (used by the API / dashboard)."""
        m = self.manifest
        sched = self.scheduler
        counts = sched.counts() if sched else {}
        elapsed = self._elapsed()
        total = m.size if m else 0
        done_total = self.bytes_acked + self.resumed_bytes
        avg = self.bytes_acked / elapsed if elapsed > 0 else 0.0
        cur = self.rate.rate() if self.status in ("transferring", "verifying") else 0.0
        if self.result is not None:                      # finished: use the same figure the saved result reports
            avg = self.result["avg_mb_s"] * MB
        speed = cur or avg
        remaining = max(total - done_total, 0)
        eta = remaining / speed if (speed > 0 and self.status == "transferring") else None
        now = time.monotonic()
        conns = []
        for cid, st in sorted(self.conn.items()):
            span = ((st.ended or now) - st.started) if st.started else 0
            conns.append({
                "conn_id": cid, "status": st.status, "bytes_acked": st.bytes_acked, "chunks_acked": st.chunks_acked,
                "retries": st.retries, "reconnects": st.reconnects, "current_chunk": st.current_chunk,
                "current_rate": st.rate.rate() if st.status == "active" else 0.0,
                "avg_rate": (st.bytes_acked / span) if span > 0 else 0.0, "last_error": st.last_error})
        return {
            "job_id": self.job_id, "status": self.status, "error": self.error, "transfer_id": self.transfer_id,
            "file": {"name": m.name, "size": m.size, "sha256": m.sha256, "chunks": len(m.chunks)} if m else None,
            "config": {"host": self.cfg.host, "port": self.cfg.port, "connections": self.cfg.connections,
                       "chunk_size": self.cfg.chunk_size},
            "scan_progress": self.scan_progress,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "elapsed": elapsed, "eta_seconds": eta,
            "bytes": {"acked": self.bytes_acked, "resumed": self.resumed_bytes, "done": done_total,
                      "total": total, "sent": self.bytes_sent},
            "throughput": {"current": cur, "average": avg, "peak": max(self.peak_rate, cur, avg)},
            "chunks": {"total": len(m.chunks) if m else 0, "completed": counts.get(COMPLETED, 0),
                       "pending": counts.get(PENDING, 0), "in_progress": counts.get(IN_PROGRESS, 0),
                       "retrying": counts.get(RETRYING, 0), "failed": counts.get(FAILED, 0)},
            "chunk_states": "".join(STATE_LETTER[s] for s in list(sched.state)) if sched else "",
            "chunk_owner": {str(k): v for k, v in dict(sched.owner).items()} if sched else {},
            "chunk_attempts": {str(i): a for i, a in enumerate(list(sched.attempts)) if a} if sched else {},
            "connections": conns, "retries": self.retries, "resumed_chunks": self.resumed_chunks,
            "events": self.events_since(max(self._seq - events_tail, 0), events_tail),
            "last_seq": self._seq, "result": self.result, "now": time.time(),
        }
