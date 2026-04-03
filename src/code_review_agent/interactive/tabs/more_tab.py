"""More tab: help, agents, version, and miscellaneous commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from code_review_agent.agents import AGENT_REGISTRY, CUSTOM_AGENT_NAMES

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState

_VERSION = __import__("code_review_agent").__version__

_MORE_COMMANDS: list[tuple[str, str]] = [
    ("agents", "List available review agents"),
    ("news", "Terminal news reader (40+ tech domains)"),
    ("version", "Show version info"),
    ("history", "View past reviews (opens REPL)"),
    ("watch", "Start file watcher (opens REPL)"),
]


class MoreTab(Vertical):
    """Help, agents, version, and miscellaneous commands."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield DataTable(id="more-commands")
        yield Static("", id="more-output")

    def on_mount(self) -> None:
        table = self.query_one("#more-commands", DataTable)
        table.cursor_type = "row"
        table.add_columns("Command", "Description")
        for cmd, desc in _MORE_COMMANDS:
            table.add_row(cmd, desc)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#more-commands", DataTable)
        row_data = table.get_row(event.row_key)
        cmd = str(row_data[0])

        output_widget = self.query_one("#more-output", Static)

        if cmd == "agents":
            lines = [" [bold]Available Review Agents[/bold]", ""]
            for name in AGENT_REGISTRY:
                label = " [custom]" if name in CUSTOM_AGENT_NAMES else ""
                lines.append(f"   {name}{label}")
            output_widget.update("\n".join(lines))
        elif cmd == "version":
            output_widget.update(f" code-review-agent {_VERSION}")
        elif cmd in ("history", "watch"):
            output_widget.update(
                f" '{cmd}' requires the REPL. Use "
                f"[bold]code-review-agent interactive[/bold] then run '{cmd}'."
            )
