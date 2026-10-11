"""Test helpers: a raw protocol client and a fault-injecting TCP proxy."""
from __future__ import annotations

import hashlib
import socket
import threading
from typing import Optional

from common.integrity import total_chunks
from common.protocol import MsgType, derive_transfer_id, read_frame, send_chunk, send_json


def make_offer(name: str, data: bytes, chunk_size: int, sha: Optional[str] = None, restart: bool = False) -> dict:
    sha = sha or hashlib.sha256(data).hexdigest()
    return {"transfer_id": derive_transfer_id(name, len(data), sha, chunk_size), "filename": name,
            "file_size": len(data), "file_sha256": sha, "chunk_size": chunk_size,
            "total_chunks": total_chunks(len(data), chunk_size), "restart": restart}


class RawClient:
    """Speaks the protocol by hand so tests can send out-of-order, duplicate or corrupt frames."""

    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        send_json(self.sock, MsgType.HELLO, {"versions": [1]})
        assert read_frame(self.sock).msg_type == MsgType.HELLO_OK

    def request(self, msg_type: MsgType, obj: dict):
        send_json(self.sock, msg_type, obj)
        f = read_frame(self.sock)
        return f.msg_type, f.json()

    def offer(self, name: str, data: bytes, chunk_size: int, **kw):
        return self.request(MsgType.OFFER, make_offer(name, data, chunk_size, **kw))

    def chunk(self, chunk_id: int, data: bytes, chunk_size: int, *, payload: Optional[bytes] = None,
              offset: Optional[int] = None, digest: Optional[bytes] = None):
        start = chunk_id * chunk_size
        piece = data[start:start + chunk_size]
        send_chunk(self.sock, chunk_id, start if offset is None else offset, piece if payload is None else payload,
                   hashlib.sha256(piece).digest() if digest is None else digest)
        f = read_frame(self.sock)
        return f.msg_type, f.json()

    def finish(self, transfer_id: str):
        return self.request(MsgType.FINISH, {"transfer_id": transfer_id})

    def close(self) -> None:
        self.sock.close()


class FaultProxy:
    """TCP proxy in front of the server.

    * ``kill_first``/``kill_after``: the first N connections are cut after ``kill_after`` bytes
      travelled client->server (transient failure; reconnecting works).
    * ``blackout_after``: after this many total client->server bytes, every connection is cut and
      the proxy stops listening (permanent failure; models a dead link).
    """

    def __init__(self, target_port: int, kill_first: int = 0, kill_after: int = 0,
                 blackout_after: Optional[int] = None) -> None:
        self.target = target_port
        self.kill_first, self.kill_after, self.blackout_after = kill_first, kill_after, blackout_after
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.total = 0
        self.n_conns = 0
        self.cut = 0
        self._lock = threading.Lock()
        self._socks = []
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                c, _ = self.listener.accept()
            except OSError:
                return
            with self._lock:
                idx = self.n_conns
                self.n_conns += 1
            try:
                s = socket.create_connection(("127.0.0.1", self.target), timeout=5)
            except OSError:
                c.close()
                continue
            with self._lock:
                self._socks += [c, s]
            threading.Thread(target=self._pump, args=(c, s, idx, True), daemon=True).start()
            threading.Thread(target=self._pump, args=(s, c, idx, False), daemon=True).start()

    def _blackout(self) -> None:
        self._stop = True
        try:
            self.listener.close()
        except OSError:
            pass
        with self._lock:
            socks = list(self._socks)
        for x in socks:
            self._kill(x)

    @staticmethod
    def _kill(x: socket.socket) -> None:
        try:
            x.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        x.close()

    def _pump(self, src: socket.socket, dst: socket.socket, idx: int, upstream: bool) -> None:
        sent = 0
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                if upstream:
                    with self._lock:
                        if self.blackout_after is not None and self.total + len(data) > self.blackout_after:
                            data = None
                        else:
                            self.total += len(data)
                    if data is None:
                        self._blackout()
                        return
                    sent += len(data)
                    if idx < self.kill_first and sent > self.kill_after:
                        with self._lock:
                            self.cut += 1
                        self._kill(src)
                        self._kill(dst)
                        return
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for x in (src, dst):
                try:
                    x.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self._stop = True
        try:
            self.listener.close()
        except OSError:
            pass
        with self._lock:
            socks = list(self._socks)
        for x in socks:
            self._kill(x)
