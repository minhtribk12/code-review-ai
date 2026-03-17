"""Interactive REPL for the code review agent."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console

from code_review_agent.interactive.commands.config_cmd import cmd_config
from code_review_agent.interactive.commands.findings_cmd import cmd_findings
from code_review_agent.interactive.commands.git_read import (
    cmd_diff,
    cmd_log,
    cmd_show,
    cmd_status,
)
from code_review_agent.interactive.commands.git_write import (
    cmd_add,
    cmd_branch,
    cmd_commit,
    cmd_stash,
    cmd_unstage,
)
from code_review_agent.interactive.commands.history_cmd import cmd_history
from code_review_agent.interactive.commands.meta import (
    cmd_agents,
    cmd_clear,
    cmd_help,
    cmd_shell,
    cmd_version,
)
from code_review_agent.interactive.commands.pr_read import cmd_pr
from code_review_agent.interactive.commands.repo_cmd import cmd_repo
from code_review_agent.interactive.commands.review_cmd import cmd_review
from code_review_agent.interactive.commands.usage_cmd import cmd_usage
from code_review_agent.interactive.commands.watch_cmd import cmd_watch
from code_review_agent.interactive.completers import build_static_completer
from code_review_agent.interactive.session import SessionState
from code_review_agent.theme import theme

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
    "branch": cmd_branch,
    "add": cmd_add,
    "unstage": cmd_unstage,
    "commit": cmd_commit,
    "stash": cmd_stash,
    "review": cmd_review,
    "findings": cmd_findings,
    "pr": cmd_pr,
    "repo": cmd_repo,
    "watch": cmd_watch,
    "history": cmd_history,
    "config": cmd_config,
    "usage": cmd_usage,
    "help": cmd_help,
    "agents": cmd_agents,
    "version": cmd_version,
    "clear": cmd_clear,
}

_VERSION = "0.1.0"


def _get_toolbar(session: SessionState) -> HTML:
    """Build the bottom toolbar separated by a horizontal line."""
    branch = ""
    try:
        from code_review_agent.interactive import git_ops

        branch = git_ops.current_branch()
    except Exception:
        branch = "?"
    tokens = _format_token_count(session.total_tokens_used)

    # Build repo label: "owner/repo:local" or "owner/repo:remote"
    if session.active_repo:
        source = session.active_repo_source or "local"
        repo_label = f"{session.active_repo}:{source}"
    else:
        # Derive from local git remote without storing
        try:
            from code_review_agent.interactive.git_ops import (
                parse_github_owner_repo,
                remote_url,
            )

            url = remote_url()
            if url:
                parsed = parse_github_owner_repo(url)
                repo_label = f"{parsed[0]}/{parsed[1]}:local" if parsed else ""
            else:
                repo_label = ""
        except Exception:
            repo_label = ""

    # DB-backed usage stats for the configured window
    usage_label = ""
    try:
        from code_review_agent.progress import USAGE_WINDOW_HOURS, USAGE_WINDOW_LABELS
        from code_review_agent.storage import ReviewStorage

        settings = session.effective_settings
        window = settings.usage_window
        if window != "session":
            storage = ReviewStorage(settings.history_db_path)
            hours = USAGE_WINDOW_HOURS.get(window)
            stats = storage.get_usage_stats(hours=hours)
            window_label = USAGE_WINDOW_LABELS.get(window, window)
            tok = stats["total_tokens"]
            tok_str = f"{tok / 1000:.1f}k" if tok >= 1000 else str(tok)
            cost = stats["estimated_cost_usd"]
            cost_str = f"${cost:.4f}" if 0 < cost < 0.01 else f"${cost:.2f}" if cost else "$0"
            usage_label = f" | <b>{window_label}:</b> {tok_str} tokens, {cost_str}"
    except Exception:  # noqa: S110
        pass

    repo_part = f" | <b>Repo:</b> {repo_label}" if repo_label else ""
    separator = "\u2500" * 80
    return HTML(
        f'<style fg="ansigray">{separator}</style>\n'
        f" <b>Branch:</b> {branch}"
        f"{repo_part}"
        f" | <b>Reviews:</b> {session.reviews_completed}"
        f" | <b>Tokens:</b> {tokens}"
        f" | <b>Tier:</b> {session.display_tier}"
        f"{usage_label}"
        f"{'  !' if session.has_cost_warning else ''}"
    )


_REPL_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse",
    }
)


def _format_token_count(count: int) -> str:
    """Format token count with human-readable suffix (k, m, b)."""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}b"
    if count >= 999_950:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _print_welcome() -> None:
    """Print a friendly welcome banner with getting-started guidance."""
    console.print()
    console.print(f"  [bold]Code Review Agent[/bold] v{_VERSION}")
    console.print("  Multi-agent code review powered by LLM. I can review your code")
    console.print("  for security, performance, style, and test coverage issues.")
    console.print()
    console.print("  [bold]Getting started:[/bold]")
    console.print()
    console.print(
        "    [bold cyan]review --diff <file>[/bold cyan]       Review a local diff or patch file"
    )
    console.print(
        "    [bold cyan]repo select <owner/repo>[/bold cyan]  Set the active GitHub repository"
    )
    console.print("    [bold cyan]pr list[/bold cyan]                  List open pull requests")
    console.print(
        "    [bold cyan]pr review <number>[/bold cyan]"
        "        Review a PR (fetches diff from GitHub)"
    )
    console.print(
        "    [bold cyan]findings[/bold cyan]                 Browse, triage, and post findings"
    )
    console.print(
        "    [bold cyan]config edit[/bold cyan]              Open the interactive config editor"
    )
    console.print(
        "    [bold cyan]help[/bold cyan]                     Show all available commands"
    )
    console.print()
    console.print("  [dim]Tab for autocomplete | Ctrl+D to exit[/dim]")
    console.print()


def run_repl(settings: Settings) -> None:
    """Launch the interactive REPL loop."""
    session = SessionState(settings=settings)
    completer = build_static_completer()

    prompt_str = settings.interactive_prompt
    history_path = os.path.expanduser(settings.interactive_history_file)

    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        complete_while_typing=True,
        bottom_toolbar=lambda: _get_toolbar(session),
        style=_REPL_STYLE,
        refresh_interval=1.0,
        vi_mode=settings.interactive_vi_mode,
    )

    _print_welcome()

    while True:
        try:
            text = prompt_session.prompt(prompt_str).strip()
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
            console.print(
                f"  [{theme.warning}]You have {n} unsaved config change(s).[/{theme.warning}]"
            )
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
