"""Server configuration and safety limits."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

MiB = 1024 * 1024
GiB = 1024 * MiB


class ConfigError(ValueError):
    """Invalid server configuration."""


def is_loopback(host: str) -> bool:
    """True if ``host`` is localhost or a loopback IP."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class ServerConfig:
    """All server tunables. Defaults are safe for a local demo."""

    host: str = "127.0.0.1"
    port: int = 9000
    data_dir: Path = field(default_factory=lambda: Path("data"))
    allow_remote: bool = False            # must be True to bind a non-loopback address
    max_file_size: int = 4 * GiB
    min_chunk_size: int = 1024
    max_chunk_size: int = 8 * MiB
    max_chunks: int = 65_536
    max_connections_per_transfer: int = 32
    max_total_connections: int = 256
    socket_timeout: float = 30.0          # idle timeout per connection (seconds)
    manifest_flush_interval: float = 0.5  # debounce for manifest writes

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)

    @property
    def received_dir(self) -> Path:
        return self.data_dir / "received"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def manifests_dir(self) -> Path:
        return self.data_dir / "manifests"

    def validate(self) -> None:
        """Raise ConfigError for unsafe or inconsistent settings."""
        if not is_loopback(self.host) and not self.allow_remote:
            raise ConfigError(
                f"Refusing to bind to non-loopback address {self.host!r}. "
                "Pass --allow-remote to expose this unencrypted service on the network."
            )
        if not 0 <= self.port <= 65535:
            raise ConfigError("port must be between 0 and 65535")
        if self.min_chunk_size <= 0 or self.min_chunk_size > self.max_chunk_size:
            raise ConfigError("invalid chunk size limits")
