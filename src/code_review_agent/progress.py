from __future__ import annotations

import sys
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
    """Docker-style multi-bar progress display using Rich.

    Each agent gets its own row that updates independently:
    - waiting (dim)
    - running (pulsing blue)
    - done (green + elapsed time)
    - failed (red + error)

    The synthesis step is shown as its own row at the end.
    """

    def __init__(self, agent_names: list[str]) -> None:
        from rich.console import Console
        from rich.live import Live

        self._agent_names = agent_names
        self._console = Console()
        self._states: dict[str, tuple[str, str, float | None]] = {}

        # Initialize all agents as waiting
        for name in agent_names:
            self._states[name] = ("waiting", "dim", None)

        self._live = Live(
            self._build_table(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )

    def start(self) -> None:
        """Start the live display."""
        self._live.start()

    def stop(self) -> None:
        """Stop the live display and print final state."""
        self._live.update(self._build_table())
        self._live.stop()
        # Print final state so it persists in terminal
        self._console.print(self._build_table())

    def __call__(self, event: ReviewEvent, agent_name: str, elapsed: float | None = None) -> None:
        """Handle a review event and update the display."""
        if event == ReviewEvent.AGENT_STARTED:
            self._states[agent_name] = ("running", "blue", None)
        elif event == ReviewEvent.AGENT_COMPLETED:
            self._states[agent_name] = ("done", "green", elapsed)
        elif event == ReviewEvent.AGENT_FAILED:
            self._states[agent_name] = ("failed", "red", elapsed)
        elif event == ReviewEvent.SYNTHESIS_STARTED:
            self._states["synthesis"] = ("running", "blue", None)
        elif event == ReviewEvent.SYNTHESIS_COMPLETED:
            self._states["synthesis"] = ("done", "green", elapsed)

        self._live.update(self._build_table())

    def _build_table(self) -> Table:
        """Build the current state table for the live display."""
        table = Table(
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 1),
        )
        table.add_column("Agent", width=18)
        table.add_column("Status", width=12)
        table.add_column("Time", width=8)

        for name in self._agent_names:
            state, color, elapsed = self._states.get(name, ("waiting", "dim", None))
            status_text = self._format_status(state, color)
            time_text = f"{elapsed:.1f}s" if elapsed is not None else ""
            table.add_row(f"  {name}", status_text, time_text)

        # Show synthesis row if it exists
        if "synthesis" in self._states:
            state, color, elapsed = self._states["synthesis"]
            status_text = self._format_status(state, color)
            time_text = f"{elapsed:.1f}s" if elapsed is not None else ""
            table.add_row("  synthesis", status_text, time_text)

        return table

    @staticmethod
    def _format_status(state: str, color: str) -> str:
        """Format a status string with Rich markup."""
        if state == "running":
            return f"[{color}]>> running[/{color}]"
        if state == "done":
            return f"[{color}]-- done[/{color}]"
        if state == "failed":
            return f"[{color}]xx failed[/{color}]"
        return f"[{color}]   waiting[/{color}]"


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
