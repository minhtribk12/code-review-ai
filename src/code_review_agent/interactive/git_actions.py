"""Quick git actions from findings navigator.

Run git commands contextual to the current finding without leaving
the TUI. Supports status, diff, blame, and commit.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GitActionResult:
    """Result from a git action."""

    command: str
    output: str
    is_success: bool
    error: str = ""


def git_status() -> GitActionResult:
    """Run git status."""
    return _run_git("git", "status", "--short")


def git_diff_file(file_path: str) -> GitActionResult:
    """Run git diff on a specific file."""
    return _run_git("git", "diff", "--", file_path)


def git_blame_line(file_path: str, line_number: int) -> GitActionResult:
    """Run git blame for a specific line range."""
    start = max(1, line_number - 2)
    end = line_number + 2
    return _run_git("git", "blame", f"-L{start},{end}", "--", file_path)


def git_log_file(file_path: str, max_count: int = 5) -> GitActionResult:
    """Show recent commits for a specific file."""
    return _run_git(
        "git",
        "log",
        f"--max-count={max_count}",
        "--oneline",
        "--",
        file_path,
    )


def generate_fix_commit_message(
    title: str,
    file_path: str | None,
    line_number: int | None,
    agent_name: str,
) -> str:
    """Generate a commit message from a finding.

    Format: fix(agent): title (file:line)
    """
    scope = agent_name if agent_name else "review"
    short_title = title[:50].lower().rstrip(".")
    location = ""
    if file_path:
        short_file = file_path.split("/")[-1]
        location = f" ({short_file}"
        if line_number:
            location += f":{line_number}"
        location += ")"
    return f"fix({scope}): {short_title}{location}"


def git_stage_and_commit(file_path: str, message: str) -> GitActionResult:
    """Stage a file and commit with the given message."""
    stage_result = _run_git("git", "add", "--", file_path)
    if not stage_result.is_success:
        return stage_result
    return _run_git("git", "commit", "-m", message)


def _run_git(*args: str) -> GitActionResult:
    """Run a git command and return the result."""
    cmd = " ".join(args)
    try:
        result = subprocess.run(  # noqa: S603 - git commands with known args
            args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return GitActionResult(
                command=cmd,
                output=result.stdout,
                is_success=False,
                error=result.stderr.strip(),
            )
        return GitActionResult(
            command=cmd,
            output=result.stdout,
            is_success=True,
        )
    except subprocess.TimeoutExpired:
        return GitActionResult(
            command=cmd,
            output="",
            is_success=False,
            error="Command timed out",
        )
    except Exception as exc:
        return GitActionResult(
            command=cmd,
            output="",
            is_success=False,
            error=str(exc),
        )
