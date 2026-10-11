from pathlib import Path

import pytest

from server.chunk_storage import sanitize_filename
from server.config import ConfigError, ServerConfig


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"),
    ("..\\..\\windows\\evil.exe", "evil.exe"),
    ("/abs/path/file.txt", "file.txt"),
    ("C:\\Users\\me\\doc.pdf", "doc.pdf"),
    ("..", "file.bin"),
    ("...", "file.bin"),
    ("", "file.bin"),
    ("a\x00b.txt", "ab.txt"),
    ("na<>me?.txt", "na__me_.txt"),
    ("CON", "_CON"),
    ("normal name.tar.gz", "normal name.tar.gz"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitized_name_is_always_a_plain_basename():
    for raw in ["../x", "a/b/c", "..//..//z", "\\\\server\\share\\f", "x" * 500 + ".bin"]:
        out = sanitize_filename(raw)
        assert Path(out).name == out and "/" not in out and "\\" not in out and 0 < len(out) <= 128


def test_path_traversal_filename_is_confined_to_received_dir(server, make_file, run_job, tmp_path):
    src = make_file(3000)
    _, r = run_job(src, display_name="../../evil.txt")
    assert r["status"] == "completed" and r["saved_as"] == "evil.txt"
    assert (server.cfg.received_dir / "evil.txt").read_bytes() == src.read_bytes()
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()
    # nothing was written anywhere but the data dir
    written = {p.name for p in (tmp_path / "srv").rglob("*") if p.is_file()}
    assert "evil.txt" in written


def test_existing_output_is_never_overwritten(server, make_file, run_job):
    a = make_file(2000, seed=1)
    b = make_file(2000, name="other.bin", seed=2)
    _, r1 = run_job(a, display_name="same.bin")
    _, r2 = run_job(b, display_name="same.bin")
    assert r1["saved_as"] == "same.bin" and r2["saved_as"] != "same.bin"
    assert (server.cfg.received_dir / "same.bin").read_bytes() == a.read_bytes()
    assert (server.cfg.received_dir / r2["saved_as"]).read_bytes() == b.read_bytes()


def test_remote_bind_requires_explicit_opt_in(tmp_path):
    with pytest.raises(ConfigError):
        ServerConfig(host="0.0.0.0", data_dir=tmp_path).validate()
    ServerConfig(host="0.0.0.0", allow_remote=True, data_dir=tmp_path).validate()
    ServerConfig(host="127.0.0.1", data_dir=tmp_path).validate()
    ServerConfig(host="localhost", data_dir=tmp_path).validate()


def test_failed_bind_does_not_leak_a_socket(tmp_path):
    import gc
    import socket
    import warnings
    from server.socket_server import FileTransferServer
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    try:
        srv = FileTransferServer(ServerConfig(port=blocker.getsockname()[1], data_dir=tmp_path))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with pytest.raises(OSError):
                srv.start()
            gc.collect()
    finally:
        blocker.close()
