"""Threaded TCP server speaking the MPFT protocol (one thread per connection)."""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Optional, Set

from common.protocol import (
    E_BAD_FRAME, E_BAD_STATE, E_INTERNAL, E_UNSUPPORTED_VERSION, SUPPORTED_VERSIONS,
    ConnectionClosed, MsgType, ProtocolError, encode_ranges, parse_chunk_meta, read_frame, send_json,
)
from server.chunk_storage import ChunkStorage
from server.config import ServerConfig
from server.models import TransferSession
from server.transfer_manager import ChunkRejected, TransferManager

log = logging.getLogger("mpft.server")


class FileTransferServer:
    """Accepts connections, negotiates the protocol, and dispatches frames."""

    def __init__(self, config: ServerConfig) -> None:
        config.validate()
        self.cfg = config
        self.storage = ChunkStorage(config)
        self.manager = TransferManager(config, self.storage)
        self._listener: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._conns: Set[socket.socket] = set()
        self._handlers: Set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    @property
    def port(self) -> int:
        """Actual bound port (useful when configured with port 0)."""
        assert self._listener is not None
        return self._listener.getsockname()[1]

    def start(self) -> None:
        """Bind, listen and start accepting in a background thread."""
        ls = socket.socket(socket.AF_INET6 if ":" in self.cfg.host else socket.AF_INET, socket.SOCK_STREAM)
        try:
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ls.bind((self.cfg.host, self.cfg.port))
            ls.listen(128)
        except OSError:
            ls.close()                      # do not leak the half-opened socket if the port is taken
            raise
        ls.settimeout(0.5)
        self._listener = ls
        self._stopping.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="mpft-accept", daemon=True)
        self._accept_thread.start()
        log.info("listening on %s:%d (plain TCP, NOT encrypted)", self.cfg.host, self.port)

    def stop(self) -> None:
        """Stop accepting, close client sockets, flush manifests."""
        self._stopping.set()
        if self._listener:
            self._listener.close()
        with self._lock:
            conns, handlers = list(self._conns), list(self._handlers)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()
        if self._accept_thread:
            self._accept_thread.join(timeout=3)
        for t in handlers:
            t.join(timeout=3)
        self.manager.flush_all()

    def serve_forever(self) -> None:
        """Block until interrupted (used by the CLI)."""
        self.start()
        try:
            while not self._stopping.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ---------------------------------------------------------------- internals
    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                conn, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if len(self._conns) >= self.cfg.max_total_connections:
                    conn.close()
                    continue
                t = threading.Thread(target=self._serve, args=(conn, addr), name=f"mpft-conn-{addr[1]}", daemon=True)
                self._conns.add(conn)
                self._handlers.add(t)
            t.start()

    def _serve(self, conn: socket.socket, addr) -> None:
        conn.settimeout(self.cfg.socket_timeout)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._handle(conn)
        except (ConnectionClosed, socket.timeout, ConnectionError, struct.error):
            pass
        except OSError as exc:
            log.debug("socket error from %s: %s", addr, exc)
        finally:
            conn.close()
            with self._lock:
                self._conns.discard(conn)
                self._handlers.discard(threading.current_thread())

    def _error(self, conn: socket.socket, code: str, message: str) -> None:
        try:
            send_json(conn, MsgType.ERROR, {"code": code, "message": message})
        except OSError:
            pass

    def _handle(self, conn: socket.socket) -> Optional[TransferSession]:
        session: Optional[TransferSession] = None
        try:
            frame = read_frame(conn, self.cfg.max_chunk_size)
            if frame.msg_type != MsgType.HELLO:
                raise ProtocolError(E_BAD_FRAME, "first message must be HELLO")
            versions = frame.json().get("versions")
            common = [v for v in SUPPORTED_VERSIONS if isinstance(versions, list) and v in versions]
            if not common:
                raise ProtocolError(E_UNSUPPORTED_VERSION, f"server supports versions {list(SUPPORTED_VERSIONS)}")
            send_json(conn, MsgType.HELLO_OK, {
                "version": max(common), "max_chunk_size": self.cfg.max_chunk_size,
                "min_chunk_size": self.cfg.min_chunk_size, "max_file_size": self.cfg.max_file_size,
                "timeout": self.cfg.socket_timeout})
            while True:
                frame = read_frame(conn, self.cfg.max_chunk_size)
                t = frame.msg_type
                if t == MsgType.OFFER:
                    if session is not None:
                        raise ProtocolError(E_BAD_STATE, "connection already bound to a transfer")
                    s, have = self.manager.offer(frame.json())
                    self.manager.attach(s)
                    session = s
                    send_json(conn, MsgType.OFFER_OK, {
                        "transfer_id": s.transfer_id, "state": s.status,
                        "have": encode_ranges(have), "saved_as": s.saved_as})
                elif t == MsgType.ATTACH:
                    if session is not None:
                        raise ProtocolError(E_BAD_STATE, "connection already bound to a transfer")
                    s = self.manager.get(frame.json().get("transfer_id"))
                    self.manager.attach(s)
                    session = s
                    send_json(conn, MsgType.ATTACH_OK, {"transfer_id": s.transfer_id, "state": s.status})
                elif t == MsgType.CHUNK:
                    if session is None:
                        raise ProtocolError(E_BAD_STATE, "send OFFER or ATTACH before CHUNK")
                    self._on_chunk(conn, session, frame)
                elif t == MsgType.FINISH:
                    if session is None:
                        raise ProtocolError(E_BAD_STATE, "send OFFER or ATTACH before FINISH")
                    send_json(conn, MsgType.COMPLETE, self.manager.finish(session))
                else:
                    raise ProtocolError(E_BAD_FRAME, f"unexpected message {t.name}")
        except ProtocolError as exc:
            log.warning("protocol error: %s", exc)
            self._error(conn, exc.code, exc.message)
        finally:
            if session is not None:
                self.manager.detach(session)
        return session

    def _on_chunk(self, conn: socket.socket, s: TransferSession, frame) -> None:
        chunk_id, offset, length, digest = parse_chunk_meta(frame.meta)
        try:
            new = self.manager.store_chunk(s, chunk_id, offset, length, digest, frame.payload)
        except ChunkRejected as exc:
            log.info("transfer %s chunk %d rejected: %s", s.transfer_id[:8], chunk_id, exc.message)
            send_json(conn, MsgType.NACK, {"chunk_id": chunk_id, "code": exc.code, "message": exc.message})
            return
        except OSError as exc:
            log.error("disk error storing chunk %d: %s", chunk_id, exc)
            send_json(conn, MsgType.NACK, {"chunk_id": chunk_id, "code": E_INTERNAL, "message": "disk error"})
            return
        send_json(conn, MsgType.ACK, {"chunk_id": chunk_id, "duplicate": not new})
