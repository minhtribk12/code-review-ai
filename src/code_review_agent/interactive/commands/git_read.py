"""Git read commands: status, diff, log, show."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def cmd_status(args: list[str], session: SessionState) -> None:
    """Show git status with branch info."""
    output = git_ops.status_short()
    if not output.strip():
        console.print("[green]Working tree clean[/green]")
        return
    console.print(Panel(output.rstrip(), title="git status", border_style="blue"))


def cmd_diff(args: list[str], session: SessionState) -> None:
    """Show git diff (unstaged, staged, file, or between refs)."""
    if not args:
        output = git_ops.diff()
    elif args[0] == "staged":
        output = git_ops.diff(staged=True)
    elif ".." in args[0]:
        parts = args[0].split("..", 1)
        output = git_ops.diff_between(parts[0], parts[1])
    elif args[0].startswith("HEAD~") or args[0].startswith("HEAD^"):
        output = git_ops.diff_ref(args[0])
    else:
        output = git_ops.diff(file_path=args[0])

    if not output.strip():
        console.print("[dim]No differences.[/dim]")
        return

    syntax = Syntax(output, "diff", theme="monokai", line_numbers=False)
    console.print(syntax)


def cmd_log(args: list[str], session: SessionState) -> None:
    """Show git log in compact format."""
    count = 20
    branch = None

    i = 0
    while i < len(args):
        if args[i] == "-n" and i + 1 < len(args):
            count = int(args[i + 1])
            i += 2
        else:
            branch = args[i]
            i += 1

    output = git_ops.log_oneline(count=count, branch=branch)
    if not output.strip():
        console.print("[dim]No commits.[/dim]")
        return
    console.print(Panel(output.rstrip(), title="git log", border_style="blue"))


def cmd_show(args: list[str], session: SessionState) -> None:
    """Show full commit details with diff."""
    if not args:
        console.print("[red]Usage: show <commit>[/red]")
        return
    output = git_ops.show_commit(args[0])
    syntax = Syntax(output, "diff", theme="monokai", line_numbers=False)
    console.print(syntax)
