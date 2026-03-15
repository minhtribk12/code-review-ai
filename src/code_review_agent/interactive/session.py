"""Session state for the interactive REPL."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from code_review_agent.config import Settings


@dataclass
class PRCache:
    """Cache for PR list data to avoid redundant GitHub API calls.

    Stores the most recent list_prs result with a monotonic timestamp.
    Invalidated explicitly on PR write operations (create/merge/approve)
    or when the TTL expires.
    """

    data: list[dict[str, Any]] = field(default_factory=list)
    owner: str = ""
    repo: str = ""
    state: str = ""
    fetched_at: float = 0.0
    ttl_seconds: float = 60.0

    @property
    def is_valid(self) -> bool:
        """Return True if cached data is still fresh."""
        if not self.data:
            return False
        return (time.monotonic() - self.fetched_at) < self.ttl_seconds

    def get(self, owner: str, repo: str, state: str) -> list[dict[str, Any]] | None:
        """Return cached data if it matches the query and is still fresh."""
        if self.is_valid and self.owner == owner and self.repo == repo and self.state == state:
            return self.data
        return None

    def set(
        self,
        owner: str,
        repo: str,
        state: str,
        data: list[dict[str, Any]],
    ) -> None:
        """Store PR list data in cache."""
        self.owner = owner
        self.repo = repo
        self.state = state
        self.data = data
        self.fetched_at = time.monotonic()

    def invalidate(self) -> None:
        """Clear cached data (call after PR write operations)."""
        self.data = []
        self.fetched_at = 0.0


@dataclass
class SessionState:
    """Mutable state that persists across REPL commands within a session.

    Holds a mutable copy of settings (for session-only overrides),
    usage counters, and context tracking.
    """

    settings: Settings
    reviews_completed: int = 0
    current_context: str = "default"
    config_overrides: dict[str, str] = field(default_factory=dict)
    pr_cache: PRCache = field(default_factory=PRCache)
