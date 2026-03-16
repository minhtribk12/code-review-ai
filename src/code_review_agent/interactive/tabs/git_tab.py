"""Git tab: common git operations with output display."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState

_GIT_COMMANDS: list[tuple[str, str]] = [
    ("status", "Show working tree status"),
    ("diff", "Show unstaged changes"),
    ("diff staged", "Show staged changes"),
    ("log", "Show recent commit history"),
    ("branch", "List local branches"),
    ("branch -r", "List remote branches"),
    ("stash list", "List stashed changes"),
]


class GitTab(Vertical):
    """Git operations tab with command list and output panel."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield DataTable(id="git-commands")
        yield Static("", id="git-output")

    def on_mount(self) -> None:
        table = self.query_one("#git-commands", DataTable)
        table.cursor_type = "row"
        table.add_columns("Command", "Description")
        for cmd, desc in _GIT_COMMANDS:
            table.add_row(cmd, desc)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#git-commands", DataTable)
        row_data = table.get_row(event.row_key)
        cmd = str(row_data[0])

        output_widget = self.query_one("#git-output", Static)

        try:
            result = self._run_git_command(cmd)
            output_widget.update(f"[bold]$ git {cmd}[/bold]\n\n{result}")
        except Exception as exc:
            output_widget.update(f"[bold red]Error:[/bold red] {exc}")

    def _run_git_command(self, cmd: str) -> str:
        """Execute a git command and return the output."""
        if cmd == "status":
            return git_ops.status_short()
        if cmd == "diff":
            return git_ops.diff()
        if cmd == "diff staged":
            return git_ops.diff(staged=True)
        if cmd == "log":
            return git_ops.log_oneline(count=15)
        if cmd == "branch":
            return git_ops.list_branches()
        if cmd == "branch -r":
            return git_ops.list_branches(remote=True)
        if cmd == "stash list":
            return git_ops.stash_list()
        return f"Unknown command: {cmd}"
