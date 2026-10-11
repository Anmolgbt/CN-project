import hashlib
import threading
import time

import pytest

from common.protocol import MsgType
from tests.conftest import make_bytes, sha
from tests.helpers import RawClient


def assert_verified(server, src, result):
    data = src.read_bytes()
    assert result["status"] == "completed", result
    assert result["original_sha256"] == result["received_sha256"] == sha(data)
    assert result["checksum_match"] is True
    out = server.cfg.received_dir / result["saved_as"]
    assert out.read_bytes() == data
    assert result["validated_chunks"] == result["expected_chunks"] and result["missing_count"] == 0


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 10 * 1024, 10 * 1024 + 7],
                         ids=["empty", "1byte", "lt-chunk", "exact-chunk", "chunk+1", "10chunks", "10chunks+tail"])
def test_file_sizes(server, make_file, run_job, size):
    src = make_file(size)
    job, r = run_job(src, chunk_size=1024, connections=4)
    assert_verified(server, src, r)
    assert r["final_size"] == size


@pytest.mark.parametrize("n", [1, 2, 4, 8])
def test_multiple_concurrent_connections(server, make_file, run_job, n):
    src = make_file(300_000)       # 74 chunks of 4 KiB
    job, r = run_job(src, chunk_size=4096, connections=n)
    assert_verified(server, src, r)
    assert r["connections_used"] == n and len(job.conn) == n
    session = server.manager.sessions[r["transfer_id"]]
    # n sockets really were attached at once (+1 if the short FINISH socket overlapped a closing worker)
    assert n <= session.peak_connections <= n + 1
    assert sum(c.chunks_acked for c in job.conn.values()) == r["expected_chunks"]
    assert sum(c.bytes_acked for c in job.conn.values()) == 300_000


def test_connections_are_capped_by_chunk_count(server, make_file, run_job):
    src = make_file(2048)
    job, r = run_job(src, chunk_size=1024, connections=8)
    assert_verified(server, src, r)
    assert r["connections_used"] == 2


@pytest.mark.slow
def test_large_file(server, make_file, run_job):
    src = make_file(12 * 1024 * 1024 + 123)
    _, r = run_job(src, chunk_size=256 * 1024, connections=4)
    assert_verified(server, src, r)


def test_throughput_is_bytes_over_time(server, make_file, run_job):
    src = make_file(500_000)
    _, r = run_job(src, chunk_size=8192, connections=2)
    assert r["bytes_acked"] == 500_000
    assert r["avg_mb_s"] == pytest.approx(500_000 / r["data_seconds"] / 1e6, rel=1e-6)
    assert r["peak_mb_s"] >= r["avg_mb_s"] > 0        # peak is never below the average


# ---- receiver behaviour driven by a hand-written client ------------------------------------
CS = 100


def setup_raw(server, size=1050, name="raw.bin"):
    data = make_bytes(size, seed=7)
    c = RawClient(server.port)
    t, body = c.offer(name, data, CS)
    assert t == MsgType.OFFER_OK and body["have"] == []
    return c, data, body


def test_out_of_order_arrival_reconstructs_in_order(server):
    c, data, body = setup_raw(server)
    n = (len(data) + CS - 1) // CS
    order = list(range(n))[::-1]
    order = order[1::2] + order[0::2]          # scrambled
    for cid in order:
        t, r = c.chunk(cid, data, CS)
        assert t == MsgType.ACK and r["duplicate"] is False
    t, res = c.finish(body["transfer_id"])
    assert t == MsgType.COMPLETE and res["status"] == "verified"
    assert (server.cfg.received_dir / "raw.bin").read_bytes() == data
    c.close()


def test_duplicate_chunk_is_idempotent(server):
    c, data, body = setup_raw(server)
    n = (len(data) + CS - 1) // CS
    assert c.chunk(3, data, CS)[1]["duplicate"] is False
    t, r = c.chunk(3, data, CS)
    assert t == MsgType.ACK and r["duplicate"] is True
    for cid in range(n):
        if cid != 3:
            c.chunk(cid, data, CS)
    c.chunk(3, data, CS)                         # and again after everything else
    t, res = c.finish(body["transfer_id"])
    assert res["status"] == "verified"
    assert (server.cfg.received_dir / "raw.bin").read_bytes() == data
    assert server.manager.sessions[body["transfer_id"]].duplicates >= 2
    c.close()


def test_corrupted_payload_is_nacked_and_not_stored(server):
    c, data, body = setup_raw(server)
    bad = bytearray(data[:CS])
    bad[10] ^= 0xFF
    t, r = c.chunk(0, data, CS, payload=bytes(bad))
    assert t == MsgType.NACK and r["code"] == "CHECKSUM_MISMATCH"
    assert 0 not in server.manager.sessions[body["transfer_id"]].chunks
    t, r = c.chunk(0, data, CS)                   # a clean retry succeeds
    assert t == MsgType.ACK
    c.close()


def test_wrong_claimed_checksum_is_nacked(server):
    c, data, body = setup_raw(server)
    t, r = c.chunk(1, data, CS, digest=b"\x00" * 32)
    assert t == MsgType.NACK and r["code"] == "CHECKSUM_MISMATCH"
    c.close()


@pytest.mark.parametrize("kw", [
    dict(offset=5), dict(offset=10 ** 12), dict(payload=b"short"), dict(payload=b"x" * (CS + 1)),
], ids=["bad-offset", "huge-offset", "short-payload", "long-payload"])
def test_invalid_chunk_boundaries_are_nacked(server, kw):
    c, data, body = setup_raw(server)
    t, r = c.chunk(2, data, CS, **kw)
    assert t == MsgType.NACK and r["code"] in ("BAD_CHUNK", "CHECKSUM_MISMATCH")
    assert not server.manager.sessions[body["transfer_id"]].chunks
    c.close()


def test_chunk_id_out_of_range_is_nacked(server):
    c, data, body = setup_raw(server)
    t, r = c.chunk(99, data, CS, payload=b"x" * CS, offset=99 * CS)
    assert t == MsgType.NACK and r["code"] == "BAD_CHUNK"
    c.close()


def test_short_last_chunk_must_have_exact_length(server):
    c, data, body = setup_raw(server)             # 1050 bytes -> last chunk (id 10) is 50 bytes
    t, r = c.chunk(10, data, CS, payload=b"x" * CS, offset=10 * CS)
    assert t == MsgType.NACK and r["code"] == "BAD_CHUNK"
    assert c.chunk(10, data, CS)[0] == MsgType.ACK
    c.close()


def test_missing_chunk_detection_never_reports_success(server):
    c, data, body = setup_raw(server)
    n = (len(data) + CS - 1) // CS
    for cid in range(n):
        if cid not in (4, 9):
            c.chunk(cid, data, CS)
    t, res = c.finish(body["transfer_id"])
    assert res["status"] == "incomplete" and res["missing_chunks"] == [4, 9]
    assert res["missing_count"] == 2 and res["saved_as"] is None
    assert not list(server.cfg.received_dir.iterdir())          # nothing published
    c.close()


def test_final_checksum_mismatch_is_reported_and_nothing_is_published(server):
    data = make_bytes(1050, seed=7)
    c = RawClient(server.port)
    wrong = hashlib.sha256(b"not the file").hexdigest()
    t, body = c.offer("liar.bin", data, CS, sha=wrong)       # sender claims a different whole-file hash
    n = (len(data) + CS - 1) // CS
    for cid in range(n):
        assert c.chunk(cid, data, CS)[0] == MsgType.ACK       # every chunk is individually valid
    t, res = c.finish(body["transfer_id"])
    assert res["status"] == "integrity_failed"
    assert res["received_sha256"] == hashlib.sha256(data).hexdigest() != res["original_sha256"]
    assert not list(server.cfg.received_dir.iterdir())
    c.close()


def test_progress_snapshot_reports_real_numbers(server, make_file):
    from client.socket_client import TransferJob
    from tests.conftest import client_config
    src = make_file(200_000)
    seen = []

    def slow(cid, attempts, data):
        time.sleep(0.01)
        return data
    job = TransferJob(src, client_config(server.port, chunk_size=4096, connections=2), corrupt_hook=slow)
    t = threading.Thread(target=job.run)
    t.start()
    while t.is_alive():
        seen.append(job.snapshot())
        time.sleep(0.02)
    t.join()
    done = [s["bytes"]["acked"] for s in seen]
    assert done == sorted(done) and 0 < max(done) <= 200_000
    assert any(s["status"] == "transferring" and s["throughput"]["current"] > 0 for s in seen)
    assert job.snapshot()["chunks"]["completed"] == job.snapshot()["chunks"]["total"]
