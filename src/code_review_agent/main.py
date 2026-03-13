from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
import typer

from code_review_agent.config import Settings
from code_review_agent.github_client import fetch_pr_diff, parse_pr_reference
from code_review_agent.llm_client import LLMClient
from code_review_agent.models import DiffFile, ReviewInput
from code_review_agent.orchestrator import Orchestrator
from code_review_agent.report import render_report_rich, save_report

if TYPE_CHECKING:
    from pathlib import Path

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
) -> None:
    """Run multi-agent code review on a GitHub PR or local diff."""
    if pr is None and diff is None:
        typer.echo("Error: provide either --pr or --diff", err=True)
        raise typer.Exit(code=1)
    if pr is not None and diff is not None:
        typer.echo("Error: provide only one of --pr or --diff, not both", err=True)
        raise typer.Exit(code=1)

    try:
        settings = Settings()  # type: ignore[call-arg]
        review_input = _build_review_input(pr=pr, diff=diff, settings=settings)

        llm_client = LLMClient(settings=settings)
        orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
        report = orchestrator.run(review_input=review_input)

        render_report_rich(report=report)

        if output is not None:
            save_report(report=report, path=output)
            typer.echo(f"Report saved to {output}")

    except Exception as exc:
        logger.error("review failed", error=str(exc))
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


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
    """Parse a unified diff string into a list of DiffFile objects."""
    files: list[DiffFile] = []
    current_filename: str | None = None
    current_patch_lines: list[str] = []

    for line in raw_diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if current_filename is not None:
                files.append(
                    DiffFile(
                        filename=current_filename,
                        patch="".join(current_patch_lines),
                        status="modified",
                    )
                )
            parts = line.strip().split(" b/")
            current_filename = parts[-1] if len(parts) > 1 else "unknown"
            current_patch_lines = [line]
        elif current_filename is not None:
            current_patch_lines.append(line)

    if current_filename is not None:
        files.append(
            DiffFile(
                filename=current_filename,
                patch="".join(current_patch_lines),
                status="modified",
            )
        )

    return files


if __name__ == "__main__":
    app()
