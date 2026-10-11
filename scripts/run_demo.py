"""One-command demo, no dashboard needed.

    python scripts/run_demo.py              # send a 24 MB generated file over 4 sockets and verify it
    python scripts/run_demo.py --resume     # also demonstrate interrupt + resume
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.config import ClientConfig  # noqa: E402
from client.socket_client import TransferJob  # noqa: E402
from server.config import ServerConfig  # noqa: E402
from server.socket_server import FileTransferServer  # noqa: E402


def show(job: TransferJob, label: str) -> None:
    while job.status not in ("completed", "incomplete", "failed", "integrity_failed", "cancelled"):
        s = job.snapshot()
        busy = ", ".join(f"#{c['conn_id']}:{c['current_chunk'] if c['current_chunk'] is not None else '-'}" for c in s["connections"])
        print(f"\r{label} {s['status']:<12} {s['chunks']['completed']:>3}/{s['chunks']['total']} chunks  sockets[{busy}]   ", end="", flush=True)
        time.sleep(0.2)
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mb", type=int, default=24)
    ap.add_argument("--connections", type=int, default=4)
    ap.add_argument("--resume", action="store_true", help="interrupt a transfer halfway, then resume it")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="mpft-demo-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "demo.bin"
        src.write_bytes(random.Random(42).randbytes(a.size_mb * 1024 * 1024))
        server = FileTransferServer(ServerConfig(port=0, data_dir=tmp_path / "srv"))
        server.start()
        cfg = ClientConfig(port=server.port, connections=a.connections, chunk_size=1024 * 1024)
        try:
            if a.resume:
                def throttle(cid, attempts, data):          # slows each chunk so there is time to interrupt
                    time.sleep(0.03)
                    return data
                job = TransferJob(src, cfg, corrupt_hook=throttle)
                t = threading.Thread(target=job.run)
                t.start()
                while job.snapshot()["chunks"]["completed"] < a.size_mb // 3:
                    time.sleep(0.02)
                job.cancel()
                t.join()
                held = len(server.manager.sessions[job.transfer_id].chunks)
                print(f"interrupted: receiver holds {held}/{len(job.manifest.chunks)} validated chunks; nothing published")
            job = TransferJob(src, cfg)
            t = threading.Thread(target=job.run)
            t.start()
            show(job, "send ")
            t.join()
            r = job.result
            print(f"status        : {r['status']}")
            if r["resumed_chunks"]:
                print(f"resumed       : {r['resumed_chunks']} chunks already on receiver, sent only {r['bytes_acked']} bytes")
            print(f"original sha  : {r['original_sha256']}\nreceived sha  : {r['received_sha256']}")
            print(f"time {r['elapsed']:.2f}s, average {r['avg_mb_s']:.1f} MB/s, retries {r['retries']}")
            out = server.cfg.received_dir / (r["saved_as"] or "")
            same = r["saved_as"] and hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256(src.read_bytes()).hexdigest()
            print("independent re-hash of the received file matches:", bool(same))
            return 0 if r["status"] == "completed" and same else 1
        finally:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
