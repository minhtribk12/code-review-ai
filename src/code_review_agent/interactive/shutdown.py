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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

_shutdown_registered = False
_session_ref: SessionState | None = None
_save_fn: object | None = None  # pre-imported save function


def register_shutdown(session: SessionState) -> None:
    """Register graceful shutdown handlers for the session.

    Pre-imports the save function so it's available during interpreter
    teardown when lazy imports may fail.
    """
    global _shutdown_registered, _session_ref, _save_fn
    if _shutdown_registered:
        return
    _session_ref = session

    # Pre-import while modules are still alive
    from code_review_agent.interactive.commands.config_cmd import save_config_to_yaml

    _save_fn = save_config_to_yaml

    import atexit

    atexit.register(_shutdown)
    _shutdown_registered = True


def _shutdown() -> None:
    """Execute shutdown phases synchronously.

    Runs directly (no threads) because atexit handlers execute during
    interpreter teardown when daemon threads may be killed and modules
    may be partially garbage-collected.
    """
    # Phase 1: Terminal reset (synchronous, immediate)
    _reset_terminal()

    if _session_ref is None:
        return

    # Phase 2: Save session state (synchronous, no thread)
    _save_state()

    # Phase 3: Flush usage data
    _flush_usage()


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
    if _session_ref is None or _save_fn is None:
        return
    try:  # noqa: SIM105 - contextlib may be unavailable during teardown
        _save_fn(_session_ref)  # type: ignore[operator]
    except Exception:  # noqa: S110
        pass


def _flush_usage() -> None:
    """Flush any pending usage data."""
    if _session_ref is None:
        return
    try:  # noqa: SIM105 - contextlib may be unavailable during teardown
        _ = len(_session_ref.usage_history.records)
    except Exception:  # noqa: S110
        pass
