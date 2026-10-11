import socket
import threading

import pytest

from common.protocol import (HEADER, MAGIC, MAX_PAYLOAD_BYTES, ConnectionClosed, MsgType, ProtocolError,
                             decode_ranges, derive_transfer_id, encode_ranges, parse_chunk_meta, read_frame,
                             recv_exact, send_chunk, send_frame, send_json)
from tests.helpers import RawClient, make_offer
from tests.conftest import make_bytes


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    a.settimeout(3)
    b.settimeout(3)
    yield a, b
    a.close()
    b.close()


def test_control_frame_roundtrip(pair):
    a, b = pair
    send_json(a, MsgType.HELLO, {"versions": [1]})
    f = read_frame(b)
    assert f.msg_type == MsgType.HELLO and f.json() == {"versions": [1]}


def test_chunk_frame_roundtrip(pair):
    a, b = pair
    send_chunk(a, 7, 7168, b"hello", b"\x01" * 32)
    f = read_frame(b)
    assert f.msg_type == MsgType.CHUNK and f.payload == b"hello"
    assert parse_chunk_meta(f.meta) == (7, 7168, 5, b"\x01" * 32)


def test_recv_exact_reassembles_fragmented_stream(pair):
    a, b = pair
    msg = bytes(range(200))

    def dribble():
        for i in range(0, len(msg), 3):       # 3 bytes per send(): recv() would return partial data
            a.sendall(msg[i:i + 3])
    t = threading.Thread(target=dribble)
    t.start()
    assert recv_exact(b, len(msg)) == msg
    t.join()


def test_frame_split_across_many_tcp_segments(pair):
    a, b = pair
    send_json(a, MsgType.OFFER, {"x": 1})   # reference encoding
    raw = b.recv(4096)
    c, d = socket.socketpair()
    try:
        threading.Thread(target=lambda: [c.sendall(raw[i:i + 1]) for i in range(len(raw))]).start()
        d.settimeout(3)
        assert read_frame(d).json() == {"x": 1}
    finally:
        c.close()
        d.close()


def test_connection_closed_mid_frame(pair):
    a, b = pair
    a.sendall(HEADER.pack(MAGIC, 1, int(MsgType.HELLO), 50, 0) + b"short")
    a.close()
    with pytest.raises(ConnectionClosed):
        read_frame(b)


def test_bad_magic_rejected(pair):
    a, b = pair
    a.sendall(b"XX" + b"\x00" * 10)
    with pytest.raises(ProtocolError) as e:
        read_frame(b)
    assert e.value.code == "BAD_FRAME"


def test_unsupported_version_and_unknown_type(pair):
    a, b = pair
    a.sendall(HEADER.pack(MAGIC, 9, int(MsgType.HELLO), 0, 0))
    with pytest.raises(ProtocolError) as e:
        read_frame(b)
    assert e.value.code == "UNSUPPORTED_VERSION"
    a.sendall(HEADER.pack(MAGIC, 1, 99, 0, 0))
    with pytest.raises(ProtocolError):
        read_frame(b)


def test_oversized_lengths_rejected_before_reading_body(pair):
    a, b = pair
    a.sendall(HEADER.pack(MAGIC, 1, int(MsgType.CHUNK), 48, MAX_PAYLOAD_BYTES + 1))   # no body follows
    with pytest.raises(ProtocolError) as e:
        read_frame(b)
    assert e.value.code == "LIMIT_EXCEEDED"
    a.sendall(HEADER.pack(MAGIC, 1, int(MsgType.OFFER), 2 ** 31, 0))
    with pytest.raises(ProtocolError):
        read_frame(b)


def test_payload_only_allowed_on_chunk_frames_and_meta_size_checked(pair):
    a, b = pair
    a.sendall(HEADER.pack(MAGIC, 1, int(MsgType.FINISH), 0, 4))
    with pytest.raises(ProtocolError):
        read_frame(b)
    a.sendall(HEADER.pack(MAGIC, 1, int(MsgType.CHUNK), 10, 0))
    with pytest.raises(ProtocolError):
        read_frame(b)


def test_json_must_be_an_object(pair):
    a, b = pair
    send_frame(a, MsgType.OFFER, b"[1,2,3]")
    with pytest.raises(ProtocolError):
        read_frame(b).json()
    send_frame(a, MsgType.OFFER, b"\xff\xfe")
    with pytest.raises(ProtocolError):
        read_frame(b).json()


def test_ranges_roundtrip_and_validation():
    ids = [0, 1, 2, 5, 7, 8]
    assert encode_ranges(ids) == [[0, 2], [5, 5], [7, 8]]
    assert decode_ranges(encode_ranges(ids), 10) == ids
    for bad in ([[3, 2]], [[0, 99]], [[-1, 1]], "x", [[0]], [[True, 1]]):
        with pytest.raises(ProtocolError):
            decode_ranges(bad, 10)


def test_transfer_id_is_stable_and_sensitive():
    a = derive_transfer_id("f", 10, "a" * 64, 4)
    assert a == derive_transfer_id("f", 10, "a" * 64, 4) and len(a) == 32
    assert a != derive_transfer_id("f", 10, "a" * 64, 5)
    assert a != derive_transfer_id("g", 10, "a" * 64, 4)


# ---- server-side validation of malformed traffic -------------------------------------------
def raw_error(port, payload: bytes):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(payload)
    try:
        f = read_frame(s)
        return f.msg_type, f.json()
    finally:
        s.close()


def test_server_answers_garbage_with_error(server):
    t, body = raw_error(server.port, b"THIS IS NOT THE PROTOCOL")
    assert t == MsgType.ERROR and body["code"] == "BAD_FRAME"


def test_server_rejects_unsupported_version(server):
    t, body = raw_error(server.port, HEADER.pack(MAGIC, 7, int(MsgType.HELLO), 0, 0))
    assert t == MsgType.ERROR and body["code"] == "UNSUPPORTED_VERSION"


def test_server_requires_hello_first(server):
    t, body = raw_error(server.port, HEADER.pack(MAGIC, 1, int(MsgType.FINISH), 0, 0))
    assert t == MsgType.ERROR and body["code"] == "BAD_FRAME"


def test_server_rejects_oversized_payload_header(server):
    c = RawClient(server.port)
    c.sock.sendall(HEADER.pack(MAGIC, 1, int(MsgType.CHUNK), 48, 2 ** 31 - 1))
    f = read_frame(c.sock)
    assert f.msg_type == MsgType.ERROR and f.json()["code"] == "LIMIT_EXCEEDED"
    c.close()


def test_chunk_before_offer_is_a_state_error(server):
    c = RawClient(server.port)
    send_chunk(c.sock, 0, 0, b"x", b"\x00" * 32)
    f = read_frame(c.sock)
    assert f.msg_type == MsgType.ERROR and f.json()["code"] == "BAD_STATE"
    c.close()


@pytest.mark.parametrize("mutate,label", [
    (lambda o: o.update(file_size=-1), "negative size"),
    (lambda o: o.update(file_size=10 ** 15), "huge size"),
    (lambda o: o.update(chunk_size=10 ** 9), "huge chunk"),
    (lambda o: o.update(chunk_size=1), "tiny chunk"),
    (lambda o: o.update(file_sha256="zz"), "bad hash"),
    (lambda o: o.update(total_chunks=999), "chunk count mismatch"),
    (lambda o: o.update(transfer_id="0" * 32), "forged transfer id"),
    (lambda o: o.update(filename=""), "empty name"),
    (lambda o: o.update(filename=123), "non-string name"),
    (lambda o: o.update(file_size=True), "bool size"),
])
def test_invalid_offer_metadata_rejected(server, mutate, label):
    c = RawClient(server.port)
    offer = make_offer("f.bin", make_bytes(500), 100)
    mutate(offer)
    t, body = c.request(MsgType.OFFER, offer)
    assert t == MsgType.ERROR and body["code"] == "BAD_METADATA", label
    c.close()


def test_attach_to_unknown_transfer(server):
    c = RawClient(server.port)
    t, body = c.request(MsgType.ATTACH, {"transfer_id": "f" * 32})
    assert t == MsgType.ERROR and body["code"] == "UNKNOWN_TRANSFER"
    c.close()
