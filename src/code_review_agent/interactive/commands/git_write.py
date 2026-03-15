"""Git write commands: branch, add, unstage, commit, stash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def cmd_branch(args: list[str], session: SessionState) -> None:
    """Branch management: list, switch, create, delete, rename."""
    if not args:
        _branch_list(remote=False)
        return

    sub = args[0]

    if sub == "-r":
        _branch_list(remote=True)
    elif sub == "switch":
        _branch_switch(args[1:])
    elif sub == "create":
        _branch_create(args[1:])
    elif sub == "delete":
        _branch_delete(args[1:])
    elif sub == "rename":
        _branch_rename(args[1:])
    else:
        console.print(
            f"[red]Unknown branch subcommand: {sub}[/red]\n"
            "  Usage: branch [switch|create|delete|rename|-r]"
        )


def _branch_list(*, remote: bool) -> None:
    """List branches."""
    output = git_ops.list_branches(remote=remote)
    current = git_ops.current_branch()

    lines: list[str] = []
    for branch in output.strip().splitlines():
        branch = branch.strip()
        if not branch:
            continue
        if branch == current:
            lines.append(f"  [bold green]* {branch}[/bold green]")
        else:
            lines.append(f"    {branch}")

    if not lines:
        console.print("[dim]No branches found.[/dim]")
        return

    title = "Remote Branches" if remote else "Local Branches"
    console.print(Panel("\n".join(lines), title=title, border_style="blue"))


def _branch_switch(args: list[str]) -> None:
    """Switch to a branch with dirty-tree safety check."""
    if not args:
        console.print("[red]Usage: branch switch <name>[/red]")
        return

    name = args[0]

    if git_ops.is_working_tree_dirty():
        console.print(
            "[yellow]Working tree has uncommitted changes. "
            "Stash or commit before switching.[/yellow]"
        )
        return

    result = git_ops.switch_branch(name)
    console.print(f"  [green]Switched to branch '{name}'[/green]")
    if result:
        console.print(f"  {result}")


def _branch_create(args: list[str]) -> None:
    """Create and switch to a new branch."""
    if not args:
        console.print("[red]Usage: branch create <name> [from][/red]")
        return

    name = args[0]
    start_point = args[1] if len(args) > 1 else None

    result = git_ops.create_branch(name, start_point)
    console.print(f"  [green]Created and switched to branch '{name}'[/green]")
    if result:
        console.print(f"  {result}")


def _branch_delete(args: list[str]) -> None:
    """Delete a branch with safety checks."""
    if not args:
        console.print("[red]Usage: branch delete <name> [--force][/red]")
        return

    name = args[0]
    is_force = "--force" in args

    if name == git_ops.current_branch():
        console.print("[red]Cannot delete the current branch.[/red]")
        return

    if not is_force and not git_ops.is_branch_merged(name):
        console.print(
            f"[yellow]Branch '{name}' is not merged. "
            f"Use 'branch delete {name} --force' to force delete.[/yellow]"
        )
        return

    result = git_ops.delete_branch(name, force=is_force)
    console.print(f"  [green]Deleted branch '{name}'[/green]")
    if result:
        console.print(f"  {result}")


def _branch_rename(args: list[str]) -> None:
    """Rename a branch."""
    if len(args) < 2:
        console.print("[red]Usage: branch rename <old> <new>[/red]")
        return

    result = git_ops.rename_branch(args[0], args[1])
    console.print(f"  [green]Renamed '{args[0]}' to '{args[1]}'[/green]")
    if result:
        console.print(f"  {result}")


def cmd_add(args: list[str], session: SessionState) -> None:
    """Stage files for commit."""
    if not args:
        console.print("[red]Usage: add <file> [file...] or add .[/red]")
        return

    if args[0] == ".":
        # Show what will be staged and confirm
        changed = git_ops.list_changed_files()
        untracked = git_ops.list_untracked_files()
        all_files = changed + untracked
        if not all_files:
            console.print("[dim]Nothing to stage.[/dim]")
            return
        console.print(f"  Staging {len(all_files)} file(s):")
        for f in all_files[:20]:
            console.print(f"    {f}")
        if len(all_files) > 20:
            console.print(f"    ... and {len(all_files) - 20} more")

    git_ops.add_files(*args)
    console.print(f"  [green]Staged: {' '.join(args)}[/green]")


def cmd_unstage(args: list[str], session: SessionState) -> None:
    """Unstage files."""
    if not args:
        console.print("[red]Usage: unstage <file> [file...][/red]")
        return

    git_ops.unstage_files(*args)
    console.print(f"  [green]Unstaged: {' '.join(args)}[/green]")


def cmd_commit(args: list[str], session: SessionState) -> None:
    """Create a commit."""
    # Check for staged files
    staged = git_ops.list_staged_files()
    if not staged:
        console.print("[yellow]Nothing staged. Use 'add' to stage files first.[/yellow]")
        return

    # Parse -m flag
    message: str | None = None
    if "-m" in args:
        idx = args.index("-m")
        if idx + 1 < len(args):
            message = args[idx + 1]

    if message is None:
        console.print('[red]Usage: commit -m "message"[/red]')
        console.print("[dim]Tip: use conventional commit format: type(scope): description[/dim]")
        return

    # Show what will be committed
    console.print(f"  Committing {len(staged)} file(s):")
    for f in staged[:10]:
        console.print(f"    {f}")
    if len(staged) > 10:
        console.print(f"    ... and {len(staged) - 10} more")

    result = git_ops.commit(message)
    console.print(f"  [green]Committed: {message}[/green]")
    if result:
        # Show the first line of git commit output (hash + message)
        first_line = result.strip().splitlines()[0] if result.strip() else ""
        if first_line:
            console.print(f"  {first_line}")


def cmd_stash(args: list[str], session: SessionState) -> None:
    """Stash management: push, pop, list."""
    if not args:
        result = git_ops.stash_push()
        if "No local changes" in result:
            console.print("[dim]No changes to stash.[/dim]")
        else:
            console.print("  [green]Stashed changes.[/green]")
            if result.strip():
                console.print(f"  {result.strip()}")
        return

    sub = args[0]

    if sub == "pop":
        result = git_ops.stash_pop()
        console.print("  [green]Popped latest stash.[/green]")
        if result.strip():
            console.print(f"  {result.strip()}")
    elif sub == "list":
        result = git_ops.stash_list()
        if not result.strip():
            console.print("[dim]No stashes.[/dim]")
        else:
            console.print(Panel(result.rstrip(), title="Stashes", border_style="blue"))
    else:
        console.print(f"[red]Unknown stash subcommand: {sub}[/red]")
        console.print("  Usage: stash [pop|list]")
