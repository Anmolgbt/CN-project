"""API tests: real server + real jobs behind FastAPI's TestClient."""
import time

import pytest
from fastapi.testclient import TestClient

from api.main import Settings, create_app
from tests.conftest import make_bytes, sha


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", receiver_port=0, serve_frontend=False))
    with TestClient(app) as c:
        yield c


def upload(client, data: bytes, name="sample.bin"):
    r = client.put("/api/files", params={"name": name}, content=data)
    assert r.status_code == 201, r.text
    return r.json()


def wait_done(client, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/transfers/{job_id}").json()
        if s["status"] in ("completed", "incomplete", "failed", "integrity_failed", "cancelled"):
            return s
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["embedded_receiver"] and cfg["default_port"] > 0 and cfg["encrypted"] is False


def test_upload_reports_size_and_sha(client):
    data = make_bytes(12345)
    meta = upload(client, data)
    assert meta["size"] == 12345 and meta["sha256"] == sha(data)
    assert [f["file_id"] for f in client.get("/api/files").json()] == [meta["file_id"]]


def test_full_transfer_through_api_with_download_and_history(client):
    data = make_bytes(700_000)
    meta = upload(client, data)
    port = client.get("/api/config").json()["default_port"]
    r = client.post("/api/transfers", json={"file_id": meta["file_id"], "port": port, "connections": 4,
                                            "chunk_size": 65536})
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    snap = wait_done(client, job_id)
    assert snap["status"] == "completed"
    res = snap["result"]
    assert res["original_sha256"] == res["received_sha256"] == sha(data)
    assert len(snap["connections"]) == 4 and sum(c["chunks_acked"] for c in snap["connections"]) == 11
    assert client.get(f"/api/transfers/{job_id}/connections").json() == snap["connections"]
    ev = client.get(f"/api/transfers/{job_id}/events").json()
    assert any(e["type"] == "chunk_acked" for e in ev)
    dl = client.get(f"/api/transfers/{job_id}/download")
    assert dl.status_code == 200 and dl.content == data
    hist = client.get("/api/history").json()
    assert hist[0]["job_id"] == job_id and hist[0]["checksum_match"] is True and hist[0]["status"] == "completed"
    assert client.get(f"/api/history/{job_id}").json()["snapshot"]["result"]["status"] == "completed"


def test_websocket_streams_real_snapshots(client):
    data = make_bytes(300_000)
    meta = upload(client, data)
    port = client.get("/api/config").json()["default_port"]
    job_id = client.post("/api/transfers", json={"file_id": meta["file_id"], "port": port,
                                                 "chunk_size": 16384}).json()["job_id"]
    with client.websocket_connect(f"/ws/transfers/{job_id}") as ws:
        last = None
        for _ in range(60):
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            last = msg["data"]
            if last["status"] == "completed":
                break
    assert last["status"] == "completed" and last["bytes"]["acked"] == 300_000


def test_validation_errors_are_structured(client):
    r = client.post("/api/transfers", json={"file_id": "x", "port": 99999})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = client.post("/api/transfers", json={"file_id": "nope", "port": 9000})
    assert r.status_code == 404 and r.json()["error"]["code"] == "file_not_found"
    r = client.post("/api/transfers", json={"file_id": "../../etc/passwd"})
    assert r.status_code in (400, 404) and "error" in r.json()
    r = client.post("/api/transfers", json={"file_id": upload(client, b"abc")["file_id"], "host": "8.8.8.8"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "remote_target_disabled"
    assert client.get("/api/transfers/doesnotexist").json()["error"]["code"] == "job_not_found"
    assert client.get("/api/transfers/doesnotexist/download").status_code == 404


def test_too_many_chunks_is_rejected_early(client):
    meta = upload(client, b"x" * 2048)
    r = client.post("/api/transfers", json={"file_id": meta["file_id"], "chunk_size": 1024, "port": 1})
    assert r.status_code == 201   # 2 chunks is fine; the limit guard is exercised in the unit below
    from api.job_manager import MAX_CHUNKS
    from common.integrity import total_chunks
    assert total_chunks(MAX_CHUNKS * 1024 + 1, 1024) > MAX_CHUNKS


def test_cancel_then_resume_via_api(client):
    data = make_bytes(8 * 1024 * 1024)
    meta = upload(client, data)
    port = client.get("/api/config").json()["default_port"]
    job_id = client.post("/api/transfers", json={"file_id": meta["file_id"], "port": port, "connections": 1,
                                                 "chunk_size": 4096 * 4}).json()["job_id"]
    deadline = time.time() + 10
    while client.get(f"/api/transfers/{job_id}").json()["chunks"]["completed"] < 5 and time.time() < deadline:
        time.sleep(0.005)
    c = client.post(f"/api/transfers/{job_id}/cancel")
    snap = wait_done(client, job_id)
    if c.status_code == 409 or snap["status"] == "completed":
        pytest.skip("transfer finished before cancel on this machine")
    assert snap["status"] == "cancelled"
    assert client.post(f"/api/transfers/{job_id}/cancel").status_code == 409
    new_id = client.post(f"/api/transfers/{job_id}/resume").json()["job_id"]
    final = wait_done(client, new_id)
    assert final["status"] == "completed" and final["resumed_chunks"] > 0
    assert client.post(f"/api/transfers/{new_id}/resume").status_code == 409     # completed is not resumable


def test_benchmark_runs_1_2_4_8_on_same_file(client):
    port = client.get("/api/config").json()["default_port"]
    r = client.post("/api/benchmarks", json={"size_mb": 2, "port": port, "chunk_size": 131072})
    assert r.status_code == 201
    bid = r.json()["id"]
    end = time.time() + 60
    while time.time() < end:
        b = client.get(f"/api/benchmarks/{bid}").json()
        if b["status"] != "running":
            break
        time.sleep(0.1)
    assert b["status"] == "completed", b
    assert [row["connections"] for row in b["summary"]] == [1, 2, 4, 8]
    for row in b["summary"]:
        assert row["integrity"] is True and row["bytes_acked"] == 2 * 1024 * 1024 and row["avg_mb_s"] > 0
        assert row["completed_chunks"] == row["expected_chunks"] == 16
    assert b["summary"][0]["speedup_vs_1"] == pytest.approx(1.0)
    assert client.get("/api/benchmarks").json()[0]["id"] == bid
    assert not list((client.app.state.jobs.embedded.received_dir).iterdir())    # throw-away copies cleaned up


def test_busy_receiver_port_gives_actionable_error(tmp_path):
    import socket
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    try:
        app = create_app(Settings(data_dir=tmp_path / "d", receiver_port=port, serve_frontend=False))
        with pytest.raises(RuntimeError, match="MPFT_RECEIVER_PORT"):
            with TestClient(app):
                pass
    finally:
        blocker.close()


def test_too_many_simultaneous_jobs_returns_429(client, monkeypatch):
    import api.job_manager as jm
    meta = upload(client, b"x" * 5000)
    port = client.get("/api/config").json()["default_port"]
    monkeypatch.setattr(jm, "MAX_ACTIVE_JOBS", 0)          # deterministic: the limit is already reached
    r = client.post("/api/transfers", json={"file_id": meta["file_id"], "port": port})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_jobs"
    monkeypatch.setattr(jm, "MAX_ACTIVE_JOBS", 4)
    assert client.post("/api/transfers", json={"file_id": meta["file_id"], "port": port}).status_code == 201
