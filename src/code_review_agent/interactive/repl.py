"""Interactive REPL for the code review agent."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console

from code_review_agent.interactive.commands.config_cmd import cmd_config
from code_review_agent.interactive.commands.git_read import (
    cmd_diff,
    cmd_log,
    cmd_show,
    cmd_status,
)
from code_review_agent.interactive.commands.meta import (
    cmd_agents,
    cmd_clear,
    cmd_help,
    cmd_shell,
    cmd_version,
)
from code_review_agent.interactive.commands.review_cmd import cmd_review
from code_review_agent.interactive.commands.usage_cmd import cmd_usage
from code_review_agent.interactive.completers import build_static_completer
from code_review_agent.interactive.session import SessionState

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)
console = Console()

# Command handler type: (args, session) -> None
CommandHandler = Callable[[list[str], SessionState], None]

# Map command names to their handlers.
_COMMANDS: dict[str, CommandHandler] = {
    "status": cmd_status,
    "diff": cmd_diff,
    "log": cmd_log,
    "show": cmd_show,
    "review": cmd_review,
    "config": cmd_config,
    "usage": cmd_usage,
    "help": cmd_help,
    "agents": cmd_agents,
    "version": cmd_version,
    "clear": cmd_clear,
}

_VERSION = "0.1.0"


def _get_toolbar(session: SessionState) -> HTML:
    """Build the bottom toolbar content."""
    branch = ""
    try:
        from code_review_agent.interactive import git_ops

        branch = git_ops.current_branch()
    except Exception:
        branch = "?"
    return HTML(
        f" <b>Branch:</b> {branch}"
        f" | <b>Reviews:</b> {session.reviews_completed}"
        f" | <b>Tier:</b> {session.settings.token_tier}"
    )


def run_repl(settings: Settings) -> None:
    """Launch the interactive REPL loop."""
    session = SessionState(settings=settings)
    completer = build_static_completer()

    history_path = "~/.cra_history"

    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        complete_while_typing=True,
        bottom_toolbar=lambda: _get_toolbar(session),
        refresh_interval=1.0,
    )

    console.print(f"\n  [bold]code-review-agent[/bold] v{_VERSION}")
    console.print(
        "  Type [bold cyan]help[/bold cyan] for commands, Tab for autocomplete, Ctrl+D to exit.\n"
    )

    while True:
        try:
            text = prompt_session.prompt("cra> ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not text:
            continue

        _dispatch(text, session)


def _dispatch(text: str, session: SessionState) -> None:
    """Parse and dispatch a single command."""
    # Shell escape
    if text.startswith("!"):
        shell_cmd = text[1:].strip()
        cmd_shell(shell_cmd.split() if shell_cmd else [], session)
        return

    # Exit aliases
    if text in ("exit", "quit", "q"):
        if session.config_overrides:
            n = len(session.config_overrides)
            console.print(f"  [yellow]You have {n} unsaved config change(s).[/yellow]")
        raise EOFError

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        console.print(f"[red]Parse error: {exc}[/red]")
        return

    if not tokens:
        return

    command = tokens[0].lower()
    args = tokens[1:]

    handler = _COMMANDS.get(command)
    if handler is None:
        console.print(
            f"[red]Unknown command: {command}[/red]. "
            "Type [bold cyan]help[/bold cyan] for available commands."
        )
        return

    try:
        handler(args, session)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        logger.debug("command failed", command=command, error=str(exc), exc_info=True)
