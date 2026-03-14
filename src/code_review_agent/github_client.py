from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from code_review_agent.models import DiffFile, DiffStatus, ReviewInput

logger = structlog.get_logger(__name__)

_PR_REF_PATTERN = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/pull/|#)"
    r"(?P<number>\d+)$"
)


def parse_pr_reference(pr_ref: str) -> tuple[str, str, int]:
    """Parse a PR reference string into (owner, repo, pr_number).

    Accepts formats:
      - owner/repo#123
      - https://github.com/owner/repo/pull/123
    """
    match = _PR_REF_PATTERN.match(pr_ref.strip())
    if match is None:
        msg = (
            f"Invalid PR reference: '{pr_ref}'. "
            "Expected format: owner/repo#number or https://github.com/owner/repo/pull/number"
        )
        raise ValueError(msg)

    owner = match.group("owner")
    repo = match.group("repo")
    pr_number = int(match.group("number"))

    logger.debug("parsed pr reference", owner=owner, repo=repo, pr_number=pr_number)
    return owner, repo, pr_number


def fetch_pr_diff(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
) -> ReviewInput:
    """Fetch PR diff and metadata from the GitHub API.

    Returns a ReviewInput populated with the diff files, PR URL, title, and
    description.
    """
    base_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    logger.info("fetching pr metadata", owner=owner, repo=repo, pr_number=pr_number)

    with httpx.Client(timeout=30.0) as client:
        # Fetch PR metadata.
        meta_resp = client.get(base_url, headers=headers)
        if meta_resp.status_code == 404:
            hint = (
                " If this is a private repo, set GITHUB_TOKEN in your .env file."
                if token is None
                else ""
            )
            msg = f"PR not found: {owner}/{repo}#{pr_number}.{hint}"
            raise ValueError(msg)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        pr_title: str = meta.get("title", "")
        pr_description: str = meta.get("body", "") or ""
        pr_url: str = meta.get("html_url", f"https://github.com/{owner}/{repo}/pull/{pr_number}")

        # Fetch PR files (which include patches).
        files_resp = client.get(f"{base_url}/files", headers=headers)
        files_resp.raise_for_status()
        files_data: list[dict[str, Any]] = files_resp.json()

    diff_files: list[DiffFile] = []
    for file_entry in files_data:
        patch = file_entry.get("patch", "")
        if not patch:
            continue
        diff_files.append(
            DiffFile(
                filename=file_entry["filename"],
                patch=patch,
                status=_map_github_status(file_entry.get("status", "modified")),
            )
        )

    logger.info("fetched pr diff", file_count=len(diff_files))

    return ReviewInput(
        diff_files=diff_files,
        pr_url=pr_url,
        pr_title=pr_title,
        pr_description=pr_description,
    )


_GITHUB_STATUS_MAP: dict[str, DiffStatus] = {
    "added": DiffStatus.ADDED,
    "modified": DiffStatus.MODIFIED,
    "removed": DiffStatus.DELETED,
    "renamed": DiffStatus.RENAMED,
    "copied": DiffStatus.ADDED,
    "changed": DiffStatus.MODIFIED,
}


def _map_github_status(github_status: str) -> DiffStatus:
    """Map GitHub API file status string to DiffStatus enum."""
    return _GITHUB_STATUS_MAP.get(github_status, DiffStatus.MODIFIED)
