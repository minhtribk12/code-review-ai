"""Usage tab: token usage, cost, and review history dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState


class UsageTab(Vertical):
    """Token usage and cost dashboard."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("", id="usage-dashboard")

    def on_mount(self) -> None:
        self.refresh_usage()

    def refresh_usage(self) -> None:
        label = self.query_one("#usage-dashboard", Static)
        session = self._session

        lines = [
            " [bold]Session Usage[/bold]",
            "",
            f" Reviews completed: {session.reviews_completed}",
            f" Total tokens used: {session.total_tokens_used:,}",
        ]

        # Per-review breakdown from usage history
        records = session.usage_history.records
        if records:
            total_cost = sum(r.estimated_cost_usd or 0 for r in records)
            total_calls = sum(r.llm_calls for r in records)
            lines.extend(
                [
                    f" Total LLM calls:   {total_calls}",
                    f" Estimated cost:    ${total_cost:.4f}",
                    "",
                    " [bold]Recent Reviews[/bold]",
                    "",
                ]
            )
            import time as _time

            for rec in records[-5:]:
                cost_str = f"${rec.estimated_cost_usd:.4f}" if rec.estimated_cost_usd else "n/a"
                time_str = _time.strftime("%H:%M:%S", _time.localtime(rec.timestamp))
                lines.append(
                    f"   {time_str}  "
                    f"tokens={rec.total_tokens:,}  "
                    f"calls={rec.llm_calls}  "
                    f"cost={cost_str}"
                )
        else:
            lines.extend(
                [
                    "",
                    " No reviews completed yet.",
                ]
            )

        # Tier and budget info
        settings = session.effective_settings
        lines.extend(
            [
                "",
                " [bold]Configuration[/bold]",
                "",
                f" Token tier:       {settings.token_tier}",
                f" Deepening rounds: {settings.max_deepening_rounds}",
                f" Validation:       "
                f"{'enabled' if settings.is_validation_enabled else 'disabled'}",
            ]
        )

        if settings.max_tokens_per_review:
            lines.append(f" Token budget:     {settings.max_tokens_per_review:,}/review")

        label.update("\n".join(lines))
