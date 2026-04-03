"""Graceful shutdown with phase ordering.

Ensures terminal is restored to a clean state even on crash.
Inspired by better-clawd's gracefulShutdown.ts.

Phase order:
1. Synchronous terminal reset (immediate -- no async, no I/O)
2. Save session state (1s timeout)
3. Run post-session hooks (1s timeout)
4. Flush usage data (500ms timeout)
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

logger = structlog.get_logger(__name__)

_shutdown_registered = False
_session_ref: SessionState | None = None


def register_shutdown(session: SessionState) -> None:
    """Register graceful shutdown handlers for the session."""
    global _shutdown_registered, _session_ref
    if _shutdown_registered:
        return
    _session_ref = session
    import atexit

    atexit.register(_shutdown)
    _shutdown_registered = True


def _shutdown() -> None:
    """Execute shutdown phases with individual timeouts."""
    # Phase 1: Terminal reset (synchronous, immediate)
    _reset_terminal()

    if _session_ref is None:
        return

    # Phase 2: Save session state (1s timeout)
    _run_with_timeout(_save_state, timeout=1.0, phase="save_state")

    # Phase 3: Flush usage data (500ms timeout)
    _run_with_timeout(_flush_usage, timeout=0.5, phase="flush_usage")


def _reset_terminal() -> None:
    """Reset terminal to a clean state. No exceptions allowed.

    Only writes escape sequences when stdout is a real TTY to avoid
    garbage output in redirected/piped scenarios.
    """
    try:
        if not sys.stdout.isatty():
            return
        # Disable mouse tracking (SGR and X11 protocols)
        sys.stdout.write("\033[?1000l\033[?1006l")
        # Exit alt screen if active
        sys.stdout.write("\033[?1049l")
        # Reset cursor visibility
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    except Exception:  # noqa: S110 - terminal reset must never raise
        pass


def _save_state() -> None:
    """Persist config overrides to config.yaml."""
    if _session_ref is None:
        return
    try:
        from code_review_agent.interactive.commands.config_cmd import save_config_to_yaml

        save_config_to_yaml(_session_ref)
    except Exception:
        logger.debug("failed to save state during shutdown", exc_info=True)


def _flush_usage() -> None:
    """Flush any pending usage data."""
    if _session_ref is None:
        return
    try:
        _ = len(_session_ref.usage_history.records)
    except Exception:
        logger.debug("failed to flush usage during shutdown", exc_info=True)


def _run_with_timeout(fn: object, timeout: float, phase: str) -> None:
    """Run a function with a timeout. Log failures, never raise."""
    if not callable(fn):
        return
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.debug(f"shutdown phase {phase} timed out after {timeout}s")
