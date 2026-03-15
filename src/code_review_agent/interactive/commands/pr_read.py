"""PR read commands: list, show, diff, checks, checkout."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from code_review_agent.github_client import (
    fetch_pr_diff,
    get_pr_checks,
    get_pr_detail,
    list_prs,
)
from code_review_agent.interactive import git_ops
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def _parse_pr_number(args: list[str]) -> int:
    """Parse and validate the PR number from the first positional arg.

    Raises ValueError with a user-friendly message if invalid.
    """
    try:
        return int(args[0])
    except ValueError:
        msg = f"Invalid PR number: '{args[0]}'. Must be an integer."
        raise ValueError(msg) from None


def _get_repo_info(session: SessionState) -> tuple[str, str, str | None]:
    """Extract owner/repo and token for GitHub API calls.

    Priority: session.active_repo (from ``repo select``) > local git remote.
    """
    effective = session.effective_settings
    token = (
        effective.github_token.get_secret_value() if effective.github_token is not None else None
    )

    # Use manually selected repo if set
    if session.active_repo:
        parts = session.active_repo.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1], token

    # Fall back to local git remote
    remote = git_ops.remote_url()
    if remote is None:
        msg = (
            "No git remote found. Use 'repo select owner/repo' to set a repo, "
            "or 'git remote add origin <url>'."
        )
        raise ValueError(msg)

    remote = remote.rstrip("/").removesuffix(".git")
    if "github.com" not in remote:
        msg = f"Remote is not a GitHub URL: {remote}"
        raise ValueError(msg)

    url_parts = remote.split("github.com")[-1].lstrip(":/").split("/")
    if len(url_parts) < 2:
        msg = f"Cannot parse owner/repo from remote: {remote}"
        raise ValueError(msg)

    return url_parts[0], url_parts[1], token


def cmd_pr(args: list[str], session: SessionState) -> None:
    """PR command router for read, write, and workflow operations."""
    if not args:
        console.print(
            "[red]Usage: pr <subcommand> [args][/red]\n"
            "  Read:     list, show, diff, checks, checkout, review\n"
            "  Write:    create, merge, approve, request-changes\n"
            "  Workflow: mine, assigned, stale, ready, conflicts, summary, unresolved"
        )
        return

    # Lazy imports to avoid circular dependencies
    from code_review_agent.interactive.commands.pr_workflow import (
        pr_assigned,
        pr_conflicts,
        pr_mine,
        pr_ready,
        pr_stale,
        pr_summary,
        pr_unresolved,
    )
    from code_review_agent.interactive.commands.pr_write import (
        pr_approve,
        pr_create,
        pr_merge,
        pr_request_changes,
    )

    sub = args[0]
    sub_args = args[1:]

    handlers: dict[str, Any] = {
        # Read
        "list": _pr_list,
        "show": _pr_show,
        "diff": _pr_diff,
        "checks": _pr_checks,
        "checkout": _pr_checkout,
        "review": _pr_review,
        # Write
        "create": pr_create,
        "merge": pr_merge,
        "approve": pr_approve,
        "request-changes": pr_request_changes,
        # Workflow
        "mine": pr_mine,
        "assigned": pr_assigned,
        "stale": pr_stale,
        "ready": pr_ready,
        "conflicts": pr_conflicts,
        "summary": pr_summary,
        "unresolved": pr_unresolved,
    }

    handler = handlers.get(sub)
    if handler is None:
        console.print(
            f"[red]Unknown pr subcommand: {sub}[/red]\n"
            "  Read:     list, show, diff, checks, checkout, review\n"
            "  Write:    create, merge, approve, request-changes\n"
            "  Workflow: mine, assigned, stale, ready, conflicts, summary, unresolved"
        )
        return

    try:
        handler(sub_args, session)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
    except Exception as exc:
        console.print(f"[red]GitHub API error: {exc}[/red]")


def _pr_list(args: list[str], session: SessionState) -> None:
    """List pull requests."""
    owner, repo, token = _get_repo_info(session)

    state = "open"
    if args and args[0] == "--state" and len(args) > 1:
        state = args[1]

    prs = list_prs(owner=owner, repo=repo, token=token, state=state)

    if not prs:
        console.print(f"[dim]No {state} pull requests.[/dim]")
        return

    table = Table(title=f"Pull Requests ({state})", show_lines=False)
    table.add_column("#", style="bold", width=6)
    table.add_column("Title", width=40)
    table.add_column("Branch", width=25)
    table.add_column("Author", width=15)
    table.add_column("Status", width=10)

    for pr in prs:
        status = "draft" if pr["draft"] else pr["state"]
        table.add_row(
            str(pr["number"]),
            pr["title"][:40],
            f"{pr['head_branch']} -> {pr['base_branch']}",
            pr["author"],
            status,
        )

    console.print(table)


def _pr_show(args: list[str], session: SessionState) -> None:
    """Show PR details."""
    if not args:
        console.print("[red]Usage: pr show <number>[/red]")
        return

    owner, repo, token = _get_repo_info(session)
    pr_number = _parse_pr_number(args)

    detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_number, token=token)

    lines = [
        f"[bold]#{detail['number']}[/bold] {detail['title']}",
        f"Author: {detail['author']}  |  State: {detail['state']}",
        f"Branch: {detail['head_branch']} -> {detail['base_branch']}",
        f"Changed: {detail['changed_files']} files "
        f"(+{detail['additions']}/-{detail['deletions']})",
    ]
    if detail["labels"]:
        lines.append(f"Labels: {', '.join(detail['labels'])}")
    if detail["reviewers"]:
        lines.append(f"Reviewers: {', '.join(detail['reviewers'])}")
    if detail["body"]:
        lines.append("")
        lines.append(detail["body"][:500])

    console.print(Panel("\n".join(lines), title="Pull Request", border_style="blue"))


def _pr_diff(args: list[str], session: SessionState) -> None:
    """Show PR diff with syntax highlighting."""
    if not args:
        console.print("[red]Usage: pr diff <number>[/red]")
        return

    owner, repo, token = _get_repo_info(session)
    pr_number = _parse_pr_number(args)

    result = fetch_pr_diff(owner=owner, repo=repo, pr_number=pr_number, token=token)

    if not result.diff_files:
        console.print("[dim]No diff content.[/dim]")
        return

    for df in result.diff_files:
        console.print(f"\n[bold]{df.filename}[/bold] ({df.status})")
        syntax = Syntax(df.patch, "diff", theme="monokai", line_numbers=False)
        console.print(syntax)


def _pr_checks(args: list[str], session: SessionState) -> None:
    """Show CI/CD check status for a PR."""
    if not args:
        console.print("[red]Usage: pr checks <number>[/red]")
        return

    owner, repo, token = _get_repo_info(session)
    pr_number = _parse_pr_number(args)

    checks = get_pr_checks(owner=owner, repo=repo, pr_number=pr_number, token=token)

    if not checks:
        console.print("[dim]No checks found for this PR.[/dim]")
        return

    table = Table(title="CI/CD Checks", show_lines=False)
    table.add_column("Name", width=30)
    table.add_column("Status", width=12)
    table.add_column("Conclusion", width=12)

    for check in checks:
        conclusion = check["conclusion"]
        if conclusion == "success":
            style = "green"
        elif conclusion in ("failure", "cancelled"):
            style = "red"
        else:
            style = "magenta"
        table.add_row(
            check["name"],
            check["status"],
            f"[{style}]{conclusion}[/{style}]",
        )

    console.print(table)


def _pr_checkout(args: list[str], session: SessionState) -> None:
    """Check out a PR branch locally."""
    if not args:
        console.print("[red]Usage: pr checkout <number>[/red]")
        return

    owner, repo, token = _get_repo_info(session)
    pr_number = _parse_pr_number(args)

    detail = get_pr_detail(owner=owner, repo=repo, pr_number=pr_number, token=token)
    branch_name = detail["head_branch"]

    try:
        git_ops.switch_branch(branch_name)
        console.print(f"  [green]Switched to PR #{pr_number} branch: {branch_name}[/green]")
    except git_ops.GitError:
        # Branch might not exist locally, fetch and checkout
        try:
            git_ops.fetch_pr_ref(pr_number, branch_name)
            git_ops.switch_branch(branch_name)
            console.print(
                f"  [green]Fetched and switched to PR #{pr_number} branch: {branch_name}[/green]"
            )
        except git_ops.GitError as exc:
            console.print(f"[red]Failed to checkout PR branch: {exc}[/red]")


def _pr_review(args: list[str], session: SessionState) -> None:
    """Run code review on a PR.

    If the working tree is dirty, stashes changes before checking out the PR
    and pops the stash afterward.
    """
    if not args:
        console.print("[red]Usage: pr review <number> [--agents <list>][/red]")
        return

    owner, repo, token = _get_repo_info(session)
    pr_number = _parse_pr_number(args)

    # Auto-stash dirty working tree
    is_dirty = git_ops.is_working_tree_dirty()
    if is_dirty:
        console.print("  [dim]Stashing local changes...[/dim]")
        git_ops.stash_push()

    try:
        console.print(f"  Fetching {owner}/{repo}#{pr_number}...")

        result = fetch_pr_diff(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=token,
            rate_limit_warn_threshold=session.effective_settings.github_rate_limit_warn_threshold,
        )

        if not result.diff_files:
            console.print("[dim]No diff content to review.[/dim]")
            return

        from code_review_agent.interactive.commands.review_cmd import (
            _parse_review_flags,
            _run_review_on_input,
        )

        _, agent_names, output_format = _parse_review_flags(args[1:])
        _run_review_on_input(
            result,
            session,
            agent_names=agent_names,
            output_format=output_format,
        )
    finally:
        if is_dirty:
            console.print("  [dim]Restoring stashed changes...[/dim]")
            try:
                git_ops.stash_pop()
            except git_ops.GitError as exc:
                console.print(f"[red]Failed to restore stash: {exc}[/red]")
                console.print(
                    f"[{theme.warning}]Your changes are in stash."
                    f" Run 'stash pop' manually.[/{theme.warning}]"
                )
