"""Review command: run code review from the interactive REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from code_review_agent.interactive import git_ops
from code_review_agent.llm_client import LLMClient
from code_review_agent.main import _parse_unified_diff
from code_review_agent.models import OutputFormat, ReviewInput
from code_review_agent.orchestrator import Orchestrator
from code_review_agent.progress import create_progress_callback
from code_review_agent.report import render_report_json, render_report_rich
from code_review_agent.token_budget import default_agents_for_tier

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def cmd_review(args: list[str], session: SessionState) -> None:
    """Run code review on a diff from the current git context."""
    settings = session.settings
    output_format = OutputFormat.RICH
    agent_names: list[str] | None = None

    # Parse flags
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--agents" and i + 1 < len(args):
            agent_names = [n.strip() for n in args[i + 1].split(",") if n.strip()]
            i += 2
        elif args[i] == "--format" and i + 1 < len(args):
            output_format = OutputFormat(args[i + 1])
            i += 2
        else:
            positional.append(args[i])
            i += 1

    # Get diff based on positional args
    raw_diff = _resolve_diff(positional)
    if raw_diff is None:
        return

    if not raw_diff.strip():
        console.print("[dim]No differences to review.[/dim]")
        return

    diff_files = _parse_unified_diff(raw_diff=raw_diff)
    if not diff_files:
        console.print("[dim]No parseable diff content.[/dim]")
        return

    review_input = ReviewInput(diff_files=diff_files)

    selected_names = agent_names or default_agents_for_tier(settings.token_tier)
    is_quiet = output_format == OutputFormat.JSON

    callback, display = create_progress_callback(
        agent_names=selected_names,
        is_quiet=is_quiet,
    )

    try:
        llm_client = LLMClient(settings=settings)
        orchestrator = Orchestrator(settings=settings, llm_client=llm_client, on_event=callback)

        if display is not None:
            display.start()
        try:
            report = orchestrator.run(review_input=review_input, agent_names=agent_names)
        finally:
            if display is not None:
                display.stop()

        session.reviews_completed += 1

        if output_format == OutputFormat.JSON:
            console.print(render_report_json(report))
        else:
            render_report_rich(report)

    except Exception as exc:
        console.print(f"[red]Review failed: {exc}[/red]")


def _resolve_diff(positional: list[str]) -> str | None:
    """Resolve diff content from positional args."""
    if not positional:
        # Default: unstaged diff
        return git_ops.diff()

    target = positional[0]

    if target == "staged":
        return git_ops.diff(staged=True)

    if target.startswith("HEAD~") or target.startswith("HEAD^"):
        return git_ops.diff_ref(target)

    if ".." in target:
        parts = target.split("..", 1)
        return git_ops.diff_between(parts[0], parts[1])

    # Assume it's a file path
    try:
        return git_ops.diff(file_path=target)
    except git_ops.GitError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
