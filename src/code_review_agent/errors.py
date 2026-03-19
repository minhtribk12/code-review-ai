"""Structured user-facing error display with detail, reason, and solution sections.

Every error shown to the user should use ``print_error()`` (TUI) or
``print_error_cli()`` (CLI) with a ``UserError`` that carries three fields:

- **detail**: What happened (always present).
- **reason**: Why it happened (when determinable).
- **solution**: How to fix it (when a fix path is known).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from code_review_agent.theme import theme

if TYPE_CHECKING:
    from rich.console import Console


@dataclass(frozen=True)
class UserError:
    """Structured error for user display.

    All user-facing errors must have a detail. Reason and solution are
    optional but strongly encouraged -- omit only when genuinely unknown.
    """

    detail: str
    reason: str | None = None
    solution: str | None = None


def print_error(
    error: UserError,
    *,
    console: Console | None = None,
) -> None:
    """Render a structured error as a Rich Panel (TUI mode)."""
    from rich.console import Console as RichConsole
    from rich.panel import Panel
    from rich.text import Text

    if console is None:
        console = RichConsole()

    body = Text()
    body.append(error.detail, style=theme.error)
    if error.reason:
        body.append("\n\n")
        body.append("  Reason: ", style="bold " + theme.info)
        body.append(error.reason, style="dim")
    if error.solution:
        body.append("\n\n")
        body.append("  Fix:    ", style="bold " + theme.success)
        body.append(error.solution, style=theme.accent)

    panel = Panel(body, title="Error", border_style=theme.error, expand=False)
    console.print(panel)


def print_error_cli(error: UserError) -> None:
    """Render a structured error as plain text to stderr (CLI mode)."""
    import typer

    lines = [f"Error: {error.detail}"]
    if error.reason:
        lines.append(f"Reason: {error.reason}")
    if error.solution:
        lines.append(f"Fix: {error.solution}")
    typer.echo("\n".join(lines), err=True)
