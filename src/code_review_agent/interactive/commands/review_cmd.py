"""Review command: run code review from the interactive REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from code_review_agent.cancel_prompt import run_with_cancel_support
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


def _parse_review_flags(args: list[str]) -> tuple[list[str], list[str] | None, OutputFormat]:
    """Parse --agents and --format flags from args.

    Returns (positional_args, agent_names, output_format).
    """
    positional: list[str] = []
    agent_names: list[str] | None = None
    output_format = OutputFormat.RICH

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

    return positional, agent_names, output_format


def cmd_review(args: list[str], session: SessionState) -> None:
    """Run code review on a diff from the current git context."""
    from code_review_agent.interactive.commands._helpers import warn_if_remote_repo

    warn_if_remote_repo(session)
    positional, agent_names, output_format = _parse_review_flags(args)

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
    label = _build_review_label(positional)
    _run_review_on_input(
        review_input,
        session,
        agent_names=agent_names,
        output_format=output_format,
        background=True,
        label=label,
    )


def _run_review_on_input(
    review_input: ReviewInput,
    session: SessionState,
    *,
    agent_names: list[str] | None = None,
    output_format: OutputFormat = OutputFormat.RICH,
    background: bool = False,
    label: str = "local diff",
) -> None:
    """Run the review pipeline on a prepared ReviewInput.

    When ``background=True`` (REPL mode), starts a non-blocking
    ``BackgroundReview`` and returns immediately. The REPL toolbar
    shows live progress; results are processed on the next Enter.

    When ``background=False`` (CLI mode), blocks until the review
    completes using ``ProgressDisplay`` + ``run_with_cancel_support``.
    """
    settings = session.effective_settings

    # Priority: explicit --agents > default_agents config > tier defaults
    if agent_names:
        selected_names = agent_names
    elif settings.default_agents:
        selected_names = [n.strip() for n in settings.default_agents.split(",") if n.strip()]
    else:
        selected_names = default_agents_for_tier(settings.token_tier)

    if background:
        if session.background_review is not None and session.background_review.is_running:
            console.print(
                "[red]A review is already running. "
                "Wait for it to finish or Ctrl+C to cancel.[/red]"
            )
            return

        from code_review_agent.interactive.background import BackgroundReview

        llm_client = LLMClient(settings=settings)
        bg = BackgroundReview(
            settings=settings,
            llm_client=llm_client,
            review_input=review_input,
            agent_names=selected_names,
            output_format=output_format,
            label=label,
        )
        session.background_review = bg
        # Give bg access to the prompt app so it can interrupt the prompt on done
        prompt_session = getattr(session, "_prompt_session", None)
        if prompt_session is not None:
            bg.set_prompt_app(prompt_session.app)
        bg.start()
        console.print(f"[dim]Review started ({label}). Prompt is active.[/dim]")
        return

    # Synchronous path (CLI mode)
    is_quiet = output_format == OutputFormat.JSON

    callback, display = create_progress_callback(
        agent_names=selected_names,
        is_quiet=is_quiet,
        db_path=settings.history_db_path,
        usage_window=settings.usage_window,
    )

    try:
        llm_client = LLMClient(settings=settings)
        orchestrator = Orchestrator(settings=settings, llm_client=llm_client, on_event=callback)

        if display is not None:
            display.start()

        report = run_with_cancel_support(
            orchestrator,
            review_input,
            selected_names,
            display,
            console,
        )

        if display is not None and not display.is_cancelled:
            display.stop()

        if report is None:
            return

        session.reviews_completed += 1
        session.last_review_report = report
        session.usage_history.record_review(report)
        if report.token_usage is not None:
            session.total_tokens_used += report.token_usage.total_tokens

        session.last_review_id = _auto_save_report(report, session)

        if output_format == OutputFormat.JSON:
            console.print(render_report_json(report))
        else:
            render_report_rich(report)

    except KeyboardInterrupt:
        console.print("[bold]Review cancelled.[/bold]")
    except Exception as exc:
        console.print(f"[red]Review failed: {exc}[/red]")


def _build_review_label(positional: list[str]) -> str:
    """Build a human-readable label for what is being reviewed."""
    if not positional:
        return "working tree"
    target = positional[0]
    if target == "staged":
        return "staged changes"
    return target


def _resolve_diff(positional: list[str]) -> str | None:
    """Resolve diff content from positional args.

    When called with no args: if there are unstaged changes but nothing staged,
    auto-stage all changes, capture the staged diff, then unstage in a finally
    block. This lets ``review`` with no args always review the working tree.
    """
    if not positional:
        raw = git_ops.diff()
        if raw.strip():
            return raw

        # No unstaged diff -- check if there are staged changes
        staged = git_ops.diff(staged=True)
        if staged.strip():
            return staged

        # No diff at all -- try auto-staging unstaged changes
        changed = git_ops.list_changed_files()
        if not changed:
            return ""

        # Auto-stage, capture diff, then unstage
        git_ops.add_files(".")
        try:
            return git_ops.diff(staged=True)
        finally:
            git_ops.unstage_files(".")

    target = positional[0]

    if target == "staged":
        return git_ops.diff(staged=True)

    if target.startswith("HEAD~") or target.startswith("HEAD^"):
        return git_ops.diff_ref(target)

    if ".." in target:
        parts = target.split("..", 1)
        return git_ops.diff_between(parts[0], parts[1])

    try:
        return git_ops.diff(file_path=target)
    except git_ops.GitError as exc:
        console.print(f"[red]{exc}[/red]")
        return None


def _auto_save_report(report: object, session: SessionState) -> int | None:
    """Save the review report to history storage. Returns the review ID.

    Fails silently -- storage errors should never block the review output.
    """
    try:
        from code_review_agent.storage import ReviewStorage

        settings = session.effective_settings
        if not settings.auto_save_history:
            return None

        storage = ReviewStorage(settings.history_db_path)
        return storage.save(
            report,  # type: ignore[arg-type]
            repo=session.active_repo,
            llm_model=settings.llm_model,
            token_tier=str(settings.token_tier),
            dedup_strategy=str(settings.dedup_strategy),
        )
    except Exception:
        import structlog

        structlog.get_logger(__name__).warning("auto-save failed", exc_info=True)
        return None
