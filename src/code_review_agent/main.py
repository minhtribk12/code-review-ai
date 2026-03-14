from __future__ import annotations

from pathlib import Path  # noqa: TC003 - Typer needs Path at runtime

import structlog
import typer

from code_review_agent.agents import ALL_AGENT_NAMES
from code_review_agent.config import Settings
from code_review_agent.github_client import fetch_pr_diff, parse_pr_reference
from code_review_agent.llm_client import LLMClient
from code_review_agent.models import DiffFile, DiffStatus, ReviewInput
from code_review_agent.orchestrator import Orchestrator
from code_review_agent.report import render_report_rich, save_report

logger = structlog.get_logger(__name__)

app = typer.Typer(
    name="code-review-agent",
    help="Multi-agent code review powered by Nemotron 3 Super",
)

_VERSION = "0.1.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"code-review-agent {_VERSION}")
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

    level_name = "DEBUG" if verbose else "INFO"
    level = getattr(logging, level_name)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


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
            f"Available: {', '.join(ALL_AGENT_NAMES)}. "
            "Default: all agents."
        ),
    ),
) -> None:
    """Run multi-agent code review on a GitHub PR or local diff."""
    if pr is None and diff is None:
        typer.echo("Error: provide either --pr or --diff", err=True)
        raise typer.Exit(code=1)
    if pr is not None and diff is not None:
        typer.echo("Error: provide only one of --pr or --diff, not both", err=True)
        raise typer.Exit(code=1)

    try:
        settings = _load_settings()
        review_input = _build_review_input(pr=pr, diff=diff, settings=settings)

        agent_names = _parse_agent_names(agents)

        llm_client = LLMClient(settings=settings)
        orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
        report = orchestrator.run(review_input=review_input, agent_names=agent_names)

        render_report_rich(report=report)

        if output is not None:
            save_report(report=report, path=output)
            typer.echo(f"Report saved to {output}")

    except Exception as exc:
        logger.error("review failed", error=str(exc))
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


def _parse_agent_names(agents_arg: str | None) -> list[str] | None:
    """Parse the --agents CLI flag into a list of agent names.

    Returns ``None`` for the default (all agents).
    """
    if agents_arg is None:
        return None

    names = [name.strip() for name in agents_arg.split(",") if name.strip()]
    if not names:
        return None

    invalid = [n for n in names if n not in ALL_AGENT_NAMES]
    if invalid:
        typer.echo(
            f"Error: unknown agent(s): {', '.join(invalid)}. "
            f"Available: {', '.join(ALL_AGENT_NAMES)}",
            err=True,
        )
        raise typer.Exit(code=1)

    return names


def _load_settings() -> Settings:
    """Load settings with a user-friendly error on missing configuration."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:
        error_str = str(exc)
        if "llm_api_key" in error_str.lower():
            msg = (
                "LLM_API_KEY is required. Set it in .env or as an environment variable.\n"
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
