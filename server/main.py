"""Command-line entry point for the receiving server."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from server.config import ConfigError, ServerConfig
from server.socket_server import FileTransferServer


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Multipath file transfer receiver (plain TCP, unencrypted)")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--data-dir", default="data", help="holds received/, staging/ and manifests/")
    p.add_argument("--allow-remote", action="store_true", help="required to bind a non-loopback address")
    p.add_argument("--max-file-size-mb", type=int, default=4096)
    p.add_argument("--config", help="JSON file with ServerConfig fields (see config/server.example.json); CLI flags win")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    fields = {}
    if a.config:
        try:
            fields = json.loads(Path(a.config).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            p.error(f"cannot read config file {a.config!r}: {exc}")
        if not isinstance(fields, dict):
            p.error("config file must contain a JSON object")
        unknown = set(fields) - set(ServerConfig.__dataclass_fields__)
        if unknown:
            p.error(f"unknown config keys: {sorted(unknown)}")
    fields.update(host=a.host, port=a.port, data_dir=Path(a.data_dir), allow_remote=a.allow_remote,
                  max_file_size=a.max_file_size_mb * 1024 * 1024)
    cfg = ServerConfig(**fields)
    try:
        FileTransferServer(cfg).serve_forever()
    except ConfigError as exc:
        p.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
