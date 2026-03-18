"""Background review runner for non-blocking REPL mode.

Runs the orchestrator in a daemon thread while the REPL prompt stays
active. Provides a ``format_status_line()`` method that the bottom
toolbar calls every 0.25 s to render animated progress.

Thread safety: all shared mutable state (_agent_states, _phase,
_finishing) is protected by ``_lock``. The lock is held briefly
for dict reads/writes -- never during I/O.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from code_review_agent.models import OutputFormat, ReviewEvent

if TYPE_CHECKING:
    from code_review_agent.config import Settings
    from code_review_agent.llm_client import LLMClient
    from code_review_agent.models import ReviewInput, ReviewReport

logger = structlog.get_logger(__name__)

_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
_ICON_DONE = "\u2713"
_ICON_FAILED = "\u2717"
_ICON_WAITING = "\u00b7"


@dataclass(frozen=True)
class _AgentState:
    """Snapshot of a single agent's status."""

    status: str  # waiting, running, done, failed
    elapsed: float | None = None
    started_at: float | None = None


def _format_seconds(seconds: float) -> str:
    """Format seconds as a compact human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m{secs:02d}s"


def _format_elapsed(state: _AgentState, now: float) -> str:
    """Format elapsed time for an agent: live timer if running, final if done."""
    if state.status == "running" and state.started_at is not None:
        return _format_seconds(now - state.started_at)
    if state.status in ("done", "failed") and state.elapsed is not None:
        return _format_seconds(state.elapsed)
    return ""


class BackgroundReview:
    """Owns the review lifecycle in a background thread.

    Implements the ``EventCallback`` protocol so the orchestrator pushes
    events here. The toolbar reads ``format_status_line()`` for display.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        llm_client: LLMClient,
        review_input: ReviewInput,
        agent_names: list[str],
        output_format: OutputFormat = OutputFormat.RICH,
        label: str = "local diff",
    ) -> None:
        from code_review_agent.orchestrator import Orchestrator

        self._review_input = review_input
        self._agent_names = agent_names
        self.output_format = output_format
        self.label = label

        self.orchestrator = Orchestrator(
            settings=settings,
            llm_client=llm_client,
            on_event=self,
        )

        self._done = threading.Event()
        self._collected = False
        self._result: ReviewReport | None = None
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None

        # Lock protects _agent_states, _phase, _finishing (read by toolbar, written by worker)
        self._lock = threading.Lock()
        self._agent_states: dict[str, _AgentState] = {
            name: _AgentState(status="waiting") for name in agent_names
        }
        self._phase = "starting"
        self._finishing = False
        self._frame = 0
        self._started_at = time.monotonic()
        self._prompt_app: Any = None  # set by REPL to interrupt prompt on done

    # -- Lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background daemon thread."""
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        try:
            self._result = self.orchestrator.run(
                review_input=self._review_input,
                agent_names=self._agent_names,
            )
        except Exception as exc:
            self._error = exc
            with self._lock:
                self._phase = "failed"
        else:
            with self._lock:
                self._phase = "done"
        finally:
            self._done.set()
            self._interrupt_prompt()

    # -- Properties ------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True while the background thread is still working."""
        return not self._done.is_set()

    @property
    def is_done(self) -> bool:
        """True once the background thread has finished (success or error)."""
        return self._done.is_set()

    def set_prompt_app(self, app: object) -> None:
        """Store a reference to the prompt_toolkit Application.

        Called by the REPL after creating the PromptSession so the
        worker thread can interrupt the prompt when the review finishes.
        """
        self._prompt_app = app

    def _interrupt_prompt(self) -> None:
        """Exit the active prompt so the REPL loop processes the result."""
        import contextlib

        app = self._prompt_app
        if app is None:
            return
        with contextlib.suppress(Exception):
            from code_review_agent.interactive.repl import _REVIEW_DONE_SENTINEL

            app.exit(result=_REVIEW_DONE_SENTINEL)

    def mark_finishing(self) -> None:
        """Mark the review as finishing (cancel sent, waiting for synthesis)."""
        with self._lock:
            self._finishing = True

    def collect_result(self) -> tuple[ReviewReport | None, Exception | None]:
        """Return (report, error) and clear internal state.

        Safe to call multiple times -- second call returns (None, None).
        """
        if self._collected:
            return None, None
        self._collected = True
        report = self._result
        error = self._error
        self._result = None
        self._error = None
        return report, error

    # -- EventCallback protocol ------------------------------------------------

    def __call__(
        self,
        event: ReviewEvent,
        agent_name: str,
        elapsed: float | None = None,
    ) -> None:
        """Handle orchestrator events (called from worker threads)."""
        with self._lock:
            if event == ReviewEvent.AGENT_STARTED:
                self._agent_states[agent_name] = _AgentState(
                    status="running",
                    started_at=time.monotonic(),
                )
                self._phase = "agents"
            elif event == ReviewEvent.AGENT_COMPLETED:
                self._agent_states[agent_name] = _AgentState(
                    status="done",
                    elapsed=elapsed,
                )
            elif event == ReviewEvent.AGENT_FAILED:
                # Don't downgrade "done" to "failed" (race with late completion)
                current = self._agent_states.get(agent_name)
                if current is None or current.status != "done":
                    self._agent_states[agent_name] = _AgentState(
                        status="failed",
                        elapsed=elapsed,
                    )
            elif event == ReviewEvent.SYNTHESIS_STARTED:
                self._phase = "synthesis"
                self._agent_states["synthesis"] = _AgentState(
                    status="running",
                    started_at=time.monotonic(),
                )
            elif event == ReviewEvent.SYNTHESIS_COMPLETED:
                self._agent_states["synthesis"] = _AgentState(
                    status="done",
                    elapsed=elapsed,
                )
            elif event == ReviewEvent.VALIDATION_STARTED:
                self._phase = "validation"
                self._agent_states["validation"] = _AgentState(
                    status="running",
                    started_at=time.monotonic(),
                )
            elif event == ReviewEvent.VALIDATION_COMPLETED:
                self._agent_states["validation"] = _AgentState(
                    status="done",
                    elapsed=elapsed,
                )

    # -- Toolbar rendering -----------------------------------------------------

    def format_status_line(self) -> str:
        """Build a plain-text status line for the prompt_toolkit toolbar.

        Called from the toolbar lambda every ~0.25 s. Returns plain text
        (no HTML tags) -- the caller wraps it in prompt_toolkit HTML.
        Thread-safe: takes a snapshot of state under lock.

        Shows full agent names with live elapsed time, dynamically
        truncated to fit the terminal width.
        """
        self._frame += 1
        now = time.monotonic()
        spinner = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]

        # Snapshot state under lock
        with self._lock:
            states = dict(self._agent_states)
            phase = self._phase
            finishing = self._finishing

        agent_names = self._agent_names
        done_count = sum(
            1
            for n in agent_names
            if states.get(n, _AgentState(status="waiting")).status in ("done", "failed")
        )
        total = len(agent_names)

        # Build per-agent status with full name and elapsed time
        parts: list[str] = []
        extras = [e for e in ("synthesis", "validation") if e in states]
        all_names = [*agent_names, *extras]
        for name in all_names:
            state = states.get(name, _AgentState(status="waiting"))
            elapsed_str = _format_elapsed(state, now)
            if state.status == "done":
                parts.append(f"{_ICON_DONE} {name} {elapsed_str}")
            elif state.status == "failed":
                parts.append(f"{_ICON_FAILED} {name} {elapsed_str}")
            elif state.status == "running":
                parts.append(f"{spinner} {name} {elapsed_str}")
            else:
                parts.append(f"{_ICON_WAITING} {name}")

        agents_str = "  ".join(parts)

        # Truncate to fit terminal width
        term_width = shutil.get_terminal_size((80, 24)).columns
        # Reserve space for phase prefix like "⠋ Reviewing PR #42 [2/4] "
        prefix_budget = 30 + len(self.label)
        max_agents_width = max(20, term_width - prefix_budget)
        if len(agents_str) > max_agents_width:
            agents_str = agents_str[: max_agents_width - 1] + "\u2026"

        # Phase label
        if phase == "done":
            total_elapsed = _format_seconds(now - self._started_at)
            return f"{_ICON_DONE} Complete {total_elapsed} [{done_count}/{total}] {agents_str}"
        if phase == "failed":
            total_elapsed = _format_seconds(now - self._started_at)
            return f"{_ICON_FAILED} Failed {total_elapsed} [{done_count}/{total}] {agents_str}"
        total_elapsed = _format_seconds(now - self._started_at)
        if finishing:
            return f"{spinner} Finishing {total_elapsed} [{done_count}/{total}] {agents_str}"
        return (
            f"{spinner} Reviewing {self.label} {total_elapsed} [{done_count}/{total}] {agents_str}"
        )
