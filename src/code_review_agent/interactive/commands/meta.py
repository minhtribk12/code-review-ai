"""Meta commands: help, agents, version, history, clear, exit."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from code_review_agent.agents import AGENT_REGISTRY, ALL_AGENT_NAMES, CUSTOM_AGENT_NAMES

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

_VERSION = "0.1.2"

# Command registry: (name, description) grouped by category.
COMMAND_HELP: dict[str, list[tuple[str, str]]] = {
    "Git Read": [
        ("status", "Show git status (branch, changed files)"),
        ("diff [staged|file|ref..ref]", "Show git diff"),
        ("log [-n N] [--graph] [branch...]", "Show git log (compact or graph)"),
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
        ("cd <path>", "Change working directory (Tab for path completion)"),
    ],
    "Pr Read": [
        ("pr list [--state open|closed|all]", "List pull requests"),
        ("pr show <number>", "Show PR details"),
        ("pr diff <number>", "Show PR diff with syntax highlighting"),
        ("pr checks <number>", "Show CI/CD check status"),
        ("pr checkout <number>", "Check out PR branch locally"),
        ("pr review <number> [--agents ...]", "Run code review on PR (auto-stashes)"),
        ("pr mine", "List your open PRs"),
        ("pr assigned [--limit N]", "List PRs where you are a reviewer"),
        ("pr stale [--days N]", "List stale PRs (no activity)"),
        ("pr ready [--limit N]", "List PRs ready to merge"),
        ("pr conflicts [--limit N]", "List PRs with merge conflicts"),
        ("pr summary [--full]", "PR dashboard (--full for detailed counts)"),
        ("pr unresolved [--limit N]", "List PRs with unresolved feedback"),
    ],
    "Pr Write": [
        ("pr create [--title T] [--fill] [--base B]", "Create PR (--fill from commits)"),
        ("pr create --draft --dry-run", "Preview draft PR without creating"),
        ("pr merge <N> [--strategy squash|merge|rebase]", "Merge PR with pre-flight checks"),
        ("pr approve <N> [-m comment]", "Approve a PR"),
        ('pr request-changes <N> -m "reason"', "Request changes on a PR"),
    ],
    "Repo": [
        ("repo list [--limit N]", "List accessible repositories"),
        ("repo select [owner/repo]", "Interactive repo picker (or direct)"),
        ("repo current", "Show current active repo"),
        ("repo clear", "Clear selection (use local git remote)"),
    ],
    "Review": [
        ("review [staged|HEAD~N|branch|file]", "Run code review on diff (auto-stages)"),
        ("review --agents <list>", "Review with specific agents"),
        ("review --format json", "Review with JSON output"),
    ],
    "Findings": [
        ("findings", "Interactive findings navigator (last review)"),
        ("findings <review_id>", "Navigate findings from a saved review"),
    ],
    "Watch": [
        ("watch [--interval N] [--agents list]", "Continuous monitoring (Ctrl+C to stop)"),
    ],
    "Config": [
        ("config", "Show all configuration (grouped, secrets masked)"),
        ("config edit", "Interactive config editor (full-screen)"),
        ("config get <key>", "Show a single config value"),
        ("config set <key> <value>", "Set config for this session"),
        ("config save", "Persist session config to database"),
        ("config reset", "Reload config from .env"),
        ("config validate", "Check config for errors"),
    ],
    "Provider": [
        ("provider", "Interactive provider/model browser (alias: pv)"),
        ("provider add", "Add a custom LLM provider (alias: pv add)"),
        ("provider list", "Table view of all providers"),
        ("provider models <name>", "List models for a provider"),
        ("provider remove <name>", "Remove a user-defined provider"),
    ],
    "History": [
        ("history [--repo R] [--days N]", "List past reviews"),
        ("history show <id>", "Show full review detail"),
        ("history trends [--days N]", "Aggregated trends and stats"),
        ("history export", "Export review history as JSON"),
    ],
    "Usage": [
        ("usage", "Show session usage summary (tokens, cost, calls)"),
    ],
    "Meta": [
        ("help [command|group]", "Show help"),
        ("agents", "List available review agents"),
        ("Ctrl+A", "Quick agent selector (persisted to database)"),
        ("Ctrl+P", "Quick provider selector (persisted to database)"),
        ("Ctrl+O", "Quick repo selector (interactive picker)"),
        ("Ctrl+L", "Git graph navigator (interactive)"),
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
    table.add_column("Type", style="dim")
    table.add_column("Pri", justify="right")
    table.add_column("Description")
    for name in ALL_AGENT_NAMES:
        is_custom = name in CUSTOM_AGENT_NAMES
        agent_cls = AGENT_REGISTRY.get(name)
        label = "[custom]" if is_custom else "[built-in]"
        priority = str(getattr(agent_cls, "priority", "?"))
        description = getattr(agent_cls, "_custom_description", "") if is_custom else ""
        if not description:
            description = f"Specialized {name} reviewer"
        table.add_row(name, label, priority, description)
    console.print(table)


def cmd_version(args: list[str], session: SessionState) -> None:
    """Show version info."""
    console.print(f"code-review-ai {_VERSION}")


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
