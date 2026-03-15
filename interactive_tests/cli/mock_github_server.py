"""Mock GitHub API server for interactive TUI testing.

Responds to GitHub REST API endpoints used by PR write commands,
PR read commands, and workflow helpers. Returns realistic PR data
so the TUI can be tested end-to-end without a real GitHub account.

Endpoints:
    GET  /repos/{owner}/{repo}/pulls           -- list PRs
    POST /repos/{owner}/{repo}/pulls           -- create PR
    GET  /repos/{owner}/{repo}/pulls/{n}       -- get PR detail
    GET  /repos/{owner}/{repo}/pulls/{n}/files -- get PR files
    PUT  /repos/{owner}/{repo}/pulls/{n}/merge -- merge PR
    GET  /repos/{owner}/{repo}/pulls/{n}/reviews    -- get reviews
    POST /repos/{owner}/{repo}/pulls/{n}/reviews    -- submit review
    GET  /repos/{owner}/{repo}/commits/{sha}/check-runs -- CI checks
    GET  /user                                 -- authenticated user
    GET  /health                               -- health check
    GET  /stats                                -- request counters
    POST /reset                                -- reset counters

Run:
    uv run uvicorn interactive_tests.cli.mock_github_server:app --port 9998
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock GitHub API Server")

# Counters for test verification
_stats: dict[str, int] = {
    "prs_created": 0,
    "prs_merged": 0,
    "reviews_submitted": 0,
    "total_requests": 0,
}

# In-memory PR store (seeded with sample PRs)
_next_pr_number = 100

_SAMPLE_PRS: list[dict[str, Any]] = [
    {
        "number": 42,
        "title": "Fix SQL injection in login",
        "body": "Replaces f-string interpolation with parameterized queries.",
        "state": "open",
        "draft": False,
        "user": {"login": "octocat"},
        "head": {"ref": "fix/sql-injection", "sha": "aaa111bbb222"},
        "base": {"ref": "main"},
        "labels": [{"name": "security"}, {"name": "high-priority"}],
        "requested_reviewers": [{"login": "reviewer1"}],
        "additions": 15,
        "deletions": 3,
        "changed_files": 2,
        "mergeable": True,
        "html_url": "https://github.com/acme/app/pull/42",
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-10T14:30:00Z",
    },
    {
        "number": 43,
        "title": "Add caching layer",
        "body": "Adds LRU cache to config lookups for performance.",
        "state": "open",
        "draft": True,
        "user": {"login": "devuser"},
        "head": {"ref": "feat/caching", "sha": "bbb222ccc333"},
        "base": {"ref": "main"},
        "labels": [{"name": "performance"}],
        "requested_reviewers": [],
        "additions": 45,
        "deletions": 0,
        "changed_files": 3,
        "mergeable": True,
        "html_url": "https://github.com/acme/app/pull/43",
        "created_at": "2026-03-05T08:00:00Z",
        "updated_at": "2026-03-12T11:00:00Z",
    },
    {
        "number": 44,
        "title": "Refactor auth middleware",
        "body": "Splits monolithic auth into token validation and session management.",
        "state": "open",
        "draft": False,
        "user": {"login": "octocat"},
        "head": {
            "ref": "refactor/auth-middleware",
            "sha": "ccc333ddd444",
        },
        "base": {"ref": "main"},
        "labels": [],
        "requested_reviewers": [{"login": "reviewer1"}, {"login": "reviewer2"}],
        "additions": 120,
        "deletions": 85,
        "changed_files": 7,
        "mergeable": False,
        "html_url": "https://github.com/acme/app/pull/44",
        "created_at": "2026-02-20T09:00:00Z",
        "updated_at": "2026-02-25T16:00:00Z",
    },
]

_SAMPLE_REVIEWS: dict[int, list[dict[str, Any]]] = {
    42: [
        {
            "id": 1,
            "user": {"login": "reviewer1"},
            "state": "APPROVED",
            "submitted_at": "2026-03-09T10:00:00Z",
            "html_url": "https://github.com/acme/app/pull/42#pullrequestreview-1",
        },
    ],
    43: [],
    44: [
        {
            "id": 2,
            "user": {"login": "reviewer1"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-02-22T10:00:00Z",
            "html_url": "https://github.com/acme/app/pull/44#pullrequestreview-2",
        },
    ],
}

_SAMPLE_CHECKS: dict[str, list[dict[str, Any]]] = {
    "aaa111bbb222": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "completed", "conclusion": "success"},
        {"name": "security-scan", "status": "completed", "conclusion": "success"},
    ],
    "bbb222ccc333": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": "pending"},
    ],
    "ccc333ddd444": [
        {"name": "lint", "status": "completed", "conclusion": "failure"},
        {"name": "test", "status": "completed", "conclusion": "failure"},
    ],
}

_SAMPLE_FILES: list[dict[str, Any]] = [
    {
        "filename": "src/auth/login.py",
        "status": "modified",
        "additions": 10,
        "deletions": 3,
        "patch": (
            "@@ -10,6 +10,12 @@\n"
            " def authenticate(username: str, password: str) -> bool:\n"
            "-    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
            '+    query = "SELECT * FROM users WHERE name = %s"\n'
            "+    cursor.execute(query, (username,))\n"
        ),
    },
    {
        "filename": "src/utils/cache.py",
        "status": "added",
        "additions": 15,
        "deletions": 0,
        "patch": (
            "@@ -0,0 +1,15 @@\n"
            "+from functools import lru_cache\n"
            "+\n"
            "+@lru_cache(maxsize=256)\n"
            "+def get_config(key: str) -> str:\n"
            "+    return _load_config()[key]\n"
        ),
    },
]


def _find_pr(pr_number: int) -> dict[str, Any] | None:
    for pr in _SAMPLE_PRS:
        if pr["number"] == pr_number:
            return pr
    return None


def _add_rate_limit_headers(response: Response) -> None:
    response.headers["X-RateLimit-Remaining"] = "4990"
    response.headers["X-RateLimit-Limit"] = "5000"
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 3600)


# ---------------------------------------------------------------------------
# PR endpoints
# ---------------------------------------------------------------------------


@app.get("/repos/{owner}/{repo}/pulls")
def list_pulls(
    owner: str,
    repo: str,
    response: Response,
    state: str = "open",
    per_page: int = 30,
    sort: str = "updated",
) -> list[dict[str, Any]]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    return [pr for pr in _SAMPLE_PRS if pr["state"] == state][:per_page]


@app.post("/repos/{owner}/{repo}/pulls")
async def create_pull(
    owner: str, repo: str, request: Request, response: Response
) -> dict[str, Any]:
    global _next_pr_number
    _stats["total_requests"] += 1
    _stats["prs_created"] += 1
    _add_rate_limit_headers(response)

    body = await request.json()
    pr_number = _next_pr_number
    _next_pr_number += 1

    new_pr = {
        "number": pr_number,
        "title": body.get("title", ""),
        "body": body.get("body", ""),
        "state": "open",
        "draft": body.get("draft", False),
        "user": {"login": "testuser"},
        "head": {"ref": body.get("head", ""), "sha": uuid.uuid4().hex[:12]},
        "base": {"ref": body.get("base", "main")},
        "labels": [],
        "requested_reviewers": [],
        "additions": 0,
        "deletions": 0,
        "changed_files": 0,
        "mergeable": True,
        "html_url": f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        "created_at": "2026-03-15T00:00:00Z",
        "updated_at": "2026-03-15T00:00:00Z",
    }
    _SAMPLE_PRS.append(new_pr)
    response.status_code = 201
    return new_pr


@app.get("/repos/{owner}/{repo}/pulls/{pr_number}", response_model=None)
def get_pull(
    owner: str, repo: str, pr_number: int, response: Response
) -> dict[str, Any] | Response:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    pr = _find_pr(pr_number)
    if pr is None:
        return Response(status_code=404, content='{"message": "Not Found"}')
    return pr


@app.get("/repos/{owner}/{repo}/pulls/{pr_number}/files")
def get_pull_files(
    owner: str,
    repo: str,
    pr_number: int,
    response: Response,
    per_page: int = 100,
    page: int = 1,
) -> list[dict[str, Any]]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    if page > 1:
        return []
    return _SAMPLE_FILES


@app.put("/repos/{owner}/{repo}/pulls/{pr_number}/merge", response_model=None)
async def merge_pull(
    owner: str,
    repo: str,
    pr_number: int,
    request: Request,
    response: Response,
) -> dict[str, Any] | Response:
    _stats["total_requests"] += 1
    _stats["prs_merged"] += 1
    _add_rate_limit_headers(response)

    pr = _find_pr(pr_number)
    if pr is None:
        return Response(status_code=404, content='{"message": "Not Found"}')

    if pr.get("mergeable") is False:
        return Response(status_code=405, content='{"message": "Not mergeable"}')

    pr["state"] = "closed"
    return {
        "merged": True,
        "message": "Pull Request successfully merged",
        "sha": uuid.uuid4().hex[:12],
    }


@app.get("/repos/{owner}/{repo}/pulls/{pr_number}/reviews")
def get_reviews(owner: str, repo: str, pr_number: int, response: Response) -> list[dict[str, Any]]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    return _SAMPLE_REVIEWS.get(pr_number, [])


@app.post("/repos/{owner}/{repo}/pulls/{pr_number}/reviews", response_model=None)
async def submit_review(
    owner: str,
    repo: str,
    pr_number: int,
    request: Request,
    response: Response,
) -> dict[str, Any] | Response:
    _stats["total_requests"] += 1
    _stats["reviews_submitted"] += 1
    _add_rate_limit_headers(response)

    body = await request.json()
    event = body.get("event", "COMMENT")
    review_body = body.get("body", "")

    review = {
        "id": len(_SAMPLE_REVIEWS.get(pr_number, [])) + 100,
        "user": {"login": "testuser"},
        "state": event if event != "APPROVE" else "APPROVED",
        "submitted_at": "2026-03-15T00:00:00Z",
        "body": review_body,
        "html_url": f"https://github.com/{owner}/{repo}/pull/{pr_number}#pullrequestreview-new",
    }

    if pr_number not in _SAMPLE_REVIEWS:
        _SAMPLE_REVIEWS[pr_number] = []
    _SAMPLE_REVIEWS[pr_number].append(review)

    response.status_code = 200
    return review


@app.get("/repos/{owner}/{repo}/commits/{sha}/check-runs")
def get_check_runs(owner: str, repo: str, sha: str, response: Response) -> dict[str, Any]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    return {"check_runs": _SAMPLE_CHECKS.get(sha, [])}


@app.get("/user")
def get_user(response: Response) -> dict[str, str]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    return {"login": "testuser"}


@app.get("/user/repos")
def list_repos(
    response: Response,
    per_page: int = 30,
    sort: str = "updated",
    direction: str = "desc",
    affiliation: str = "owner,collaborator,organization_member",
) -> list[dict[str, Any]]:
    _stats["total_requests"] += 1
    _add_rate_limit_headers(response)
    return [
        {
            "full_name": "acme/app",
            "description": "Main web application",
            "private": False,
            "default_branch": "main",
            "updated_at": "2026-03-14T10:00:00Z",
            "open_issues_count": 5,
            "language": "Python",
        },
        {
            "full_name": "acme/api",
            "description": "REST API service",
            "private": True,
            "default_branch": "main",
            "updated_at": "2026-03-12T08:00:00Z",
            "open_issues_count": 12,
            "language": "Go",
        },
        {
            "full_name": "acme/docs",
            "description": "Documentation site",
            "private": False,
            "default_branch": "main",
            "updated_at": "2026-03-01T15:00:00Z",
            "open_issues_count": 0,
            "language": "MDX",
        },
    ][:per_page]


# ---------------------------------------------------------------------------
# Test infrastructure endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stats")
def stats() -> dict[str, int]:
    return _stats


@app.post("/reset")
def reset() -> dict[str, str]:
    global _next_pr_number
    for key in _stats:
        _stats[key] = 0
    _next_pr_number = 100
    # Reset sample PRs to initial state
    for pr in _SAMPLE_PRS:
        if pr["number"] in (42, 43, 44):
            pr["state"] = "open"
    # Remove any dynamically created PRs
    _SAMPLE_PRS[:] = [pr for pr in _SAMPLE_PRS if pr["number"] in (42, 43, 44)]
    return {"status": "reset"}
