"""Watch command: continuous file monitoring with auto-review."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console

from code_review_agent.interactive import git_ops
from code_review_agent.interactive.commands.review_cmd import (
    _parse_review_flags,
    _run_review_on_input,
)
from code_review_agent.main import _parse_unified_diff
from code_review_agent.models import ReviewInput

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def cmd_watch(args: list[str], session: SessionState) -> None:
    """Poll git status and auto-review on changes. Ctrl+C to stop."""
    interval = session.effective_settings.watch_debounce_seconds

    # Parse flags once up front
    for i, arg in enumerate(args):
        if arg == "--interval" and i + 1 < len(args):
            try:
                interval = float(args[i + 1])
            except ValueError:
                console.print(f"[red]Invalid interval: {args[i + 1]}[/red]")
                return

    _, agent_names, output_format = _parse_review_flags(args)

    console.print(f"  Watching for changes every {interval}s. Press Ctrl+C to stop.")

    previous_status = git_ops.status_porcelain()

    try:
        while True:
            time.sleep(interval)

            current_status = git_ops.status_porcelain()
            if current_status == previous_status:
                continue

            previous_status = current_status

            if not current_status.strip():
                console.print("[dim]Working tree is clean.[/dim]")
                continue

            changed_lines = current_status.strip().splitlines()
            console.print(f"\n  [bold]Changes detected ({len(changed_lines)} files):[/bold]")
            for line in changed_lines[:10]:
                console.print(f"    {line}")
            if len(changed_lines) > 10:
                console.print(f"    ... and {len(changed_lines) - 10} more")

            raw_diff = git_ops.diff()
            if not raw_diff.strip():
                raw_diff = git_ops.diff(staged=True)

            if not raw_diff.strip():
                console.print("[dim]No diff content to review.[/dim]")
                continue

            diff_files = _parse_unified_diff(raw_diff=raw_diff)
            if not diff_files:
                console.print("[dim]No parseable diff content.[/dim]")
                continue

            console.print("  Running review...")
            review_input = ReviewInput(diff_files=diff_files)
            _run_review_on_input(
                review_input,
                session,
                agent_names=agent_names,
                output_format=output_format,
            )

    except KeyboardInterrupt:
        console.print("\n  [dim]Watch stopped.[/dim]")
