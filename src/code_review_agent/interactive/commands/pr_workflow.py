"""PR workflow commands: mine, assigned, stale, ready, conflicts, summary, unresolved."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from code_review_agent.github_client import (
    get_authenticated_user,
    get_pr_checks,
    get_pr_detail,
    get_pr_reviews,
    list_prs,
)
from code_review_agent.interactive.commands.pr_read import _get_repo_info

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_authenticated_username(session: SessionState) -> str:
    """Get the authenticated GitHub username from token."""
    effective = session.effective_settings
    token = (
        effective.github_token.get_secret_value() if effective.github_token is not None else None
    )
    if token is None:
        msg = "GitHub token required for this command. Set GITHUB_TOKEN in .env."
        raise ValueError(msg)
    return get_authenticated_user(token=token)


def _fetch_open_prs(session: SessionState) -> tuple[str, str, str | None, list[dict[str, Any]]]:
    """Fetch open PRs with session-level caching.

    Returns (owner, repo, token, prs). Uses the PR cache on SessionState
    to avoid redundant list_prs calls within the same session.
    """
    owner, repo, token = _get_repo_info(session)
    state = "open"

    cached = session.pr_cache.get(owner, repo, state)
    if cached is not None:
        return owner, repo, token, cached

    prs = list_prs(owner=owner, repo=repo, token=token, state=state)
    session.pr_cache.set(owner, repo, state, prs)
    return owner, repo, token, prs


def _parse_limit(args: list[str], default: int = 30) -> tuple[list[str], int]:
    """Parse --limit flag from args. Returns (remaining_args, limit)."""
    remaining: list[str] = []
    limit = default
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                limit = default
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return remaining, limit


def _render_pr_table(
    title: str,
    prs: list[dict[str, Any]],
    extra_columns: list[tuple[str, str]] | None = None,
) -> None:
    """Render a list of PRs as a Rich table."""
    if not prs:
        console.print(f"[dim]No results for: {title}[/dim]")
        return

    table = Table(title=title, show_lines=False)
    table.add_column("#", style="bold", width=6)
    table.add_column("Title", width=40)
    table.add_column("Branch", width=25)
    table.add_column("Author", width=15)

    extra_keys: list[str] = []
    if extra_columns:
        for col_name, col_key in extra_columns:
            table.add_column(col_name, width=15)
            extra_keys.append(col_key)

    for pr in prs:
        row = [
            str(pr["number"]),
            pr["title"][:40],
            pr.get("head_branch", ""),
            pr.get("author", ""),
        ]
        for key in extra_keys:
            row.append(str(pr.get(key, "")))
        table.add_row(*row)

    console.print(table)


# ---------------------------------------------------------------------------
# Review state helpers
# ---------------------------------------------------------------------------


def _latest_review_per_user(reviews: list[dict[str, str]]) -> dict[str, str]:
    """Get the latest review state per user (by submission order)."""
    latest: dict[str, str] = {}
    for review in reviews:
        user = review.get("user", "")
        state = review.get("state", "")
        if user and state:
            latest[user] = state
    return latest


def _has_unresolved_changes(reviews: list[dict[str, str]]) -> bool:
    """Check if any reviewer's latest state is CHANGES_REQUESTED."""
    latest_by_user = _latest_review_per_user(reviews)
    return any(state == "CHANGES_REQUESTED" for state in latest_by_user.values())


def _changes_requested_by(reviews: list[dict[str, str]]) -> list[str]:
    """Return usernames whose latest review is CHANGES_REQUESTED."""
    latest_by_user = _latest_review_per_user(reviews)
    return [user for user, state in latest_by_user.items() if state == "CHANGES_REQUESTED"]


# ---------------------------------------------------------------------------
# Workflow commands
# ---------------------------------------------------------------------------


def pr_mine(args: list[str], session: SessionState) -> None:
    """List PRs authored by the authenticated user."""
    _owner, _repo, _token, prs = _fetch_open_prs(session)
    username = _get_authenticated_username(session)

    mine = [pr for pr in prs if pr["author"] == username]

    _render_pr_table(f"My Open PRs ({username})", mine)


def pr_assigned(args: list[str], session: SessionState) -> None:
    """List PRs where the authenticated user is a requested reviewer."""
    owner, repo, token, prs = _fetch_open_prs(session)
    username = _get_authenticated_username(session)
    _remaining, limit = _parse_limit(args)

    if not prs:
        console.print("[dim]No open pull requests.[/dim]")
        return

    console.print("[dim]Fetching reviewer info...[/dim]")
    assigned: list[dict[str, Any]] = []
    for pr in prs[:limit]:
        detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr["number"], token=token)
        if username in detail.get("reviewers", []):
            assigned.append(pr)

    _render_pr_table(f"PRs Assigned to {username}", assigned)


def pr_stale(args: list[str], session: SessionState) -> None:
    """List PRs with no activity beyond the staleness threshold."""
    _owner, _repo, _token, prs = _fetch_open_prs(session)

    stale_days = session.effective_settings.pr_stale_days
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            try:
                stale_days = int(args[i + 1])
            except ValueError:
                console.print(f"[red]Invalid --days value: {args[i + 1]} (expected integer)[/red]")
                return
            i += 2
        else:
            i += 1

    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=stale_days)

    stale: list[dict[str, Any]] = []
    for pr in prs:
        updated_str = pr.get("updated_at", "")
        if not updated_str:
            continue
        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        if updated_at < cutoff:
            age_days = (now - updated_at).days
            pr_copy = {**pr, "age": f"{age_days}d ago"}
            stale.append(pr_copy)

    _render_pr_table(
        f"Stale PRs (>{stale_days} days)",
        stale,
        extra_columns=[("Last Update", "age")],
    )


def pr_ready(args: list[str], session: SessionState) -> None:
    """List PRs that passed all checks and have at least one approval."""
    owner, repo, token, prs = _fetch_open_prs(session)
    _remaining, limit = _parse_limit(args)

    candidates = [pr for pr in prs if not pr.get("draft", False)]

    if not candidates:
        console.print("[dim]No open non-draft pull requests.[/dim]")
        return

    console.print("[dim]Checking CI and review status...[/dim]")
    ready: list[dict[str, Any]] = []
    for pr in candidates[:limit]:
        pr_num = pr["number"]

        checks = get_pr_checks(owner=owner, repo=repo, pr_number=pr_num, token=token)
        all_passed = bool(checks) and all(c["conclusion"] == "success" for c in checks)
        if not all_passed:
            continue

        reviews = get_pr_reviews(owner=owner, repo=repo, pr_number=pr_num, token=token)
        has_approval = any(r["state"] == "APPROVED" for r in reviews)
        if not has_approval:
            continue

        ready.append(pr)

    _render_pr_table("Ready to Merge", ready)


def pr_conflicts(args: list[str], session: SessionState) -> None:
    """List PRs with merge conflicts."""
    owner, repo, token, prs = _fetch_open_prs(session)
    _remaining, limit = _parse_limit(args)

    if not prs:
        console.print("[dim]No open pull requests.[/dim]")
        return

    console.print("[dim]Checking merge status...[/dim]")
    conflicting: list[dict[str, Any]] = []
    unknown_count = 0
    for pr in prs[:limit]:
        detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr["number"], token=token)
        mergeable = detail.get("mergeable")
        if mergeable is False:
            conflicting.append(pr)
        elif mergeable is None:
            unknown_count += 1

    _render_pr_table("PRs with Merge Conflicts", conflicting)
    if unknown_count > 0:
        console.print(
            f"[dim]Note: {unknown_count} PR(s) have unknown merge status "
            "(GitHub is still computing mergeability).[/dim]"
        )


def pr_summary(args: list[str], session: SessionState) -> None:
    """Show a dashboard overview of PR states.

    By default shows only counts that require no per-PR API calls (fast).
    Use --full to include ready-to-merge, conflicts, assigned, and unresolved
    counts (requires per-PR API calls, slower).
    """
    is_full = "--full" in args
    owner, repo, token, prs = _fetch_open_prs(session)
    username = _get_authenticated_username(session)

    total = len(prs)
    if total == 0:
        console.print("[dim]No open pull requests.[/dim]")
        return

    # Cheap counts (no extra API calls)
    draft_count = sum(1 for pr in prs if pr.get("draft", False))
    my_prs = [pr for pr in prs if pr["author"] == username]
    my_numbers = ", ".join(f"#{pr['number']}" for pr in my_prs)

    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=session.effective_settings.pr_stale_days)
    stale_count = 0
    for pr in prs:
        updated_str = pr.get("updated_at", "")
        if updated_str:
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            if updated_at < cutoff:
                stale_count += 1

    lines = [
        f"[bold]Open:[/bold]            {total}",
        f"[bold]Draft:[/bold]           {draft_count}",
        f"[bold]Stale (>{session.effective_settings.pr_stale_days}d):[/bold]     {stale_count}",
    ]

    # Expensive counts (per-PR API calls) -- only with --full
    if is_full:
        console.print("[dim]Analyzing PRs (full mode)...[/dim]")
        ready_count = 0
        conflicts_count = 0
        assigned_prs: list[dict[str, Any]] = []
        unresolved_count = 0
        needs_review_count = 0

        for pr in prs:
            pr_num = pr["number"]
            detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_num, token=token)

            if detail.get("mergeable") is False:
                conflicts_count += 1

            if username in detail.get("reviewers", []):
                assigned_prs.append(pr)

            if not pr.get("draft", False):
                checks = get_pr_checks(owner=owner, repo=repo, pr_number=pr_num, token=token)
                reviews = get_pr_reviews(owner=owner, repo=repo, pr_number=pr_num, token=token)

                if not reviews:
                    needs_review_count += 1

                all_passed = bool(checks) and all(c["conclusion"] == "success" for c in checks)
                has_approval = any(r["state"] == "APPROVED" for r in reviews)
                if all_passed and has_approval:
                    ready_count += 1

                if _has_unresolved_changes(reviews):
                    unresolved_count += 1

        assigned_numbers = ", ".join(f"#{pr['number']}" for pr in assigned_prs)
        lines.extend(
            [
                f"[bold]Ready to merge:[/bold]  {ready_count}",
                f"[bold]Has conflicts:[/bold]   {conflicts_count}",
                f"[bold]Needs review:[/bold]    {needs_review_count}",
                f"[bold]Unresolved:[/bold]      {unresolved_count}",
            ]
        )

    lines.append("")
    lines.append(
        f"[bold]My PRs:[/bold]          {len(my_prs)}" + (f"  ({my_numbers})" if my_prs else "")
    )

    if is_full:
        lines.append(
            f"[bold]Assigned to me:[/bold]  {len(assigned_prs)}"
            + (f"  ({assigned_numbers})" if assigned_prs else "")
        )
    else:
        lines.append("")
        lines.append("[dim]Use --full for ready/conflicts/assigned/unresolved counts[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"{owner}/{repo} -- PR Summary",
            border_style="blue",
        )
    )


def pr_unresolved(args: list[str], session: SessionState) -> None:
    """List PRs with unresolved review feedback (CHANGES_REQUESTED)."""
    owner, repo, token, prs = _fetch_open_prs(session)
    _remaining, limit = _parse_limit(args)

    if not prs:
        console.print("[dim]No open pull requests.[/dim]")
        return

    console.print("[dim]Checking review status...[/dim]")
    unresolved: list[dict[str, Any]] = []
    for pr in prs[:limit]:
        reviews = get_pr_reviews(owner=owner, repo=repo, pr_number=pr["number"], token=token)
        if _has_unresolved_changes(reviews):
            requesters = _changes_requested_by(reviews)
            pr_copy = {**pr, "requested_by": ", ".join(requesters)}
            unresolved.append(pr_copy)

    _render_pr_table(
        "PRs with Unresolved Feedback",
        unresolved,
        extra_columns=[("Requested By", "requested_by")],
    )
