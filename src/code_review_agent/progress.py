from __future__ import annotations

import sys
import time
from typing import Protocol

from rich.table import Table

from code_review_agent.models import ReviewEvent


class EventCallback(Protocol):
    """Protocol for review event listeners."""

    def __call__(
        self, event: ReviewEvent, agent_name: str, elapsed: float | None = None
    ) -> None: ...


class NoOpCallback:
    """Silent callback for non-interactive or quiet mode."""

    def __call__(self, event: ReviewEvent, agent_name: str, elapsed: float | None = None) -> None:
        pass


class ProgressDisplay:
    """Multi-bar progress display with animated dots and elapsed time.

    Each agent gets its own row:
    - waiting (dim, no timer)
    - running (animated dots, live elapsed timer)
    - done (green checkmark + elapsed time)
    - failed (red x + elapsed time)
    """

    def __init__(self, agent_names: list[str]) -> None:
        from rich.console import Console
        from rich.live import Live

        self._agent_names = agent_names
        self._console = Console()
        self._states: dict[str, tuple[str, str, float | None]] = {}
        self._start_times: dict[str, float] = {}
        self._frame = 0

        self._cancelled = False

        for name in agent_names:
            self._states[name] = ("waiting", "dim", None)

        self._live = Live(
            self._build_table(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )

    @property
    def is_cancelled(self) -> bool:
        """Return True if the review was cancelled by the user."""
        return self._cancelled

    def cancel(self) -> None:
        """Mark the review as cancelled."""
        self._cancelled = True
        # Mark all running agents as cancelled
        for name in list(self._states):
            state, _color, elapsed = self._states[name]
            if state == "running":
                started = self._start_times.get(name)
                cancel_elapsed = time.monotonic() - started if started else elapsed
                self._states[name] = ("cancelled", "yellow", cancel_elapsed)

    def start(self) -> None:
        """Start the live display with auto-refresh."""
        self._live.start()
        self._start_auto_refresh()

    def stop(self) -> None:
        """Stop the live display and print final state."""
        self._stop_auto_refresh()
        self._live.update(self._build_table())
        self._live.stop()
        self._console.print(self._build_table())

    def _start_auto_refresh(self) -> None:
        """Start a background thread that refreshes the display every 250ms."""
        import threading

        self._refresh_running = True

        def _refresh_loop() -> None:
            while self._refresh_running:
                time.sleep(0.25)
                if self._refresh_running:
                    try:
                        self._live.update(self._build_table())
                    except Exception:
                        break

        self._refresh_thread = threading.Thread(
            target=_refresh_loop,
            daemon=True,
        )
        self._refresh_thread.start()

    def _stop_auto_refresh(self) -> None:
        """Stop the auto-refresh thread."""
        self._refresh_running = False

    def __call__(self, event: ReviewEvent, agent_name: str, elapsed: float | None = None) -> None:
        """Handle a review event and update the display."""
        if event == ReviewEvent.AGENT_STARTED:
            self._states[agent_name] = ("running", "blue", None)
            self._start_times[agent_name] = time.monotonic()
        elif event == ReviewEvent.AGENT_COMPLETED:
            self._states[agent_name] = ("done", "green", elapsed)
            self._start_times.pop(agent_name, None)
        elif event == ReviewEvent.AGENT_FAILED:
            self._states[agent_name] = ("failed", "red", elapsed)
            self._start_times.pop(agent_name, None)
        elif event == ReviewEvent.SYNTHESIS_STARTED:
            self._states["synthesis"] = ("running", "blue", None)
            self._start_times["synthesis"] = time.monotonic()
        elif event == ReviewEvent.SYNTHESIS_COMPLETED:
            self._states["synthesis"] = ("done", "green", elapsed)
            self._start_times.pop("synthesis", None)
        elif event == ReviewEvent.VALIDATION_STARTED:
            self._states["validation"] = ("running", "blue", None)
            self._start_times["validation"] = time.monotonic()
        elif event == ReviewEvent.VALIDATION_COMPLETED:
            self._states["validation"] = ("done", "green", elapsed)
            self._start_times.pop("validation", None)

        self._live.update(self._build_table())

    def _build_table(self) -> Table:
        """Build the current state table for the live display."""
        self._frame += 1

        table = Table(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 1),
        )
        table.add_column("Agent", width=18)
        table.add_column("Status", width=20)
        table.add_column("Time", width=10, justify="right")

        for name in self._agent_names:
            self._add_row(table, name)

        for extra in ("synthesis", "validation"):
            if extra in self._states:
                self._add_row(table, extra)

        # Show cancel hint if any agent is still running
        has_running = any(s[0] == "running" for s in self._states.values())
        if has_running and not self._cancelled:
            table.add_row("", "[dim]Press Ctrl+C to cancel[/dim]", "")

        return table

    def _add_row(self, table: Table, name: str) -> None:
        """Add a single agent row to the table."""
        state, color, elapsed = self._states.get(name, ("waiting", "dim", None))
        status_text = self._format_status(state, color)

        if state == "running":
            started = self._start_times.get(name)
            if started is not None:
                running_secs = time.monotonic() - started
                time_text = f"[{color}]{running_secs:.1f}s[/{color}]"
            else:
                time_text = ""
        elif elapsed is not None:
            time_text = f"{elapsed:.1f}s"
        else:
            time_text = ""

        table.add_row(f"  {name}", status_text, time_text)

    def _format_status(self, state: str, color: str) -> str:
        """Format a status string with animated dots for running state."""
        if state == "running":
            dots = "." * ((self._frame % 3) + 1)
            return f"[{color}]running{dots:<3}[/{color}]"
        if state == "done":
            return f"[{color}]done[/{color}]"
        if state == "failed":
            return f"[{color}]failed[/{color}]"
        if state == "cancelled":
            return f"[{color}]cancelled[/{color}]"
        return f"[{color}]waiting[/{color}]"


def is_interactive() -> bool:
    """Check if stdout is connected to a terminal."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def create_progress_callback(
    *,
    agent_names: list[str],
    is_quiet: bool,
) -> tuple[EventCallback, ProgressDisplay | None]:
    """Create the appropriate event callback based on environment.

    Returns:
        A tuple of (callback, display). The display is None for quiet/non-interactive
        modes and must be start()/stop()'d by the caller when not None.
    """
    if is_quiet or not is_interactive():
        return NoOpCallback(), None

    display = ProgressDisplay(agent_names=agent_names)
    return display, display
