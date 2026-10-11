"""Command-line sender: python -m client.main FILE --host H --port P --connections N"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from client.config import ClientConfig
from client.socket_client import TransferJob


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Multipath file transfer sender")
    p.add_argument("file")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--connections", "-c", type=int, default=4)
    p.add_argument("--chunk-size-kb", type=int, default=1024)
    p.add_argument("--restart", action="store_true", help="discard any partial state on the server first")
    p.add_argument("--json", action="store_true", help="print the final result as JSON")
    a = p.parse_args(argv)
    cfg = ClientConfig(host=a.host, port=a.port, connections=a.connections, chunk_size=a.chunk_size_kb * 1024)
    job = TransferJob(Path(a.file), cfg, restart=a.restart)
    t = threading.Thread(target=job.run, daemon=True)
    t.start()
    try:
        while t.is_alive():
            s = job.snapshot()
            c = s["chunks"]
            print(f"\r[{s['status']:<12}] {c['completed']}/{c['total']} chunks  "
                  f"{s['bytes']['done'] / 1e6:8.1f}/{s['bytes']['total'] / 1e6:.1f} MB  "
                  f"{s['throughput']['current'] / 1e6:6.1f} MB/s", end="", file=sys.stderr, flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        job.cancel()
    t.join()
    print(file=sys.stderr)
    r = job.result or {}
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"status: {r.get('status')}  {r.get('error') or ''}")
        print(f"original sha256: {r.get('original_sha256')}\nreceived sha256: {r.get('received_sha256')}")
        print(f"time {r.get('elapsed', 0):.2f}s  avg {r.get('avg_mb_s', 0):.1f} MB/s  retries {r.get('retries')}")
    return 0 if r.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
