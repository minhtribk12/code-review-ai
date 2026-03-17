"""Git read commands: status, diff, log, show."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

# Colors for branch graph lines, cycled by column position
_GRAPH_COLORS = ["green", "yellow", "blue", "magenta", "cyan", "red"]

# Regex patterns for parsing git log --graph output
_RE_HASH = re.compile(r"([0-9a-f]{7,12})\b")
_RE_REFS = re.compile(r"\(([^)]+)\)")
_RE_META = re.compile(r"\(([^)]*ago[^)]*)\)$")


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
    """Show git log in compact or graph format."""
    count = 20
    is_graph = False
    branches: list[str] = []

    i = 0
    while i < len(args):
        if args[i] == "--graph":
            is_graph = True
            i += 1
        elif args[i] == "-n" and i + 1 < len(args):
            try:
                count = int(args[i + 1])
            except ValueError:
                console.print(f"[red]Invalid count: {args[i + 1]}[/red]")
                return
            i += 2
        else:
            branches.append(args[i])
            i += 1

    if is_graph:
        from code_review_agent.interactive.commands.graph_nav import run_graph_app

        run_graph_app(count=count, branches=branches or None)
    else:
        branch = branches[0] if branches else None
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


# ---------------------------------------------------------------------------
# Graph log rendering
# ---------------------------------------------------------------------------


def _render_graph_log(raw: str, count: int) -> None:
    """Render git graph output with Rich colorization."""
    lines = raw.strip().splitlines()
    text = Text()

    for line in lines:
        _colorize_graph_line(text, line)
        text.append("\n")

    title = f"git log --graph (last {count} commits)"
    console.print(Panel(text, title=title, border_style="blue"))


def _colorize_graph_line(text: Text, line: str) -> None:
    """Colorize a single graph log line.

    Parses the graph prefix (|, *, /, \\) and colorizes by column,
    then styles the hash, refs, subject, and metadata.
    """
    if not line:
        return

    # Split into graph prefix and content
    graph_end = 0
    for idx, ch in enumerate(line):
        if ch in ("*", "|", "/", "\\", " ", "_"):
            graph_end = idx + 1
        else:
            break

    graph_part = line[:graph_end]
    content_part = line[graph_end:]

    # Colorize graph characters by column position
    col = 0
    for ch in graph_part:
        if ch in ("*", "|", "/", "\\", "_"):
            color = _GRAPH_COLORS[col % len(_GRAPH_COLORS)]
            style = f"bold {color}" if ch == "*" else color
            text.append(ch, style=style)
            col += 1
        else:
            text.append(ch)

    if not content_part:
        return

    # Parse content: hash, refs, subject, metadata
    _colorize_content(text, content_part)


def _colorize_content(text: Text, content: str) -> None:
    """Colorize the non-graph part of a log line.

    Expected format: <hash> [(<refs>)] <subject> [(<time ago, author>)]
    """
    remaining = content

    # Extract hash (first 7-12 char hex word)
    hash_match = _RE_HASH.match(remaining)
    if hash_match:
        text.append(hash_match.group(1), style="bold yellow")
        remaining = remaining[hash_match.end() :]
    else:
        text.append(remaining)
        return

    # Extract refs like (HEAD -> main, origin/main)
    if remaining.lstrip().startswith("("):
        stripped = remaining.lstrip()
        ref_match = _RE_REFS.match(stripped)
        if ref_match:
            space_prefix = remaining[: len(remaining) - len(stripped)]
            text.append(space_prefix)
            _colorize_refs(text, ref_match.group(0))
            remaining = stripped[ref_match.end() :]

    # Extract trailing metadata like (2 hours ago, Alice)
    meta_match = _RE_META.search(remaining)
    if meta_match:
        subject = remaining[: meta_match.start()]
        metadata = meta_match.group(0)
        text.append(subject)
        text.append(f" {metadata}", style="dim")
    else:
        text.append(remaining)


def _colorize_refs(text: Text, refs_str: str) -> None:
    """Colorize a refs string like (HEAD -> main, origin/main, tag: v1.0)."""
    # Remove outer parens
    inner = refs_str[1:-1]
    text.append("(", style="bold yellow")

    parts = [p.strip() for p in inner.split(",")]
    for i, part in enumerate(parts):
        if i > 0:
            text.append(", ", style="dim")

        if part.startswith("HEAD"):
            text.append(part, style="bold cyan")
        elif part.startswith("tag:"):
            text.append(part, style="bold magenta")
        elif "/" in part:
            # Remote branch (origin/main)
            text.append(part, style="bold red")
        else:
            # Local branch
            text.append(part, style="bold green")

    text.append(")", style="bold yellow")
