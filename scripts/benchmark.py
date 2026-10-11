"""Reproducible local benchmark: same file + same chunk size over 1, 2, 4 and 8 TCP connections.

    python scripts/benchmark.py --size-mb 64 --chunk-kb 1024 --repeats 3

A private receiver is started on an ephemeral localhost port in a temp directory; results are
measured (bytes acknowledged / elapsed time) and written to data/benchmark_results.json.
Nothing is simulated, and results will differ on every machine.
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.config import ClientConfig  # noqa: E402
from client.socket_client import TransferJob  # noqa: E402
from server.config import ServerConfig  # noqa: E402
from server.socket_server import FileTransferServer  # noqa: E402


def make_file(path: Path, size: int, seed: int = 20240601) -> None:
    """Deterministic pseudo-random content: identical bytes on every run and machine."""
    rnd = random.Random(seed)
    with open(path, "wb") as fh:
        left = size
        while left:
            n = min(left, 1 << 20)
            fh.write(rnd.randbytes(n))
            left -= n


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--size-mb", type=int, default=64)
    p.add_argument("--chunk-kb", type=int, default=1024)
    p.add_argument("--connections", type=int, nargs="+", default=[1, 2, 4, 8])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--output", default=str(ROOT / "data" / "benchmark_results.json"))
    a = p.parse_args()

    with tempfile.TemporaryDirectory(prefix="mpft-bench-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "bench.bin"
        make_file(src, a.size_mb * 1024 * 1024)
        server = FileTransferServer(ServerConfig(port=0, data_dir=tmp_path / "srv"))
        server.start()
        print(f"file {a.size_mb} MiB, chunk {a.chunk_kb} KiB, receiver 127.0.0.1:{server.port}, repeats {a.repeats}")
        runs = []
        try:
            for rep in range(1, a.repeats + 1):
                for n in a.connections:
                    job = TransferJob(src, ClientConfig(port=server.port, connections=n, chunk_size=a.chunk_kb * 1024),
                                      restart=True, display_name=f"bench-{n}c-r{rep}.bin")
                    r = job.run()
                    for f in server.cfg.received_dir.iterdir():     # throw away the copy
                        f.unlink()
                    runs.append({"connections": n, "repeat": rep, "status": r["status"], "elapsed_s": r["elapsed"],
                                 "avg_mb_s": r["avg_mb_s"], "peak_mb_s": r["peak_mb_s"], "bytes": r["bytes_acked"],
                                 "retries": r["retries"], "chunks": r["validated_chunks"],
                                 "integrity": r["checksum_match"]})
                    print(f"  run {rep}: {n} conn -> {r['status']}, {r['elapsed']:.2f}s, {r['avg_mb_s']:.1f} MB/s")
                    if r["status"] != "completed":
                        print("transfer failed:", r["error"])
                        return 1
        finally:
            server.stop()

    rows = []
    for n in a.connections:
        rs = [r for r in runs if r["connections"] == n]
        rows.append({"connections": n, "time_s": statistics.fmean(r["elapsed_s"] for r in rs),
                     "avg_mb_s": statistics.fmean(r["avg_mb_s"] for r in rs), "peak_mb_s": max(r["peak_mb_s"] for r in rs),
                     "bytes": rs[0]["bytes"], "retries": sum(r["retries"] for r in rs), "chunks": rs[0]["chunks"],
                     "integrity": all(r["integrity"] for r in rs)})
    base = rows[0]["time_s"]
    print(f"\n{'conns':>5} {'time s':>8} {'avg MB/s':>9} {'peak MB/s':>10} {'bytes':>12} {'retries':>8} {'chunks':>7} {'speedup':>8}  integrity")
    for r in rows:
        print(f"{r['connections']:>5} {r['time_s']:>8.2f} {r['avg_mb_s']:>9.1f} {r['peak_mb_s']:>10.1f} {r['bytes']:>12} "
              f"{r['retries']:>8} {r['chunks']:>7} {base / r['time_s']:>7.2f}x  {'verified' if r['integrity'] else 'FAILED'}")
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "machine": platform.platform(),
                               "python": platform.python_version(), "size_mb": a.size_mb, "chunk_kb": a.chunk_kb,
                               "repeats": a.repeats, "summary": rows, "runs": runs}, indent=2))
    print(f"\nsaved {out}\nNote: speed-up is relative to the first setting; results depend on this machine (localhost has no real network).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
