"""Bootstrap state isolation and startup profiling.

Separates frozen startup configuration from mutable session state.
Records startup timing checkpoints for performance analysis.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from code_review_agent.config import Settings
    from code_review_agent.config_store import ConfigStore, SecretsStore

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StartupCheckpoint:
    """A single timing checkpoint during startup."""

    name: str
    elapsed_ms: float


@dataclass
class StartupProfile:
    """Records timing checkpoints during bootstrap."""

    _start: float = field(default_factory=time.perf_counter)
    _checkpoints: list[StartupCheckpoint] = field(default_factory=list)

    def checkpoint(self, name: str) -> None:
        """Record a timing checkpoint."""
        elapsed = (time.perf_counter() - self._start) * 1000
        self._checkpoints.append(StartupCheckpoint(name=name, elapsed_ms=round(elapsed, 1)))

    @property
    def checkpoints(self) -> list[StartupCheckpoint]:
        return list(self._checkpoints)

    @property
    def total_ms(self) -> float:
        if not self._checkpoints:
            return 0.0
        return self._checkpoints[-1].elapsed_ms

    def format_report(self) -> str:
        """Format startup profile as a readable report."""
        lines = ["Startup profile:"]
        prev = 0.0
        for cp in self._checkpoints:
            delta = cp.elapsed_ms - prev
            lines.append(f"  {cp.name:<30} {cp.elapsed_ms:>7.1f}ms (+{delta:.1f}ms)")
            prev = cp.elapsed_ms
        lines.append(f"  {'total':<30} {self.total_ms:>7.1f}ms")
        return "\n".join(lines)

    def save_to_file(self, path: Path) -> None:
        """Save startup profile to a JSON file."""
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_ms": self.total_ms,
            "checkpoints": [
                {"name": cp.name, "elapsed_ms": cp.elapsed_ms} for cp in self._checkpoints
            ],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class BootstrapState:
    """Frozen startup configuration. Immutable after bootstrap.

    Contains everything needed to initialize a session but nothing
    that changes during the session.
    """

    settings: Settings
    config_store: ConfigStore
    secrets_store: SecretsStore
    startup_profile: StartupProfile


def bootstrap(
    *,
    config_store: ConfigStore | None = None,
    secrets_store: SecretsStore | None = None,
) -> BootstrapState:
    """Run the bootstrap sequence with timing.

    Returns a frozen BootstrapState that can be used to initialize
    a SessionState.
    """
    profile = StartupProfile()

    # Phase 1: Import settings
    from code_review_agent.config import Settings
    from code_review_agent.config_store import ConfigStore as CS
    from code_review_agent.config_store import SecretsStore as SS

    profile.checkpoint("imports")

    # Phase 2: Load config store
    store = config_store or CS()
    profile.checkpoint("config_store")

    # Phase 3: Load secrets store
    secrets = secrets_store or SS()
    profile.checkpoint("secrets_store")

    # Phase 4: Inject secrets to env
    try:
        secrets.inject_to_env()
    except Exception:
        logger.debug("failed to inject secrets", exc_info=True)
    profile.checkpoint("secrets_inject")

    # Phase 5: Build settings
    settings = Settings()
    profile.checkpoint("settings")

    logger.debug(profile.format_report())

    return BootstrapState(
        settings=settings,
        config_store=store,
        secrets_store=secrets,
        startup_profile=profile,
    )
