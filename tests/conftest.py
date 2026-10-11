"""Shared fixtures. Every test uses its own temp directory and a loopback server on a free port."""
from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.config import ClientConfig  # noqa: E402
from client.socket_client import TransferJob  # noqa: E402
from server.config import ServerConfig  # noqa: E402
from server.socket_server import FileTransferServer  # noqa: E402


def make_bytes(size: int, seed: int = 1) -> bytes:
    """Deterministic pseudo-random test data."""
    return random.Random(seed).randbytes(size)


@pytest.fixture
def server(tmp_path):
    cfg = ServerConfig(port=0, data_dir=tmp_path / "srv", min_chunk_size=64, socket_timeout=5.0)
    srv = FileTransferServer(cfg)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def make_file(tmp_path):
    def _make(size: int, name: str = "data.bin", seed: int = 1) -> Path:
        p = tmp_path / "src" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(make_bytes(size, seed))
        return p
    return _make


def client_config(port: int, **kw) -> ClientConfig:
    defaults = dict(connections=4, chunk_size=1024, backoff_base=0.01, backoff_max=0.05,
                    connect_timeout=2.0, socket_timeout=5.0, max_retries=3, max_reconnects=4)
    defaults.update(kw)
    return ClientConfig(port=port, **defaults)


@pytest.fixture
def run_job(server):
    """run_job(path, **cfg) -> (job, result). Extra kwargs: restart, display_name, corrupt_hook, port."""
    def _run(path: Path, restart: bool = False, display_name=None, corrupt_hook=None, port=None, **cfg):
        job = TransferJob(path, client_config(port or server.port, **cfg), restart=restart,
                          display_name=display_name, corrupt_hook=corrupt_hook)
        return job, job.run()
    return _run


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
