"""Resume and recovery: persistent manifests, restart, revalidation."""
import json
import threading
import time

from client.socket_client import TransferJob
from server.config import ServerConfig
from server.socket_server import FileTransferServer
from tests.conftest import client_config
from tests.helpers import FaultProxy

CHUNK = 64 * 1024


def interrupted_run(server, src, blackout_fraction=0.4, connections=2):
    """Send through a proxy that dies after a fraction of the bytes -> incomplete transfer."""
    size = src.stat().st_size
    proxy = FaultProxy(server.port, blackout_after=int(size * blackout_fraction))
    job = TransferJob(src, client_config(proxy.port, chunk_size=CHUNK, connections=connections,
                                         max_reconnects=1, connect_timeout=1.0))
    r = job.run()
    proxy.close()
    return r


def test_interrupted_transfer_resumes_without_resending_valid_chunks(server, make_file, run_job):
    src = make_file(2 * 1024 * 1024 + 500)
    first = interrupted_run(server, src)
    assert first["status"] == "incomplete" and first["missing_count"] > 0
    held = len(server.manager.sessions[first["transfer_id"]].chunks)
    assert 0 < held < first["expected_chunks"]
    assert not list(server.cfg.received_dir.iterdir())               # nothing published yet

    job, second = run_job(src, chunk_size=CHUNK, connections=2)
    assert second["status"] == "completed"
    assert second["resumed_chunks"] == held                            # the server told us what it had
    assert second["bytes_acked"] < src.stat().st_size                  # and we sent only the rest
    assert second["bytes_acked"] + second["resumed_bytes"] == src.stat().st_size
    assert (server.cfg.received_dir / second["saved_as"]).read_bytes() == src.read_bytes()
    assert second["transfer_id"] == first["transfer_id"]               # stable ID


def test_manifest_is_persisted_on_disk(server, make_file):
    src = make_file(1024 * 1024)
    r = interrupted_run(server, src)
    manifest = json.loads((server.cfg.manifests_dir / f"{r['transfer_id']}.json").read_text())
    assert manifest["status"] == "receiving" and manifest["file_size"] == src.stat().st_size
    assert len(manifest["chunks"]) == len(server.manager.sessions[r["transfer_id"]].chunks) > 0


def test_server_restart_recovery(tmp_path, make_file):
    src = make_file(2 * 1024 * 1024)
    cfg = ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64, socket_timeout=5)
    srv1 = FileTransferServer(cfg)
    srv1.start()
    first = interrupted_run(srv1, src)
    held = len(srv1.manager.sessions[first["transfer_id"]].chunks)
    srv1.stop()                                                        # server process "dies"

    srv2 = FileTransferServer(ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64, socket_timeout=5))
    srv2.start()
    try:
        assert len(srv2.manager.sessions[first["transfer_id"]].chunks) == held   # state loaded from manifest
        r = TransferJob(src, client_config(srv2.port, chunk_size=CHUNK, connections=3)).run()
        assert r["status"] == "completed" and r["resumed_chunks"] == held and r["checksum_match"]
        assert (srv2.cfg.received_dir / r["saved_as"]).read_bytes() == src.read_bytes()
    finally:
        srv2.stop()


def test_corrupted_staged_chunk_is_revalidated_and_resent(tmp_path, make_file):
    src = make_file(1024 * 1024)
    cfg = ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64, socket_timeout=5)
    srv1 = FileTransferServer(cfg)
    srv1.start()
    first = interrupted_run(srv1, src, blackout_fraction=0.6)
    s1 = srv1.manager.sessions[first["transfer_id"]]
    held = sorted(s1.chunks)
    srv1.stop()
    part = cfg.staging_dir / f"{first['transfer_id']}.part"
    raw = bytearray(part.read_bytes())
    victim = held[0]
    raw[victim * CHUNK + 5] ^= 0xFF                                    # bit-rot on disk
    part.write_bytes(bytes(raw))

    srv2 = FileTransferServer(ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64, socket_timeout=5))
    srv2.start()
    try:
        r = TransferJob(src, client_config(srv2.port, chunk_size=CHUNK, connections=2)).run()
        assert r["status"] == "completed" and r["checksum_match"]
        assert r["resumed_chunks"] == len(held) - 1                    # the damaged chunk was not trusted
        assert (srv2.cfg.received_dir / r["saved_as"]).read_bytes() == src.read_bytes()
    finally:
        srv2.stop()


def test_cancel_keeps_validated_chunks_and_can_resume(server, make_file, run_job):
    src = make_file(1024 * 1024)

    def slow(cid, attempts, data):
        time.sleep(0.02)
        return data
    job = TransferJob(src, client_config(server.port, chunk_size=16 * 1024, connections=2), corrupt_hook=slow)
    t = threading.Thread(target=job.run)
    t.start()
    while job.bytes_acked < 5 * 16 * 1024:
        time.sleep(0.01)
    job.cancel()
    t.join(timeout=10)
    assert job.result["status"] == "cancelled"
    held = len(server.manager.sessions[job.transfer_id].chunks)
    assert held >= 5
    _, r = run_job(src, chunk_size=16 * 1024, connections=2)
    assert r["status"] == "completed" and r["resumed_chunks"] >= held


def test_pause_stops_new_chunks_and_continue_finishes(server, make_file):
    src = make_file(1024 * 1024)

    def slow(cid, attempts, data):
        time.sleep(0.01)
        return data
    job = TransferJob(src, client_config(server.port, chunk_size=16 * 1024, connections=2), corrupt_hook=slow)
    t = threading.Thread(target=job.run)
    t.start()
    while job.bytes_acked < 3 * 16 * 1024:
        time.sleep(0.01)
    assert job.pause() is True
    time.sleep(0.5)                                                    # in-flight chunks settle
    frozen = job.snapshot()["chunks"]["completed"]
    time.sleep(0.5)
    assert job.snapshot()["status"] == "paused"
    assert job.snapshot()["chunks"]["completed"] == frozen < job.snapshot()["chunks"]["total"]
    assert all(c["status"] == "paused" for c in job.snapshot()["connections"])
    assert job.unpause() is True
    t.join(timeout=30)
    assert job.result["status"] == "completed" and job.result["checksum_match"]


def test_completed_transfer_is_recognised_and_restart_resends(server, make_file, run_job):
    src = make_file(50_000)
    _, r1 = run_job(src, chunk_size=4096)
    assert r1["status"] == "completed"
    _, r2 = run_job(src, chunk_size=4096)                              # same file again, no restart
    assert r2["status"] == "completed" and r2["bytes_acked"] == 0 and r2["resumed_chunks"] == r2["expected_chunks"]
    _, r3 = run_job(src, chunk_size=4096, restart=True)                # explicit fresh start
    assert r3["status"] == "completed" and r3["bytes_acked"] == 50_000 and r3["resumed_chunks"] == 0
    assert r3["saved_as"] != r1["saved_as"]


def test_deleted_output_triggers_full_resend(server, make_file, run_job):
    src = make_file(20_000)
    _, r1 = run_job(src, chunk_size=4096)
    (server.cfg.received_dir / r1["saved_as"]).unlink()
    _, r2 = run_job(src, chunk_size=4096)
    assert r2["status"] == "completed" and r2["bytes_acked"] == 20_000


def test_resume_refuses_mismatched_metadata(server, make_file, run_job):
    from common.protocol import MsgType
    from tests.helpers import RawClient, make_offer
    from tests.conftest import make_bytes
    data = make_bytes(1000)
    c = RawClient(server.port)
    c.offer("m.bin", data, 100)
    offer = make_offer("m.bin", data, 100)
    offer["file_size"] = 2000                                           # tamper: ID no longer matches
    offer["total_chunks"] = 20
    c2 = RawClient(server.port)
    t, body = c2.request(MsgType.OFFER, offer)
    assert t == MsgType.ERROR and body["code"] == "BAD_METADATA"
    # a second OFFER on an already-bound connection is a state error
    t, body = c.request(MsgType.OFFER, make_offer("m.bin", data, 100))
    assert t == MsgType.ERROR and body["code"] == "BAD_STATE"
    c.close(); c2.close()


# ---- hard-crash durability: the journal means a killed receiver forgets nothing ------------------
def _spawn_receiver(port, data_dir):
    import socket as _s
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    p = subprocess.Popen([sys.executable, str(root / "scripts" / "run_server.py"), "--port", str(port),
                          "--data-dir", str(data_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            _s.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise AssertionError("receiver did not start")


def _free_port():
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_sigkill_of_receiver_process_loses_no_acknowledged_chunk(tmp_path):
    import signal
    from common.protocol import MsgType
    from tests.conftest import make_bytes
    from tests.helpers import RawClient
    port, data_dir = _free_port(), tmp_path / "srv"
    data, cs = make_bytes(200 * 1024 + 37, seed=3), 1024
    n = (len(data) + cs - 1) // cs
    proc = _spawn_receiver(port, data_dir)
    try:
        c = RawClient(port)
        t, body = c.offer("crash.bin", data, cs)
        sent = [i for i in range(n) if i % 3 != 0]               # every ACK below must survive the kill
        for i in sent:
            assert c.chunk(i, data, cs)[0] == MsgType.ACK
        tid = body["transfer_id"]
        proc.send_signal(signal.SIGKILL)                          # no shutdown hooks, no manifest flush
        proc.wait()
        c.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not list((data_dir / "received").iterdir())
    proc2 = _spawn_receiver(port, data_dir)
    try:
        c2 = RawClient(port)
        t, body = c2.offer("crash.bin", data, cs)
        from common.protocol import decode_ranges
        have = decode_ranges(body["have"], n)
        assert sorted(have) == sent                               # all acknowledged chunks recovered, nothing invented
        for i in range(n):
            if i not in have:
                assert c2.chunk(i, data, cs)[0] == MsgType.ACK
        t, res = c2.finish(tid)
        assert res["status"] == "verified"
        assert (data_dir / "received" / "crash.bin").read_bytes() == data
        c2.close()
    finally:
        proc2.kill()
        proc2.wait()


def test_journal_replay_ignores_torn_and_bogus_lines(tmp_path):
    from server.chunk_storage import ChunkStorage
    from server.models import TransferSession
    cfg = ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64)
    storage = ChunkStorage(cfg)
    tid = "ab" * 16
    s = TransferSession(transfer_id=tid, filename="f.bin", file_size=1000, file_sha256="0" * 64, chunk_size=100,
                        total_chunks=10)
    storage.save_manifest(s)
    good = "1" * 64
    storage.journal_path(tid).write_bytes(
        f"3 {good}\n".encode() + b"garbage line\n" + f"99 {good}\n".encode() + b"4 nothex\n" +
        f"5 {good}\n".encode() + f"7 {good[:20]}".encode())          # last line torn mid-write
    loaded = ChunkStorage(cfg).load_all()
    assert sorted(loaded[0].chunks) == [3, 5]


def test_journal_is_removed_once_a_manifest_snapshot_supersedes_it(server, make_file, run_job):
    src = make_file(5000)
    _, r = run_job(src, chunk_size=1024)
    assert r["status"] == "completed"
    assert not list(server.cfg.manifests_dir.glob("*.journal"))
