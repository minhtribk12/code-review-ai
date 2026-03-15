from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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

_PAGE_RETRY_ATTEMPTS = 3

# Status codes that are safe to retry (transient server errors + rate limit).
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Client errors that indicate a permanent auth/permission problem.
_ABORT_STATUS_CODES = {401, 403}


class _PageFetchError(Exception):
    """Raised on retryable GitHub API errors during page fetching."""


class GitHubAuthError(Exception):
    """Raised when GitHub API returns 401/403 (auth or permission failure)."""


class GitHubRateLimitExhausted(Exception):
    """Raised when GitHub API rate limit hits 0."""


@dataclass(frozen=True)
class _PageResult:
    """Result from fetching a single page of PR files."""

    data: list[dict[str, Any]]
    response: httpx.Response


@dataclass
class GitHubRateLimitState:
    """Tracks GitHub API rate limit across requests.

    Updated from response headers after each API call. Provides proactive
    checking before requests and exhaustion detection.
    """

    remaining: int | None = None
    limit: int | None = None
    reset_at: float | None = None
    warn_threshold: int = 100

    def update_from_response(self, resp: httpx.Response) -> None:
        """Update state from GitHub API response headers."""
        remaining_str = resp.headers.get("X-RateLimit-Remaining")
        if remaining_str is not None:
            with contextlib.suppress(ValueError):
                self.remaining = int(remaining_str)

        limit_str = resp.headers.get("X-RateLimit-Limit")
        if limit_str is not None:
            with contextlib.suppress(ValueError):
                self.limit = int(limit_str)

        reset_str = resp.headers.get("X-RateLimit-Reset")
        if reset_str is not None:
            with contextlib.suppress(ValueError):
                self.reset_at = float(reset_str)

    @property
    def is_exhausted(self) -> bool:
        """Return True if rate limit is known to be exhausted.

        Returns False when remaining is unknown, positive, or when
        the reset time is in the past (limit likely already refreshed).
        """
        if self.remaining is None or self.remaining > 0:
            return False
        # remaining == 0: only exhausted if reset is in the future
        # (or unknown -- be conservative and treat as exhausted)
        return not (self.reset_at is not None and self.reset_at <= time.time())

    @property
    def is_low(self) -> bool:
        """Return True if rate limit is below the warning threshold."""
        if self.remaining is None:
            return False
        return self.remaining < self.warn_threshold

    @property
    def reset_at_utc(self) -> str | None:
        """Return reset time as human-readable UTC string."""
        if self.reset_at is None:
            return None
        return datetime.fromtimestamp(self.reset_at, tz=UTC).strftime("%H:%M UTC")

    def check_and_warn(self, warnings: list[str]) -> None:
        """Log warning and add to fetch_warnings if rate limit is low."""
        if self.remaining is None:
            return

        if self.is_exhausted:
            reset_str = self.reset_at_utc or "unknown"
            warning_msg = (
                f"GitHub API rate limit exhausted (0/{self.limit} remaining). "
                f"Resets at {reset_str}."
            )
            warnings.append(warning_msg)
            logger.error(
                "github api rate limit exhausted",
                remaining=0,
                limit=self.limit,
                reset_at=reset_str,
            )
        elif self.is_low:
            reset_str = self.reset_at_utc or "unknown"
            warning_msg = (
                f"GitHub API rate limit low: {self.remaining}/{self.limit} "
                f"remaining (resets at {reset_str})."
            )
            warnings.append(warning_msg)
            logger.warning(
                "github api rate limit low",
                remaining=self.remaining,
                limit=self.limit,
                reset_at=reset_str,
            )


# Type for the optional callback when rate limit is exhausted (TUI seam).
# Returns True to wait for reset, False to abort.
OnRateLimitExhausted = Callable[[GitHubRateLimitState], bool]


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
    rate_limit_warn_threshold: int = 100,
    on_rate_limit_exhausted: OnRateLimitExhausted | None = None,
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

    rate_limit = GitHubRateLimitState(warn_threshold=rate_limit_warn_threshold)

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
        rate_limit.update_from_response(meta_resp)
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
            rate_limit=rate_limit,
            on_rate_limit_exhausted=on_rate_limit_exhausted,
        )

    # Deduplicate files by filename (last wins).
    files_data = _deduplicate_files(files_data, warnings)

    # Add rate limit warning to fetch_warnings if low (not exhausted --
    # exhaustion is already handled inside _fetch_all_pr_files).
    if rate_limit.is_low and not rate_limit.is_exhausted:
        rate_limit.check_and_warn(warnings)

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
    rate_limit: GitHubRateLimitState,
    on_rate_limit_exhausted: OnRateLimitExhausted | None = None,
) -> list[dict[str, Any]]:
    """Paginate the GitHub PR files endpoint with retry and partial recovery.

    Fetches up to ``max_files`` files with ``per_page=100``.
    On transient page fetch failure (after retries), stops pagination and
    returns files collected so far. Auth errors (401/403) are propagated
    immediately. Rate limit exhaustion aborts with partial results (CLI)
    or invokes callback for user choice (TUI).
    """
    all_files: list[dict[str, Any]] = []
    page = 1

    while len(all_files) < max_files:
        # Proactive rate limit check before making the request.
        if rate_limit.is_exhausted:
            should_wait = on_rate_limit_exhausted is not None and on_rate_limit_exhausted(
                rate_limit
            )
            if should_wait and rate_limit.reset_at is not None:
                wait_seconds = max(0.0, rate_limit.reset_at - time.time() + 1)
                logger.info(
                    "waiting for github rate limit reset",
                    wait_seconds=round(wait_seconds, 0),
                )
                time.sleep(wait_seconds)
                # Reset state -- the limit should have refreshed
                rate_limit.remaining = None
            else:
                rate_limit.check_and_warn(warnings)
                logger.warning(
                    "aborting pr file fetch, rate limit exhausted",
                    files_collected=len(all_files),
                )
                break

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

        rate_limit.update_from_response(page_result.response)

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


# ---------------------------------------------------------------------------
# Interactive mode: higher-level GitHub API functions
# ---------------------------------------------------------------------------


def _make_github_headers(token: str | None) -> dict[str, str]:
    """Build standard GitHub API headers."""
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def list_prs(
    *,
    owner: str,
    repo: str,
    token: str | None,
    state: str = "open",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """List pull requests for a repository.

    Returns a list of PR dicts with keys: number, title, state, head_branch,
    base_branch, author, created_at, updated_at, draft, html_url.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = _make_github_headers(token)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            url,
            headers=headers,
            params={"state": state, "per_page": min(limit, 100), "sort": "updated"},
        )
        resp.raise_for_status()
        raw_prs: list[dict[str, Any]] = resp.json()

    return [
        {
            "number": pr["number"],
            "title": pr["title"],
            "state": pr["state"],
            "draft": pr.get("draft", False),
            "head_branch": pr.get("head", {}).get("ref", ""),
            "base_branch": pr.get("base", {}).get("ref", ""),
            "author": pr.get("user", {}).get("login", ""),
            "created_at": pr.get("created_at", ""),
            "updated_at": pr.get("updated_at", ""),
            "html_url": pr.get("html_url", ""),
        }
        for pr in raw_prs[:limit]
    ]


def get_pr_detail(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
) -> dict[str, Any]:
    """Get detailed PR information including labels, reviewers, and body."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = _make_github_headers(token)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        pr = resp.json()

    return {
        "number": pr["number"],
        "title": pr["title"],
        "body": pr.get("body") or "",
        "state": pr["state"],
        "draft": pr.get("draft", False),
        "head_branch": pr.get("head", {}).get("ref", ""),
        "base_branch": pr.get("base", {}).get("ref", ""),
        "author": pr.get("user", {}).get("login", ""),
        "labels": [label["name"] for label in pr.get("labels", [])],
        "reviewers": [r["login"] for r in pr.get("requested_reviewers", [])],
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "mergeable": pr.get("mergeable"),
        "html_url": pr.get("html_url", ""),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
    }


def get_pr_checks(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
) -> list[dict[str, str]]:
    """Get CI/CD check status for a PR's head commit."""
    # First get the head SHA
    pr_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = _make_github_headers(token)

    with httpx.Client(timeout=30.0) as client:
        pr_resp = client.get(pr_url, headers=headers)
        pr_resp.raise_for_status()
        head_sha = pr_resp.json().get("head", {}).get("sha", "")

        if not head_sha:
            return []

        checks_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
        checks_resp = client.get(checks_url, headers=headers)
        checks_resp.raise_for_status()
        runs: list[dict[str, Any]] = checks_resp.json().get("check_runs", [])

    return [
        {
            "name": run.get("name", ""),
            "status": run.get("status", ""),
            "conclusion": run.get("conclusion") or "pending",
        }
        for run in runs
    ]


def get_pr_reviews(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
) -> list[dict[str, str]]:
    """Get reviews for a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = _make_github_headers(token)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        reviews: list[dict[str, Any]] = resp.json()

    return [
        {
            "user": r.get("user", {}).get("login", ""),
            "state": r.get("state", ""),
            "submitted_at": r.get("submitted_at", ""),
        }
        for r in reviews
    ]


def get_authenticated_user(*, token: str) -> str:
    """Get the login name of the authenticated user."""
    headers = _make_github_headers(token)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get("https://api.github.com/user", headers=headers)
        resp.raise_for_status()
        login: str = resp.json().get("login", "")
        return login


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
