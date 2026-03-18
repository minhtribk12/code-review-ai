from __future__ import annotations

from pathlib import Path  # noqa: TC003 - Typer needs Path at runtime

import structlog
import typer

from code_review_agent.agents import AGENT_REGISTRY, register_custom_agents
from code_review_agent.config import Settings
from code_review_agent.github_client import fetch_pr_diff, parse_pr_reference
from code_review_agent.llm_client import LLMClient
from code_review_agent.models import DiffFile, DiffStatus, OutputFormat, ReviewInput
from code_review_agent.orchestrator import Orchestrator
from code_review_agent.progress import create_progress_callback
from code_review_agent.report import render_report_rich, save_report
from code_review_agent.token_budget import default_agents_for_tier

logger = structlog.get_logger(__name__)

app = typer.Typer(
    name="code-review-ai",
    help="Multi-agent code review powered by Nemotron 3 Super",
)

_VERSION = "0.1.4"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"code-review-ai {_VERSION}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging.",
    ),
) -> None:
    """Multi-agent code review powered by Nemotron 3 Super."""
    import logging

    from code_review_agent.config import Settings

    try:
        settings = Settings()
    except Exception:
        settings = None

    if verbose:
        level_name = "DEBUG"
    elif settings is not None:
        level_name = settings.log_level.value
    else:
        level_name = "INFO"
    level = getattr(logging, level_name)
    from typing import TextIO, cast

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(
            file=cast("TextIO", _StderrProxy()),
        ),
    )


class _StderrProxy:
    """Proxy that always resolves to the current sys.stderr at write time.

    Needed because structlog.PrintLoggerFactory captures the file object once.
    During testing, pytest replaces sys.stderr, so a captured reference goes stale.

    When ``suppress_background`` is True, output from non-main threads is
    silently discarded to prevent log messages from background review
    workers from corrupting the prompt_toolkit terminal display.
    """

    suppress_background: bool = False

    def write(self, data: str) -> int:
        import sys
        import threading

        if self.suppress_background and threading.current_thread() is not threading.main_thread():
            return len(data)
        return sys.stderr.write(data)

    def flush(self) -> None:
        import sys

        sys.stderr.flush()


@app.command()
def review(
    pr: str | None = typer.Option(
        None,
        "--pr",
        help="GitHub PR reference: owner/repo#number or full URL.",
    ),
    diff: Path | None = typer.Option(
        None,
        "--diff",
        help="Path to a local diff file.",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Path to save the markdown report.",
    ),
    agents: str | None = typer.Option(
        None,
        "--agents",
        help=(
            "Comma-separated agent names to run. "
            "Use 'cra agents' for the full list. "
            "Default: tier-based selection."
        ),
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress progress display (useful for CI/piping).",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.RICH,
        "--format",
        help="Output format: rich (terminal) or json (machine-readable).",
    ),
    navigate: bool = typer.Option(
        False,
        "--findings",
        help="Open interactive findings navigator after review completes.",
    ),
) -> None:
    """Run multi-agent code review on a GitHub PR or local diff."""
    if pr is None and diff is None:
        typer.echo("Error: provide either --pr or --diff", err=True)
        raise typer.Exit(code=1)
    if pr is not None and diff is not None:
        typer.echo("Error: provide only one of --pr or --diff, not both", err=True)
        raise typer.Exit(code=1)

    # JSON mode auto-suppresses progress (stdout is for JSON)
    is_quiet = quiet or output_format == OutputFormat.JSON

    try:
        settings = _load_settings()
        register_custom_agents(settings)
        review_input = _build_review_input(pr=pr, diff=diff, settings=settings)

        agent_names = _parse_agent_names(agents)
        selected_names = agent_names or default_agents_for_tier(settings.token_tier)

        callback, display = create_progress_callback(
            agent_names=selected_names,
            is_quiet=is_quiet,
            db_path=settings.history_db_path,
            usage_window=settings.usage_window,
        )

        llm_client = LLMClient(settings=settings)
        orchestrator = Orchestrator(settings=settings, llm_client=llm_client, on_event=callback)

        if display is not None:
            display.start()

        from rich.console import Console as RichConsole

        from code_review_agent.cancel_prompt import run_with_cancel_support

        cli_console = RichConsole(stderr=True)
        report = run_with_cancel_support(
            orchestrator,
            review_input,
            agent_names,
            display,
            cli_console,
        )

        if display is not None and not display.is_cancelled:
            display.stop()

        if report is None:
            raise typer.Exit(code=130)

        if output_format == OutputFormat.JSON:
            typer.echo(report.model_dump_json(indent=2))
        else:
            render_report_rich(report=report)

        if output is not None:
            save_report(report=report, path=output, output_format=output_format)
            typer.echo(f"Report saved to {output}")

        if navigate and output_format != OutputFormat.JSON:
            _launch_findings_navigator(report=report, settings=settings)

    except Exception as exc:
        logger.error("review failed", error=str(exc))
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command()
def findings(
    review_id: int = typer.Argument(
        ...,
        help="Review ID from history to navigate.",
    ),
) -> None:
    """Open interactive findings navigator for a saved review."""
    from code_review_agent.interactive.commands.findings_cmd import (
        _rows_from_db,
        run_findings_app,
    )
    from code_review_agent.storage import ReviewStorage

    try:
        settings = _load_settings()
        register_custom_agents(settings)
    except SystemExit:
        raise
    except Exception as exc:
        typer.echo(f"Error loading settings: {exc}", err=True)
        raise typer.Exit(code=1) from None

    storage = ReviewStorage(settings.history_db_path)
    db_rows = storage.load_findings_for_review(review_id)
    if not db_rows:
        typer.echo(f"Error: review #{review_id} not found or has no findings", err=True)
        raise typer.Exit(code=1)

    rows = _rows_from_db(db_rows)

    token: str | None = None
    if settings.github_token is not None:
        token = settings.github_token.get_secret_value()

    run_findings_app(rows=rows, github_token=token, storage=storage)


@app.command()
def interactive() -> None:
    """Launch interactive TUI mode with REPL prompt."""
    from code_review_agent.interactive.repl import run_repl

    try:
        settings = _load_settings_lenient()
        register_custom_agents(settings)
    except SystemExit:
        raise
    except Exception as exc:
        typer.echo(f"Error loading settings: {exc}", err=True)
        raise typer.Exit(code=1) from None

    run_repl(settings=settings)


@app.command(hidden=True)
def tui() -> None:
    """Launch tabbed TUI mode with Textual interface (experimental)."""
    typer.echo(
        "The tabbed TUI is currently disabled while under development.\n"
        "Use 'code-review-ai interactive' for the REPL interface instead.",
        err=True,
    )
    raise typer.Exit(code=1)


def _launch_findings_navigator(
    *,
    report: object,
    settings: Settings,
) -> None:
    """Launch the full-screen findings navigator for a ReviewReport."""
    from code_review_agent.interactive.commands.findings_cmd import run_findings_app
    from code_review_agent.storage import ReviewStorage

    token: str | None = None
    if settings.github_token is not None:
        token = settings.github_token.get_secret_value()

    storage = ReviewStorage(settings.history_db_path)
    run_findings_app(
        report=report,  # type: ignore[arg-type]
        github_token=token,
        storage=storage,
    )


def _parse_agent_names(agents_arg: str | None) -> list[str] | None:
    """Parse the --agents CLI flag into a list of agent names.

    Returns ``None`` for the default (all agents).
    Uses the live ``AGENT_REGISTRY`` which includes custom agents.
    """
    if agents_arg is None:
        return None

    names = [name.strip() for name in agents_arg.split(",") if name.strip()]
    if not names:
        return None

    available = list(AGENT_REGISTRY.keys())
    invalid = [n for n in names if n not in AGENT_REGISTRY]
    if invalid:
        typer.echo(
            f"Error: unknown agent(s): {', '.join(invalid)}. Available: {', '.join(available)}",
            err=True,
        )
        raise typer.Exit(code=1)

    return names


def _load_settings_lenient() -> Settings:
    """Load settings without requiring an API key.

    Used by the interactive command so the REPL can start and show
    the key setup panel when no key is configured.
    """
    import os

    # Temporarily set a placeholder key for the default provider
    # so Settings validation passes. The REPL startup will check
    # for real keys and prompt the user if needed.
    provider = os.environ.get("LLM_PROVIDER", "nvidia")
    env_key = f"{provider.upper()}_API_KEY"
    had_key = os.environ.get(env_key)
    if not had_key:
        os.environ[env_key] = "__placeholder__"  # pragma: allowlist secret
    try:
        return Settings()
    except Exception as exc:
        error_str = str(exc)
        if "api_key" not in error_str.lower():
            raise
        # Even with placeholder, some other validation failed
        raise SystemExit(f"Configuration error: {exc}") from None
    finally:
        if not had_key and os.environ.get(env_key) == "__placeholder__":
            del os.environ[env_key]


def _load_settings() -> Settings:
    """Load settings with a user-friendly error on missing configuration."""
    try:
        return Settings()
    except Exception as exc:
        error_str = str(exc)
        if "api_key" in error_str.lower():
            msg = (
                "An API key is required for your configured provider.\n"
                "  Set NVIDIA_API_KEY or OPENROUTER_API_KEY in .env"
                " or as an environment variable.\n"
                "  Run: cp .env.example .env\n"
                "  Then edit .env and add your API key."
            )
            raise SystemExit(msg) from None
        raise


def _build_review_input(
    *,
    pr: str | None,
    diff: Path | None,
    settings: Settings,
) -> ReviewInput:
    if pr is not None:
        owner, repo, pr_number = parse_pr_reference(pr_ref=pr)
        token = (
            settings.github_token.get_secret_value() if settings.github_token is not None else None
        )
        return fetch_pr_diff(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
            max_files=settings.max_pr_files,
            rate_limit_warn_threshold=settings.github_rate_limit_warn_threshold,
        )

    if diff is not None:
        raw = diff.read_text(encoding="utf-8")
        diff_files = _parse_unified_diff(raw_diff=raw)
        return ReviewInput(
            diff_files=diff_files,
            pr_url=None,
            pr_title=None,
            pr_description=None,
        )

    # Unreachable due to earlier validation, but satisfies type checker.
    msg = "either --pr or --diff must be provided"
    raise ValueError(msg)


def _parse_unified_diff(*, raw_diff: str) -> list[DiffFile]:
    """Parse a unified diff string into a list of DiffFile objects.

    Detects file status from git diff headers (``new file mode``,
    ``deleted file mode``, ``rename from``) and ``--- /dev/null`` /
    ``+++ /dev/null`` lines.  Filename is extracted from the ``+++ b/...``
    line when available, falling back to the ``diff --git`` header.
    """
    files: list[DiffFile] = []
    current_header_name: str | None = None
    current_lines: list[str] = []

    for line in raw_diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if current_header_name is not None:
                files.append(_build_diff_file(current_header_name, current_lines))
            parts = line.strip().split(" b/")
            current_header_name = parts[-1] if len(parts) > 1 else "unknown"
            current_lines = [line]
        elif current_header_name is not None:
            current_lines.append(line)

    if current_header_name is not None:
        files.append(_build_diff_file(current_header_name, current_lines))

    return files


def _build_diff_file(header_name: str, lines: list[str]) -> DiffFile:
    """Build a DiffFile from accumulated lines, detecting status and filename."""
    filename = header_name
    status = DiffStatus.MODIFIED

    for line in lines:
        stripped = line.strip()

        # Detect status from git diff header keywords
        if stripped.startswith("new file mode"):
            status = DiffStatus.ADDED
        elif stripped.startswith("deleted file mode"):
            status = DiffStatus.DELETED
        elif stripped.startswith("rename from"):
            status = DiffStatus.RENAMED

        # Detect status from --- /dev/null or +++ /dev/null
        if stripped == "--- /dev/null" and status == DiffStatus.MODIFIED:
            status = DiffStatus.ADDED
        if stripped == "+++ /dev/null" and status == DiffStatus.MODIFIED:
            status = DiffStatus.DELETED

        # Extract authoritative filename from +++ line
        if stripped.startswith("+++ b/"):
            filename = stripped[6:]
        elif stripped.startswith("+++ ") and stripped != "+++ /dev/null":
            filename = stripped[4:]

    return DiffFile(
        filename=filename,
        patch="".join(lines),
        status=status,
    )


if __name__ == "__main__":
    app()
