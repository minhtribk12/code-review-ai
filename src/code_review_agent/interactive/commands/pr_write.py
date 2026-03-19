"""PR write commands: create, merge, approve, request-changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from code_review_agent.error_guidance import classify_exception
from code_review_agent.errors import UserError, print_error
from code_review_agent.github_client import (
    GitHubAuthError,
    create_pr,
    get_pr_checks,
    get_pr_detail,
    get_pr_reviews,
    merge_pr,
    submit_pr_review,
)
from code_review_agent.interactive import git_ops
from code_review_agent.interactive.commands.pr_read import _get_repo_info, _parse_pr_number
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def _parse_flag(args: list[str], flag: str) -> str | None:
    """Extract a --flag value from args, returning None if not present."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def _has_flag(args: list[str], flag: str) -> bool:
    """Check if a boolean flag is present in args."""
    return flag in args


def pr_create(args: list[str], session: SessionState) -> None:
    """Create a pull request from the current branch."""
    owner, repo, token = _get_repo_info(session)
    if token is None:
        print_error(
            UserError(
                detail="GITHUB_TOKEN required for pr create",
                reason="Creating PRs requires GitHub API authentication.",
                solution="Set GITHUB_TOKEN in your .env file. The token needs 'repo' scope.",
            ),
            console=console,
        )
        return

    is_dry_run = _has_flag(args, "--dry-run")
    is_fill = _has_flag(args, "--fill")
    is_draft = _has_flag(args, "--draft")

    head = git_ops.current_branch()
    base = _parse_flag(args, "--base") or "main"
    title = _parse_flag(args, "--title")
    body = _parse_flag(args, "--body") or ""

    if head == base:
        print_error(
            UserError(
                detail=f"Current branch '{head}' is the same as base '{base}'",
                reason="A PR requires different head and base branches.",
                solution=(
                    "Switch to a feature branch first, or use --base to specify a different base."
                ),
            ),
            console=console,
        )
        return

    # --fill: auto-fill title/body from commits since base
    if is_fill:
        commits = git_ops.log_oneline_commits_since(base)
        if not commits:
            console.print(
                f"[{theme.warning}]No commits found since '{base}'."
                f" Cannot auto-fill title/body.[/{theme.warning}]"
            )
            return
        if title is None:
            title = commits[0]
        if not body:
            body = "\n".join(f"- {c}" for c in commits)

    if title is None:
        print_error(
            UserError(
                detail="PR title is required",
                solution="Use --title 'My title' or --fill to auto-generate from commit messages.",
            ),
            console=console,
        )
        return

    # Preview
    lines = [
        "[bold]Create PR[/bold]",
        f"  Head:  {head}",
        f"  Base:  {base}",
        f"  Title: {title}",
        f"  Draft: {is_draft}",
    ]
    if body:
        truncated = body[:300] + ("..." if len(body) > 300 else "")
        lines.append(f"  Body:  {truncated}")

    # Check upstream
    has_remote = git_ops.has_upstream()
    if not has_remote:
        lines.append(
            f"  [{theme.warning}]No upstream -- will push before creating PR.[/{theme.warning}]"
        )

    console.print(Panel("\n".join(lines), title="PR Preview", border_style="cyan"))

    if is_dry_run:
        console.print("[dim]Dry run -- no PR created.[/dim]")
        return

    # Push if no upstream
    if not has_remote:
        console.print("  Pushing branch...")
        try:
            git_ops.push_branch()
            console.print(f"  [green]Pushed {head} to origin.[/green]")
        except git_ops.GitError as exc:
            print_error(classify_exception(exc, context="Git push"), console=console)
            return

    try:
        result = create_pr(
            owner=owner,
            repo=repo,
            token=token,
            title=title,
            head=head,
            base=base,
            body=body,
            draft=is_draft,
        )
    except GitHubAuthError:
        print_error(
            UserError(
                detail="Permission denied creating PR",
                reason="Your token may lack 'repo' scope or you don't have push access.",
                solution="Check your GITHUB_TOKEN permissions at github.com/settings/tokens.",
            ),
            console=console,
        )
        return

    session.pr_cache.invalidate()
    console.print(f"  [green]Created PR #{result['number']}:[/green] {result['html_url']}")


def pr_merge(args: list[str], session: SessionState) -> None:
    """Merge a pull request with pre-flight checks."""
    if not args:
        print_error(
            UserError(
                detail="Missing PR number",
                solution="Usage: pr merge <number> [--strategy merge|squash|rebase] [--dry-run]",
            ),
            console=console,
        )
        return

    owner, repo, token = _get_repo_info(session)
    if token is None:
        print_error(
            UserError(
                detail="GITHUB_TOKEN required for pr merge",
                reason="Merging PRs requires GitHub API authentication.",
                solution="Set GITHUB_TOKEN in your .env file. The token needs 'repo' scope.",
            ),
            console=console,
        )
        return

    pr_number = _parse_pr_number(args)
    is_dry_run = _has_flag(args, "--dry-run")
    strategy = _parse_flag(args, "--strategy") or "squash"

    if strategy not in ("merge", "squash", "rebase"):
        print_error(
            UserError(
                detail=f"Invalid merge strategy: {strategy}",
                reason="GitHub supports only 'merge', 'squash', or 'rebase'.",
                solution="Usage: pr merge <number> --strategy squash",
            ),
            console=console,
        )
        return

    # Pre-flight: fetch PR detail, checks, reviews
    detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_number, token=token)
    checks = get_pr_checks(owner=owner, repo=repo, pr_number=pr_number, token=token)
    reviews = get_pr_reviews(owner=owner, repo=repo, pr_number=pr_number, token=token)

    lines = [
        f"[bold]Merge PR #{pr_number}[/bold]: {detail['title']}",
        f"  Branch:   {detail['head_branch']} -> {detail['base_branch']}",
        f"  Strategy: {strategy}",
        f"  State:    {detail['state']}",
    ]

    # Check approvals
    approvals = [r for r in reviews if r["state"] == "APPROVED"]
    changes_requested = [r for r in reviews if r["state"] == "CHANGES_REQUESTED"]
    lines.append(f"  Approvals: {len(approvals)}")

    warnings: list[str] = []
    if changes_requested:
        users = ", ".join(r["user"] for r in changes_requested)
        warnings.append(f"Changes requested by: {users}")
    if not approvals:
        warnings.append("No approvals on this PR")

    # Check CI status
    failed_checks = [c for c in checks if c["conclusion"] in ("failure", "cancelled")]
    pending_checks = [c for c in checks if c["conclusion"] == "pending"]
    if failed_checks:
        names = ", ".join(c["name"] for c in failed_checks)
        warnings.append(f"Failed checks: {names}")
    if pending_checks:
        names = ", ".join(c["name"] for c in pending_checks)
        warnings.append(f"Pending checks: {names}")

    if detail["state"] != "open":
        warnings.append(f"PR state is '{detail['state']}' (not open)")

    if detail.get("mergeable") is False:
        warnings.append("PR has merge conflicts")

    for warning in warnings:
        lines.append(f"  [{theme.warning}]Warning: {warning}[/{theme.warning}]")

    console.print(Panel("\n".join(lines), title="Merge Preview", border_style="cyan"))

    if is_dry_run:
        console.print("[dim]Dry run -- no merge performed.[/dim]")
        return

    try:
        result = merge_pr(
            owner=owner,
            repo=repo,
            token=token,
            pr_number=pr_number,
            merge_method=strategy,
        )
    except GitHubAuthError:
        print_error(
            UserError(
                detail="Permission denied merging PR",
                reason="Your token lacks write access to this repository.",
                solution=(
                    "Check your GITHUB_TOKEN permissions. You need 'repo' scope with write access."
                ),
            ),
            console=console,
        )
        return

    session.pr_cache.invalidate()

    if result["merged"]:
        console.print(
            f"  [green]Merged PR #{pr_number} ({strategy}). SHA: {result['sha'][:8]}[/green]"
        )
    else:
        print_error(
            UserError(
                detail=f"Merge failed: {result['message']}",
                reason="GitHub rejected the merge request.",
                solution=(
                    "Check for merge conflicts, branch protection rules, or required CI checks."
                ),
            ),
            console=console,
        )


def pr_approve(args: list[str], session: SessionState) -> None:
    """Submit an APPROVE review on a pull request."""
    if not args:
        print_error(
            UserError(
                detail="Missing PR number",
                solution="Usage: pr approve <number> [-m comment] [--dry-run]",
            ),
            console=console,
        )
        return

    owner, repo, token = _get_repo_info(session)
    if token is None:
        print_error(
            UserError(
                detail="GITHUB_TOKEN required for pr approve",
                reason="Approving PRs requires GitHub API authentication.",
                solution="Set GITHUB_TOKEN in your .env file. The token needs 'repo' scope.",
            ),
            console=console,
        )
        return

    pr_number = _parse_pr_number(args)
    is_dry_run = _has_flag(args, "--dry-run")
    comment = _parse_flag(args, "-m") or ""

    detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_number, token=token)

    lines = [
        f"[bold]Approve PR #{pr_number}[/bold]: {detail['title']}",
        f"  Author: {detail['author']}",
        f"  Branch: {detail['head_branch']} -> {detail['base_branch']}",
    ]
    if comment:
        lines.append(f"  Comment: {comment}")

    console.print(Panel("\n".join(lines), title="Approve Preview", border_style="green"))

    if is_dry_run:
        console.print("[dim]Dry run -- no approval submitted.[/dim]")
        return

    try:
        result = submit_pr_review(
            owner=owner,
            repo=repo,
            token=token,
            pr_number=pr_number,
            event="APPROVE",
            body=comment,
        )
    except GitHubAuthError:
        print_error(
            UserError(
                detail="Permission denied",
                reason=(
                    "You may not have access to review this PR, or you cannot approve your own PR."
                ),
                solution=(
                    "Check your GITHUB_TOKEN permissions and ensure you're not the PR author."
                ),
            ),
            console=console,
        )
        return

    session.pr_cache.invalidate()
    console.print(f"  [green]Approved PR #{pr_number}.[/green] {result['html_url']}")


def pr_request_changes(args: list[str], session: SessionState) -> None:
    """Submit a REQUEST_CHANGES review on a pull request."""
    if not args:
        print_error(
            UserError(
                detail="Missing PR number",
                solution='Usage: pr request-changes <number> -m "comment" [--dry-run]',
            ),
            console=console,
        )
        return

    owner, repo, token = _get_repo_info(session)
    if token is None:
        print_error(
            UserError(
                detail="GITHUB_TOKEN required for pr request-changes",
                reason="Requesting changes requires GitHub API authentication.",
                solution="Set GITHUB_TOKEN in your .env file. The token needs 'repo' scope.",
            ),
            console=console,
        )
        return

    pr_number = _parse_pr_number(args)
    is_dry_run = _has_flag(args, "--dry-run")
    comment = _parse_flag(args, "-m")

    if not comment:
        print_error(
            UserError(
                detail="Comment is mandatory for request-changes",
                solution='Usage: pr request-changes <number> -m "reason for changes"',
            ),
            console=console,
        )
        return

    detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_number, token=token)

    lines = [
        f"[bold]Request Changes on PR #{pr_number}[/bold]: {detail['title']}",
        f"  Author:  {detail['author']}",
        f"  Branch:  {detail['head_branch']} -> {detail['base_branch']}",
        f"  Comment: {comment}",
    ]

    console.print(Panel("\n".join(lines), title="Request Changes Preview", border_style="bold"))

    if is_dry_run:
        console.print("[dim]Dry run -- no review submitted.[/dim]")
        return

    try:
        result = submit_pr_review(
            owner=owner,
            repo=repo,
            token=token,
            pr_number=pr_number,
            event="REQUEST_CHANGES",
            body=comment,
        )
    except GitHubAuthError:
        print_error(
            UserError(
                detail="Permission denied",
                reason="You may not have access to review this PR.",
                solution="Check your GITHUB_TOKEN permissions.",
            ),
            console=console,
        )
        return

    session.pr_cache.invalidate()
    console.print(
        f"  [{theme.warning}]Requested changes on PR #{pr_number}.[/{theme.warning}]"
        f" {result['html_url']}"
    )
