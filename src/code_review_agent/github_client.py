from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from code_review_agent.models import DiffFile, DiffStatus, ReviewInput

logger = structlog.get_logger(__name__)

_PR_REF_PATTERN = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/pull/|#)"
    r"(?P<number>\d+)$"
)

_RATE_LIMIT_WARNING_THRESHOLD = 100
_PAGE_RETRY_ATTEMPTS = 3

# Server errors that are safe to retry (transient).
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}

# Client errors that indicate a permanent auth/permission problem.
# These should abort the entire fetch, not return partial results.
_ABORT_STATUS_CODES = {401, 403}


class _PageFetchError(Exception):
    """Raised on retryable GitHub API errors during page fetching."""


class GitHubAuthError(Exception):
    """Raised when GitHub API returns 401/403 (auth or permission failure)."""


@dataclass(frozen=True)
class _PageResult:
    """Result from fetching a single page of PR files."""

    data: list[dict[str, Any]]
    response: httpx.Response


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
            "Expected format: owner/repo#number or "
            "https://github.com/owner/repo/pull/number"
        )
        raise ValueError(msg)

    owner = match.group("owner")
    repo = match.group("repo")
    pr_number = int(match.group("number"))

    logger.debug(
        "parsed pr reference",
        owner=owner,
        repo=repo,
        pr_number=pr_number,
    )
    return owner, repo, pr_number


def fetch_pr_diff(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
    max_files: int = 200,
) -> ReviewInput:
    """Fetch PR diff and metadata from the GitHub API.

    Paginates the files endpoint (per_page=100) and stops after
    ``max_files`` files are collected. Returns partial results on
    transient page fetch failures instead of crashing.
    Auth errors (401/403) are always propagated.
    """
    base_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    logger.info(
        "fetching pr metadata",
        owner=owner,
        repo=repo,
        pr_number=pr_number,
    )

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
        _check_github_rate_limit(meta_resp)
        meta = meta_resp.json()

        pr_title: str = meta.get("title", "")
        pr_description: str = meta.get("body", "") or ""
        pr_url: str = meta.get(
            "html_url",
            f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        )

        # Fetch PR files with pagination.
        warnings: list[str] = []
        files_data = _fetch_all_pr_files(
            client=client,
            url=f"{base_url}/files",
            headers=headers,
            max_files=max_files,
            warnings=warnings,
        )

    # Deduplicate files by filename (last wins).
    files_data = _deduplicate_files(files_data, warnings)

    diff_files: list[DiffFile] = []
    skipped = 0
    for file_entry in files_data:
        patch = file_entry.get("patch", "")
        if not patch:
            skipped += 1
            continue
        diff_files.append(
            DiffFile(
                filename=file_entry["filename"],
                patch=patch,
                status=_map_github_status(file_entry.get("status", "modified")),
            )
        )

    if not diff_files and not files_data:
        warnings.append("No files could be fetched from the PR.")

    logger.info(
        "fetched pr diff",
        file_count=len(diff_files),
        skipped_no_patch=skipped,
        warning_count=len(warnings),
    )

    return ReviewInput(
        diff_files=diff_files,
        pr_url=pr_url,
        pr_title=pr_title,
        pr_description=pr_description,
        fetch_warnings=warnings,
    )


def _fetch_all_pr_files(
    *,
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    max_files: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Paginate the GitHub PR files endpoint with retry and partial recovery.

    Fetches up to ``max_files`` files with ``per_page=100``.
    On transient page fetch failure (after retries), stops pagination and
    returns files collected so far. Auth errors (401/403) are propagated
    immediately.
    """
    all_files: list[dict[str, Any]] = []
    page = 1

    while len(all_files) < max_files:
        try:
            page_result = _fetch_page(
                client=client,
                url=url,
                headers=headers,
                page=page,
            )
        except GitHubAuthError:
            raise
        except Exception as exc:
            warning_msg = (
                f"Failed to fetch page {page} of PR files after "
                f"{_PAGE_RETRY_ATTEMPTS} attempts: {exc}. "
                f"Continuing with {len(all_files)} files collected so far."
            )
            warnings.append(warning_msg)
            logger.warning(
                "page fetch failed, returning partial results",
                page=page,
                files_collected=len(all_files),
                error=str(exc),
            )
            break

        _check_github_rate_limit(page_result.response)

        if not page_result.data:
            break

        all_files.extend(page_result.data)
        page += 1

    if len(all_files) > max_files:
        logger.warning(
            "pr file count exceeds max, truncating",
            total_available=len(all_files),
            max_files=max_files,
        )
        all_files = all_files[:max_files]

    return all_files


@retry(
    retry=retry_if_exception_type((httpx.TransportError, _PageFetchError)),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(_PAGE_RETRY_ATTEMPTS),
    reraise=True,
)
def _fetch_page(
    *,
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    page: int,
) -> _PageResult:
    """Fetch a single page of PR files with retry on transient errors.

    Retries on: transport errors (timeouts, connection failures) and
    server errors (500, 502, 503, 504).
    Raises GitHubAuthError immediately on 401/403 (no retry).
    """
    resp = client.get(
        url,
        headers=headers,
        params={"per_page": 100, "page": page},
    )

    if resp.status_code in _ABORT_STATUS_CODES:
        msg = f"GitHub API returned {resp.status_code} for page {page}"
        raise GitHubAuthError(msg)

    if resp.status_code in _RETRYABLE_STATUS_CODES:
        raise _PageFetchError(f"GitHub API returned {resp.status_code} for page {page}")

    resp.raise_for_status()
    return _PageResult(data=resp.json(), response=resp)


def _deduplicate_files(files: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """Deduplicate files by filename (last occurrence wins).

    GitHub pagination can return duplicate filenames in rare edge cases
    when files are added/removed between page fetches.
    """
    seen: dict[str, dict[str, Any]] = {}
    for file_entry in files:
        seen[file_entry["filename"]] = file_entry

    removed_count = len(files) - len(seen)
    if removed_count > 0:
        logger.info(
            "deduplicated pr files by filename",
            original=len(files),
            deduplicated=len(seen),
            removed=removed_count,
        )
        warnings.append(f"Removed {removed_count} duplicate file(s) from PR file list.")

    return list(seen.values())


def _check_github_rate_limit(resp: httpx.Response) -> None:
    """Log a warning if GitHub API rate limit is running low."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        try:
            remaining_int = int(remaining)
        except ValueError:
            return
        if remaining_int < _RATE_LIMIT_WARNING_THRESHOLD:
            logger.warning(
                "github api rate limit low",
                remaining=remaining_int,
                limit=resp.headers.get("X-RateLimit-Limit"),
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
