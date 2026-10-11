"""MPFT wire protocol, version 1 -- the single definition used by client AND server.

Every message on a TCP connection is one *frame*::

    +-------+---------+------+----------+-------------+
    | magic | version | type | meta_len | payload_len |   12-byte header, big endian
    | 2 B   | 1 B     | 1 B  | 4 B      | 4 B         |   struct "!2sBBII"
    +-------+---------+------+----------+-------------+
    | meta  (meta_len bytes)                           |
    | payload (payload_len bytes, CHUNK frames only)   |
    +--------------------------------------------------+

* Control frames: ``meta`` is a UTF-8 JSON object, ``payload_len`` is 0.
* CHUNK frames: ``meta`` is a fixed 48-byte binary struct
  ``!IQI32s`` = chunk_id, byte offset, payload length, SHA-256 of payload;
  the payload is the raw chunk bytes.

TCP is a byte stream, so frames are read with :func:`recv_exact`, which loops
until the requested number of bytes has arrived. All lengths are validated
against hard limits *before* any buffer is allocated.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable, List, Tuple

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS: Tuple[int, ...] = (1,)
MAGIC = b"MF"
HEADER = struct.Struct("!2sBBII")
CHUNK_META = struct.Struct("!IQI32s")

MAX_META_BYTES = 1024 * 1024          # JSON control messages
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024  # absolute protocol ceiling for one chunk
MAX_CHUNKS = 65_536                   # keeps manifests and "have" lists bounded

TRANSFER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class MsgType(IntEnum):
    """Message types carried in the frame header."""

    HELLO = 1        # client -> server: supported versions
    HELLO_OK = 2     # server -> client: chosen version + limits
    OFFER = 3        # client -> server: file metadata, creates/resumes a session
    OFFER_OK = 4     # server -> client: session state + chunks already held
    ATTACH = 5       # client -> server: join an existing session (extra socket)
    ATTACH_OK = 6
    CHUNK = 7        # client -> server: one chunk
    ACK = 8          # server -> client: chunk stored (or already held)
    NACK = 9         # server -> client: chunk rejected, with an error code
    FINISH = 10      # client -> server: "verify and publish the file"
    COMPLETE = 11    # server -> client: final status
    ERROR = 12       # server -> client: connection-level failure


# Error codes used in NACK / ERROR frames.
E_BAD_FRAME = "BAD_FRAME"
E_UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
E_BAD_METADATA = "BAD_METADATA"
E_UNKNOWN_TRANSFER = "UNKNOWN_TRANSFER"
E_LIMIT = "LIMIT_EXCEEDED"
E_BAD_STATE = "BAD_STATE"
E_BAD_CHUNK = "BAD_CHUNK"
E_CHECKSUM = "CHECKSUM_MISMATCH"
E_DUP_MISMATCH = "DUPLICATE_MISMATCH"
E_INTERNAL = "INTERNAL"

RETRYABLE_NACKS = frozenset({E_CHECKSUM, E_INTERNAL})


class ProtocolError(Exception):
    """The peer violated the protocol (bad framing, bad metadata, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConnectionClosed(Exception):
    """The peer closed the TCP connection (possibly mid-frame)."""


@dataclass
class Frame:
    """One decoded frame."""

    msg_type: MsgType
    version: int
    meta: bytes
    payload: bytes

    def json(self) -> dict:
        """Decode ``meta`` as a JSON object."""
        try:
            obj = json.loads(self.meta.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProtocolError(E_BAD_FRAME, "control message is not valid JSON") from exc
        if not isinstance(obj, dict):
            raise ProtocolError(E_BAD_FRAME, "control message must be a JSON object")
        return obj


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes; ``recv()`` may return fewer, so loop."""
    if n < 0:
        raise ValueError("n must not be negative")
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        read = sock.recv_into(view[got:], n - got)
        if read == 0:
            raise ConnectionClosed(f"peer closed after {got} of {n} bytes")
        got += read
    return bytes(buf)


def send_frame(sock: socket.socket, msg_type: MsgType, meta: bytes = b"", payload: bytes = b"") -> None:
    """Encode and send one frame."""
    if len(meta) > MAX_META_BYTES or len(payload) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(E_LIMIT, "frame exceeds protocol limits")
    sock.sendall(HEADER.pack(MAGIC, PROTOCOL_VERSION, int(msg_type), len(meta), len(payload)) + meta)
    if payload:
        sock.sendall(payload)


def send_json(sock: socket.socket, msg_type: MsgType, obj: dict) -> None:
    """Send a control frame whose meta is a JSON object."""
    send_frame(sock, msg_type, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def read_frame(sock: socket.socket, max_payload: int = MAX_PAYLOAD_BYTES) -> Frame:
    """Read and validate one frame. Raises ProtocolError on malformed input."""
    magic, version, raw_type, meta_len, payload_len = HEADER.unpack(recv_exact(sock, HEADER.size))
    if magic != MAGIC:
        raise ProtocolError(E_BAD_FRAME, "bad magic bytes")
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(E_UNSUPPORTED_VERSION, f"unsupported protocol version {version}")
    try:
        msg_type = MsgType(raw_type)
    except ValueError:
        raise ProtocolError(E_BAD_FRAME, f"unknown message type {raw_type}") from None
    if msg_type == MsgType.CHUNK:
        if meta_len != CHUNK_META.size:
            raise ProtocolError(E_BAD_FRAME, "CHUNK meta must be exactly 48 bytes")
        if payload_len > min(max_payload, MAX_PAYLOAD_BYTES):
            raise ProtocolError(E_LIMIT, f"payload of {payload_len} bytes exceeds limit")
    else:
        if meta_len > MAX_META_BYTES:
            raise ProtocolError(E_LIMIT, f"control message of {meta_len} bytes exceeds limit")
        if payload_len != 0:
            raise ProtocolError(E_BAD_FRAME, "only CHUNK frames may carry a payload")
    meta = recv_exact(sock, meta_len) if meta_len else b""
    payload = recv_exact(sock, payload_len) if payload_len else b""
    return Frame(msg_type, version, meta, payload)


def send_chunk(sock: socket.socket, chunk_id: int, offset: int, data: bytes, digest: bytes) -> None:
    """Send a CHUNK frame. ``digest`` is the raw SHA-256 of the *intended* chunk."""
    send_frame(sock, MsgType.CHUNK, CHUNK_META.pack(chunk_id, offset, len(data), digest), data)


def parse_chunk_meta(meta: bytes) -> Tuple[int, int, int, bytes]:
    """Return (chunk_id, offset, length, sha256_digest) from a CHUNK meta block."""
    return CHUNK_META.unpack(meta)


def derive_transfer_id(filename: str, file_size: int, file_sha256: str, chunk_size: int) -> str:
    """Stable 32-hex transfer ID: same file + name + chunking => same ID (enables resume)."""
    blob = json.dumps([filename, file_size, file_sha256, chunk_size], separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def encode_ranges(ids: Iterable[int]) -> List[List[int]]:
    """Compress sorted chunk IDs to inclusive [start, end] ranges."""
    out: List[List[int]] = []
    for i in sorted(ids):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def decode_ranges(ranges: Any, limit: int) -> List[int]:
    """Expand ranges back to IDs, validating shape and that all IDs are < ``limit``."""
    ids: List[int] = []
    if not isinstance(ranges, list):
        raise ProtocolError(E_BAD_METADATA, "ranges must be a list")
    for r in ranges:
        if (not isinstance(r, list) or len(r) != 2 or not all(isinstance(x, int) and not isinstance(x, bool) for x in r)
                or r[0] < 0 or r[1] < r[0] or r[1] >= limit):
            raise ProtocolError(E_BAD_METADATA, "invalid chunk range")
        ids.extend(range(r[0], r[1] + 1))
    return ids
