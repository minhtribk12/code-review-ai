"""Repo commands: list, select, current, clear.

Lists both local repos (from git remotes) and remote repos (from GitHub API).
Repos are tagged as :local or :remote in the display and toolbar.
``repo select`` opens a full-screen selector when called without arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from rich.console import Console
from rich.table import Table

from code_review_agent.error_guidance import classify_exception
from code_review_agent.errors import UserError, print_error
from code_review_agent.github_client import list_user_repos
from code_review_agent.interactive import git_ops
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState

console = Console()


def _get_token(session: SessionState) -> str | None:
    """Get the GitHub token from effective settings."""
    effective = session.effective_settings
    if effective.github_token is None:
        return None
    return effective.github_token.get_secret_value()


def _collect_local_repos() -> list[dict[str, Any]]:
    """Collect repos from local git remotes."""
    remotes = git_ops.list_remotes()
    repos: list[dict[str, Any]] = []
    seen: set[str] = set()

    for remote_name, url in remotes.items():
        parsed = git_ops.parse_github_owner_repo(url)
        if parsed is None:
            continue
        full_name = f"{parsed[0]}/{parsed[1]}"
        if full_name in seen:
            continue
        seen.add(full_name)
        repos.append(
            {
                "full_name": full_name,
                "source": "local",
                "remote_name": remote_name,
                "description": "",
                "private": False,
                "language": "",
                "open_issues_count": 0,
            }
        )

    return repos


def _collect_all_repos(
    session: SessionState,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Collect local repos + remote repos from GitHub API.

    Local repos appear first. Duplicates (same owner/repo in both) are
    merged and shown as :local.
    """
    local_repos = _collect_local_repos()
    local_names = {r["full_name"] for r in local_repos}

    # Fetch remote repos if token is available
    token = _get_token(session)
    remote_repos: list[dict[str, Any]] = []
    if token:
        try:
            raw = list_user_repos(token=token, limit=limit)
            for r in raw:
                name = r["full_name"]
                if name in local_names:
                    # Merge: enrich local entry with remote metadata
                    for local in local_repos:
                        if local["full_name"] == name:
                            local["description"] = r.get("description", "")
                            local["private"] = r.get("private", False)
                            local["language"] = r.get("language", "")
                            local["open_issues_count"] = r.get("open_issues_count", 0)
                            break
                else:
                    remote_repos.append(
                        {
                            "full_name": name,
                            "source": "remote",
                            "remote_name": "",
                            "description": r.get("description", ""),
                            "private": r.get("private", False),
                            "language": r.get("language", ""),
                            "open_issues_count": r.get("open_issues_count", 0),
                        }
                    )
        except Exception as exc:
            console.print(
                f"  [{theme.warning}]Could not fetch remote repos: {exc}[/{theme.warning}]"
            )

    return local_repos + remote_repos


def cmd_repo(args: list[str], session: SessionState) -> None:
    """Repo command router."""
    if not args:
        console.print(
            "[red]Usage: repo <subcommand>[/red]\n"
            "  list [--limit N]         List local and remote repositories\n"
            "  select [owner/repo]      Interactive repo picker (or direct)\n"
            "  current                  Show current active repo\n"
            "  clear                    Clear selection (use local git remote)"
        )
        return

    sub = args[0]
    sub_args = args[1:]

    handlers = {
        "list": _repo_list,
        "select": _repo_select,
        "current": _repo_current,
        "clear": _repo_clear,
    }

    handler = handlers.get(sub)
    if handler is None:
        print_error(
            UserError(
                detail=f"Unknown repo subcommand: {sub}",
                solution="Available: list, select, current, clear",
            ),
            console=console,
        )
        return

    try:
        handler(sub_args, session)
    except ValueError as exc:
        print_error(classify_exception(exc, context="Repo command"), console=console)
    except Exception as exc:
        print_error(classify_exception(exc, context="GitHub API"), console=console)


def _repo_list(args: list[str], session: SessionState) -> None:
    """List local + remote repositories."""
    limit = 30
    if args and args[0] == "--limit" and len(args) > 1:
        try:
            limit = int(args[1])
        except ValueError:
            print_error(
                UserError(
                    detail=f"Invalid limit: {args[1]}",
                    reason="Expected an integer value.",
                    solution="Usage: repo list --limit 50",
                ),
                console=console,
            )
            return

    console.print("  Fetching repositories...")
    repos = _collect_all_repos(session, limit=limit)

    if not repos:
        console.print("[dim]No repositories found.[/dim]")
        return

    table = Table(title="Repositories", show_lines=False)
    table.add_column("Repository", style="bold", width=35)
    table.add_column("Source", width=10)
    table.add_column("Lang", width=10)
    table.add_column("Issues", width=8, justify="right")
    table.add_column("Access", width=10)
    table.add_column("Description", width=35)

    active = session.active_repo
    for repo in repos:
        name = repo["full_name"]
        source = repo["source"]
        is_active = name == active

        access = "private" if repo["private"] else "public"
        source_label = f":{source}"
        if repo.get("remote_name"):
            source_label = f":{source} ({repo['remote_name']})"

        row_style = "bold green" if is_active else ""
        marker = " (*)" if is_active else ""

        table.add_row(
            f"{name}{marker}",
            source_label,
            repo["language"],
            str(repo["open_issues_count"]),
            access,
            repo["description"][:35] if repo["description"] else "",
            style=row_style,
        )

    console.print(table)

    if active:
        source = session.active_repo_source or "local"
        console.print(f"\n  [dim]Active: {active}:{source}[/dim]")


def _apply_repo_selection(
    session: SessionState,
    repo_ref: str,
    source: str,
) -> None:
    """Apply a repo selection to the session."""
    session.active_repo = repo_ref
    session.active_repo_source = source
    session.pr_cache.invalidate()
    console.print(f"  [green]Active repo: {repo_ref}:{source}[/green]")


def _repo_select(args: list[str], session: SessionState) -> None:
    """Set the active repo via interactive selector or direct argument."""
    # Direct argument: repo select owner/repo
    if args:
        repo_ref = args[0]
        if "/" not in repo_ref:
            print_error(
                UserError(
                    detail=f"Invalid repo format: '{repo_ref}'",
                    reason="Repository reference must be in owner/repo format.",
                    solution="Example: repo select octocat/Hello-World",
                ),
                console=console,
            )
            return
        local_repos = _collect_local_repos()
        local_names = {r["full_name"] for r in local_repos}
        source = "local" if repo_ref in local_names else "remote"
        _apply_repo_selection(session, repo_ref, source)
        return

    # No args: open interactive selector
    console.print("  Fetching repositories...")
    repos = _collect_all_repos(session)

    if not repos:
        console.print("[dim]No repositories found.[/dim]")
        return

    selected = _run_repo_selector(repos, session.active_repo)
    if selected is None:
        console.print("[dim]Selection cancelled.[/dim]")
        return

    _apply_repo_selection(session, selected["full_name"], selected["source"])


def _run_repo_selector(
    repos: list[dict[str, Any]],
    current_active: str | None,
) -> dict[str, Any] | None:
    """Open a full-screen repo selector. Returns selected repo or None."""
    cursor = 0
    # Pre-select the currently active repo
    for i, r in enumerate(repos):
        if r["full_name"] == current_active:
            cursor = i
            break

    result: list[dict[str, Any] | None] = [None]

    def render() -> FormattedText:
        lines: list[tuple[str, str]] = []
        lines.append(("bold", " Select Repository\n"))
        lines.append(("dim", "  Up/Down to navigate, Enter to select, Esc to cancel\n"))
        lines.append(("", "\n"))

        for i, repo in enumerate(repos):
            is_cursor = i == cursor
            name = repo["full_name"]
            source = repo["source"]
            is_current = name == current_active

            # Cursor indicator
            if is_cursor:
                lines.append(("bold cyan", " > "))
            else:
                lines.append(("", "   "))

            # Radio button
            if is_current:
                lines.append(("green bold", "(*) "))
            else:
                lines.append(("", "( ) "))

            # Repo name + source tag
            style = "bold" if is_cursor else ""
            lines.append((style, f"{name}:{source}"))

            # Metadata
            desc = repo.get("description", "")
            lang = repo.get("language", "")
            access = "private" if repo.get("private") else "public"
            meta_parts = []
            if lang:
                meta_parts.append(lang)
            meta_parts.append(access)
            if desc:
                meta_parts.append(desc[:30])
            meta_str = " | ".join(meta_parts)
            lines.append(("dim", f"  ({meta_str})"))

            if is_current:
                lines.append(("green", "  (current)"))

            lines.append(("", "\n"))

        return FormattedText(lines)

    kb = KeyBindings()

    @kb.add("up")
    def on_up(event: KeyPressEvent) -> None:
        nonlocal cursor
        cursor = max(0, cursor - 1)

    @kb.add("down")
    def on_down(event: KeyPressEvent) -> None:
        nonlocal cursor
        cursor = min(len(repos) - 1, cursor + 1)

    @kb.add("enter")
    def on_enter(event: KeyPressEvent) -> None:
        result[0] = repos[cursor]
        event.app.exit()

    @kb.add("space")
    def on_space(event: KeyPressEvent) -> None:
        result[0] = repos[cursor]
        event.app.exit()

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        event.app.exit()

    control = FormattedTextControl(render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    return result[0]


def _repo_current(args: list[str], session: SessionState) -> None:
    """Show the current active repo."""
    if session.active_repo:
        source = session.active_repo_source or "local"
        console.print(f"  Active repo: [bold]{session.active_repo}:{source}[/bold]")
    else:
        # Show what _get_repo_info would use
        remote = git_ops.remote_url()
        if remote:
            parsed = git_ops.parse_github_owner_repo(remote)
            if parsed:
                console.print(
                    f"  Using local git remote: [bold]{parsed[0]}/{parsed[1]}:local[/bold]"
                )
            else:
                console.print(f"  [dim]Local remote (not GitHub): {remote}[/dim]")
        else:
            console.print("  [dim]No repo selected and no git remote configured.[/dim]")


def _repo_clear(args: list[str], session: SessionState) -> None:
    """Clear the active repo selection."""
    if session.active_repo is None:
        console.print("[dim]No active repo to clear.[/dim]")
        return

    old = session.active_repo
    old_source = session.active_repo_source
    session.active_repo = None
    session.active_repo_source = ""
    session.pr_cache.invalidate()
    console.print(f"  [green]Cleared active repo (was: {old}:{old_source}).[/green]")

    remote = git_ops.remote_url()
    if remote:
        parsed = git_ops.parse_github_owner_repo(remote)
        if parsed:
            console.print(f"  [dim]Falling back to: {parsed[0]}/{parsed[1]}:local[/dim]")
