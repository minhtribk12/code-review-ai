"""Permission system for PR actions.

Controls whether destructive PR operations (posting/deleting comments)
require confirmation, auto-execute, or are denied entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class PermissionMode(StrEnum):
    """Permission modes for PR actions."""

    ASK = "ask"  # require confirmation (default)
    AUTO = "auto"  # auto-execute if confidence > threshold
    DENY = "deny"  # never allow


@dataclass(frozen=True)
class PermissionDecision:
    """Result of a permission check."""

    is_allowed: bool
    reason: str
    requires_confirmation: bool = False


@dataclass
class PermissionManager:
    """Manages permission decisions for PR actions."""

    mode: PermissionMode = PermissionMode.ASK
    auto_confidence_threshold: float = 0.8
    denial_count: int = 0
    approval_count: int = 0
    _denial_history: list[str] = field(default_factory=list)

    def check_post_comment(self, confidence: float, title: str) -> PermissionDecision:
        """Check if posting a PR comment is allowed."""
        if self.mode == PermissionMode.DENY:
            self._record_denial(title)
            return PermissionDecision(
                is_allowed=False,
                reason="PR commenting is disabled (mode=deny)",
            )

        if self.mode == PermissionMode.AUTO:
            if confidence >= self.auto_confidence_threshold:
                self.approval_count += 1
                return PermissionDecision(
                    is_allowed=True,
                    reason=f"Auto-approved (confidence={confidence:.0%})",
                )
            return PermissionDecision(
                is_allowed=True,
                reason=f"Below threshold ({confidence:.0%} < {self.auto_confidence_threshold:.0%})",  # noqa: E501
                requires_confirmation=True,
            )

        # ASK mode: always require confirmation
        return PermissionDecision(
            is_allowed=True,
            reason="Confirmation required (mode=ask)",
            requires_confirmation=True,
        )

    def check_delete_comment(self) -> PermissionDecision:
        """Check if deleting a PR comment is allowed."""
        if self.mode == PermissionMode.DENY:
            return PermissionDecision(
                is_allowed=False,
                reason="PR comment deletion is disabled (mode=deny)",
            )
        return PermissionDecision(
            is_allowed=True,
            reason="Deletion requires confirmation",
            requires_confirmation=True,
        )

    def record_user_denial(self, action: str) -> None:
        """Record that the user denied a permission prompt."""
        self._record_denial(action)

    def record_user_approval(self) -> None:
        """Record that the user approved a permission prompt."""
        self.approval_count += 1

    @property
    def denial_rate(self) -> float:
        """Return the denial rate as a fraction."""
        total = self.denial_count + self.approval_count
        if total == 0:
            return 0.0
        return self.denial_count / total

    @property
    def recent_denials(self) -> list[str]:
        """Return the last 10 denied actions."""
        return self._denial_history[-10:]

    def _record_denial(self, action: str) -> None:
        self.denial_count += 1
        self._denial_history.append(action)
