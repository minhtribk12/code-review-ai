"""Session state for the interactive REPL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_review_agent.config import Settings


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
