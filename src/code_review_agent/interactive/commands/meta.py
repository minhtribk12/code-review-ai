"""Meta commands: help, agents, version, history, clear, exit."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from code_review_agent.agents import ALL_AGENT_NAMES

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

_VERSION = "0.1.0"

# Command registry: (name, description) grouped by category.
COMMAND_HELP: dict[str, list[tuple[str, str]]] = {
    "Git Read": [
        ("status", "Show git status (branch, changed files)"),
        ("diff [staged|file|ref..ref]", "Show git diff"),
        ("log [-n N] [branch]", "Show git log (compact)"),
        ("show <commit>", "Show commit details with diff"),
    ],
    "Git Write": [
        ("branch", "List local branches"),
        ("branch -r", "List remote branches"),
        ("branch switch <name>", "Switch to branch"),
        ("branch create <name> [from]", "Create and switch to new branch"),
        ("branch delete <name> [--force]", "Delete branch"),
        ("branch rename <old> <new>", "Rename branch"),
        ("add <file> | add .", "Stage files"),
        ("unstage <file>", "Unstage files"),
        ('commit -m "message"', "Create commit"),
        ("stash [pop|list]", "Stash management"),
    ],
    "Pr": [
        ("pr list [--state open|closed|all]", "List pull requests"),
        ("pr show <number>", "Show PR details"),
        ("pr diff <number>", "Show PR diff with syntax highlighting"),
        ("pr checks <number>", "Show CI/CD check status"),
        ("pr checkout <number>", "Check out PR branch locally"),
        ("pr review <number> [--agents ...]", "Run code review on PR"),
        ("pr mine", "List your open PRs"),
        ("pr assigned [--limit N]", "List PRs where you are a reviewer"),
        ("pr stale [--days N]", "List stale PRs (no activity)"),
        ("pr ready [--limit N]", "List PRs ready to merge"),
        ("pr conflicts [--limit N]", "List PRs with merge conflicts"),
        ("pr summary [--full]", "PR dashboard (--full for detailed counts)"),
        ("pr unresolved [--limit N]", "List PRs with unresolved feedback"),
    ],
    "Review": [
        ("review [staged|HEAD~N|branch|file]", "Run code review on diff"),
        ("review --agents <list>", "Review with specific agents"),
        ("review --format json", "Review with JSON output"),
    ],
    "Config": [
        ("config", "Show all configuration (grouped, secrets masked)"),
        ("config get <key>", "Show a single config value"),
        ("config set <key> <value>", "Set config for this session"),
        ("config save", "Persist session config to .env"),
        ("config reset", "Reload config from .env"),
        ("config validate", "Check config for errors"),
    ],
    "Usage": [
        ("usage", "Show session usage summary (tokens, cost, calls)"),
    ],
    "Meta": [
        ("help [command|group]", "Show help"),
        ("agents", "List available review agents"),
        ("version", "Show version"),
        ("clear", "Clear screen"),
        ("!<command>", "Run shell command"),
        ("exit / Ctrl+D", "Exit interactive mode"),
    ],
}


def cmd_help(args: list[str], session: SessionState) -> None:
    """Show help for all commands or a specific group."""
    if args:
        group_name = args[0].capitalize()
        if group_name in COMMAND_HELP:
            _print_group(group_name, COMMAND_HELP[group_name])
            return
        # Search for command by name
        for _group, commands in COMMAND_HELP.items():
            for cmd_name, desc in commands:
                if cmd_name.startswith(args[0]):
                    console.print(f"  [bold]{cmd_name}[/bold]  {desc}")
                    return
        console.print(f"[red]Unknown command or group: {args[0]}[/red]")
        return

    for group, commands in COMMAND_HELP.items():
        _print_group(group, commands)
    console.print()


def _print_group(name: str, commands: list[tuple[str, str]]) -> None:
    """Print a command group as a Rich table."""
    table = Table(
        title=name,
        show_header=False,
        show_edge=False,
        box=None,
        padding=(0, 2),
    )
    table.add_column("Command", style="bold cyan", width=40)
    table.add_column("Description")
    for cmd_name, desc in commands:
        table.add_row(cmd_name, desc)
    console.print(table)


def cmd_agents(args: list[str], session: SessionState) -> None:
    """List available review agents."""
    table = Table(title="Available Agents", show_lines=False)
    table.add_column("Name", style="bold")
    table.add_column("Description")
    for name in ALL_AGENT_NAMES:
        table.add_row(name, f"Specialized {name} reviewer")
    console.print(table)


def cmd_version(args: list[str], session: SessionState) -> None:
    """Show version info."""
    console.print(f"code-review-agent {_VERSION}")


def cmd_clear(args: list[str], session: SessionState) -> None:
    """Clear the screen."""
    console.clear()


def cmd_shell(args: list[str], session: SessionState) -> None:
    """Run an arbitrary shell command."""
    if not args:
        console.print("[red]Usage: !<command>[/red]")
        return
    cmd = " ".join(args)
    try:
        result = subprocess.run(  # noqa: S602
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout:
            console.print(result.stdout.rstrip())
        if result.stderr:
            console.print(f"[red]{result.stderr.rstrip()}[/red]")
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out (30s limit)[/red]")
