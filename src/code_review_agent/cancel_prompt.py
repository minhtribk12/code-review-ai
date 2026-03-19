"""Shared cancellation prompt and background-thread runner for reviews.

Used by both the CLI ``review`` command and the interactive REPL to
provide a consistent 3-option cancel experience:
  [1] Abort -- discard everything
  [2] Finish with partial results -- synthesize what we have
  [3] Continue waiting -- resume
"""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from rich.console import Console

    from code_review_agent.models import ReviewInput, ReviewReport
    from code_review_agent.orchestrator import Orchestrator
    from code_review_agent.progress import ProgressDisplay

logger = structlog.get_logger(__name__)


class CancelChoice(StrEnum):
    """User's response to the cancel prompt."""

    ABORT = "abort"
    FINISH_PARTIAL = "finish_partial"
    CONTINUE = "continue"


def prompt_cancel_choice(console: Console) -> CancelChoice:
    """Display the 3-option cancellation menu and return the user's choice.

    Defaults to ABORT on invalid input, KeyboardInterrupt, or EOF.
    """
    console.print()
    console.print("[bold]Review in progress.[/bold] What would you like to do?")
    console.print()
    console.print("  [bold red][1][/bold red] Abort    -- discard everything")
    console.print("  [bold yellow][2][/bold yellow] Finish   -- synthesize partial results")
    console.print("  [bold green][3][/bold green] Continue -- keep waiting")
    console.print()
    try:
        answer = input("> ").strip()
    except (KeyboardInterrupt, EOFError):
        return CancelChoice.ABORT

    if answer == "2":
        return CancelChoice.FINISH_PARTIAL
    if answer == "3":
        return CancelChoice.CONTINUE
    return CancelChoice.ABORT


def run_with_cancel_support(
    orchestrator: Orchestrator,
    review_input: ReviewInput,
    agent_names: list[str] | None,
    display: ProgressDisplay | None,
    console: Console,
) -> ReviewReport | None:
    """Run the orchestrator in a background thread with 3-option Ctrl+C handling.

    Returns a ``ReviewReport`` on success or finish-partial, ``None`` on abort.
    """
    result_holder: list[ReviewReport | None] = [None]
    error_holder: list[Exception | None] = [None]
    done = threading.Event()

    def _worker() -> None:
        try:
            result_holder[0] = orchestrator.run(
                review_input=review_input,
                agent_names=agent_names,
            )
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    while not done.is_set():
        try:
            done.wait(timeout=0.5)
        except KeyboardInterrupt:
            # Pause: stop auto-refresh but keep Live region (transient handles cleanup)
            if display is not None:
                display._stop_auto_refresh()
                display._live.stop()

            choice = prompt_cancel_choice(console)

            if choice == CancelChoice.ABORT:
                orchestrator.abort()
                if display is not None:
                    display.cancel()
                    display.stop()
                console.print("[bold]Aborting review...[/bold]")
                done.wait(timeout=10)
                console.print("[bold]Review aborted.[/bold]")
                return None

            if choice == CancelChoice.FINISH_PARTIAL:
                orchestrator.cancel()
                console.print("[bold]Finishing with partial results...[/bold]")
                # Stop the display now -- synthesis runs silently in the background
                if display is not None:
                    display.cancel()
                    display.stop()
                done.wait(timeout=120)
                break

            # CancelChoice.CONTINUE -- restart display
            console.print("[dim]Continuing review...[/dim]")
            if display is not None:
                display._live.start()
                display._start_auto_refresh()

    thread.join(timeout=5)

    if error_holder[0] is not None:
        raise error_holder[0]

    return result_holder[0]
