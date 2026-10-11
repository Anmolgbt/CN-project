"""Interrupted connections, retries, corruption handling."""
import socket

from client.socket_client import TransferJob
from tests.conftest import client_config
from tests.helpers import FaultProxy

CHUNK = 32 * 1024


def test_dropped_connections_are_retried_and_transfer_still_verifies(server, make_file):
    src = make_file(1_500_000)
    proxy = FaultProxy(server.port, kill_first=2, kill_after=120_000)   # first two sockets die mid-transfer
    try:
        job = TransferJob(src, client_config(proxy.port, chunk_size=CHUNK, connections=3))
        r = job.run()
    finally:
        proxy.close()
    assert proxy.cut == 2
    assert r["status"] == "completed" and r["checksum_match"] is True
    assert r["retries"] >= 1                                            # unfinished chunks were re-sent
    assert sum(c.reconnects for c in job.conn.values()) >= 2
    assert (server.cfg.received_dir / r["saved_as"]).read_bytes() == src.read_bytes()
    assert any(e["type"] == "conn_error" for e in job.events)


def test_total_link_failure_ends_incomplete_not_success(server, make_file):
    src = make_file(1_000_000)
    proxy = FaultProxy(server.port, blackout_after=300_000)
    try:
        r = TransferJob(src, client_config(proxy.port, chunk_size=CHUNK, connections=2, max_reconnects=1,
                                           connect_timeout=1.0)).run()
    finally:
        proxy.close()
    assert r["status"] == "incomplete" and r["checksum_match"] is None
    assert r["saved_as"] is None and not list(server.cfg.received_dir.iterdir())


def test_corrupted_payloads_are_retried_until_clean(server, make_file, run_job):
    src = make_file(20_000)

    def corrupt_first_attempt(cid, attempts, data):
        if cid in (2, 5) and attempts == 0:
            return bytes([data[0] ^ 1]) + data[1:]
        return data
    job, r = run_job(src, chunk_size=1024, connections=3, corrupt_hook=corrupt_first_attempt)
    assert r["status"] == "completed" and r["checksum_match"] and r["retries"] >= 2
    assert server.manager.sessions[r["transfer_id"]].rejected >= 2
    assert any(e["type"] == "chunk_nack" for e in job.events)


def test_retry_limit_stops_retrying_and_reports_incomplete(server, make_file, run_job):
    src = make_file(10_000)
    job, r = run_job(src, chunk_size=1024, connections=2, max_retries=2,
                     corrupt_hook=lambda cid, a, d: bytes([d[0] ^ 1]) + d[1:] if cid == 3 else d)
    assert r["status"] == "incomplete" and r["failed_chunks"] == 1
    assert job.scheduler.attempts[3] == 3                               # 1 try + 2 retries, then gave up
    assert not list(server.cfg.received_dir.iterdir())
    _, r2 = run_job(src, chunk_size=1024, connections=2)                # healthy retry: only chunk 3 is missing
    assert r2["status"] == "completed" and r2["bytes_acked"] <= 2048


def test_unreachable_server_fails_cleanly(make_file):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = TransferJob(make_file(1000), client_config(port, connect_timeout=0.5)).run()
    assert r["status"] == "failed" and "cannot start" in r["error"]


def test_missing_source_file_fails_cleanly(server, tmp_path):
    r = TransferJob(tmp_path / "nope.bin", client_config(server.port)).run()
    assert r["status"] == "failed"


def test_scheduler_never_hands_out_a_chunk_twice():
    import threading
    from client.models import ChunkInfo
    from client.scheduler import ChunkScheduler
    chunks = [ChunkInfo(i, i * 10, 10, "00" * 32) for i in range(500)]
    sched = ChunkScheduler(chunks, set(), 3)
    got, lock = [], threading.Lock()

    def worker(cid):
        while True:
            c = sched.claim(cid, lambda: False)
            if c is None:
                return
            with lock:
                got.append(c.chunk_id)
            sched.complete(c.chunk_id)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(got) == list(range(500))


def test_scheduler_requeues_released_chunks_and_enforces_retry_budget():
    from client.models import ChunkInfo, FAILED, COMPLETED
    from client.scheduler import ChunkScheduler
    chunks = [ChunkInfo(i, i, 1, "00" * 32) for i in range(2)]
    s = ChunkScheduler(chunks, {1}, max_retries=1)
    c = s.claim(0, lambda: False)
    assert c.chunk_id == 0 and s.owner == {0: 0}
    assert s.release(0, penalize=True) is True and s.claim(1, lambda: False).chunk_id == 0
    assert s.release(0, penalize=True) is False and s.state[0] == FAILED
    assert s.claim(0, lambda: False) is None and s.state[1] == COMPLETED
