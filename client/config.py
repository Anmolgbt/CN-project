"""Client configuration."""
from __future__ import annotations

from dataclasses import dataclass

KiB = 1024
MiB = 1024 * KiB
MAX_CONNECTIONS = 16


@dataclass
class ClientConfig:
    """Tunables for one transfer."""

    host: str = "127.0.0.1"
    port: int = 9000
    connections: int = 4
    chunk_size: int = 1 * MiB
    max_retries: int = 5            # retries allowed per chunk after NACKs
    max_reconnects: int = 5         # consecutive connection failures tolerated per worker
    backoff_base: float = 0.2       # seconds; delay = base * 2**(attempt-1) + jitter
    backoff_max: float = 5.0
    connect_timeout: float = 5.0
    socket_timeout: float = 30.0
    finish_timeout: float = 300.0   # hashing a big file on the server can take a while

    def validate(self) -> None:
        if not 1 <= self.connections <= MAX_CONNECTIONS:
            raise ValueError(f"connections must be 1..{MAX_CONNECTIONS}")
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be 1..65535")
