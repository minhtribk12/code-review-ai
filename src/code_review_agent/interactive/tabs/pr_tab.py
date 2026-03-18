"""PR tab: list, inspect, and act on pull requests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState


class PrTab(Vertical):
    """Pull request listing and actions tab."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session
        self._prs: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="pr-header")
        yield DataTable(id="pr-table")
        yield Static("", id="pr-detail")

    def on_mount(self) -> None:
        table = self.query_one("#pr-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Title", "Author", "Branch", "State")
        self.refresh_prs()

    def refresh_prs(self) -> None:
        """Reload PR list for the active repo."""
        header = self.query_one("#pr-header", Static)
        table = self.query_one("#pr-table", DataTable)
        detail = self.query_one("#pr-detail", Static)
        detail.update("")

        if not self._session.active_repo:
            header.update(" No repository selected. Go to Repo tab (press 1) to select.")
            table.clear()
            self._prs = []
            return

        header.update(f" PRs for [bold]{self._session.active_repo}[/bold]")

        settings = self._session.effective_settings
        token = (
            settings.github_token.get_secret_value() if settings.github_token is not None else None
        )

        parts = self._session.active_repo.split("/", 1)
        if len(parts) != 2:
            header.update(f" Invalid repo format: {self._session.active_repo}")
            return

        owner, repo = parts

        try:
            from code_review_agent.github_client import list_prs

            self._prs = list_prs(owner=owner, repo=repo, token=token, state="open")
        except Exception as exc:
            header.update(f" Error loading PRs: {exc}")
            self._prs = []
            return

        table.clear()
        for pr in self._prs:
            state_label = "draft" if pr.get("draft") else pr.get("state", "")
            table.add_row(
                str(pr.get("number", "")),
                str(pr.get("title", ""))[:50],
                str(pr.get("author", "")),
                str(pr.get("head_branch", ""))[:20],
                state_label,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#pr-table", DataTable)
        row_data = table.get_row(event.row_key)
        pr_number = str(row_data[0])

        # Find PR detail
        pr = next((p for p in self._prs if str(p.get("number")) == pr_number), None)
        if pr is None:
            return

        detail = self.query_one("#pr-detail", Static)
        lines = [
            f" [bold]PR #{pr['number']}[/bold]: {pr.get('title', '')}",
            f" Author: {pr.get('author', '')}  |  "
            f"Branch: {pr.get('head_branch', '')} -> {pr.get('base_branch', '')}",
            f" State: {pr.get('state', '')}  |  Updated: {pr.get('updated_at', '')[:10]}",
            f" URL: {pr.get('html_url', '')}",
        ]
        detail.update("\n".join(lines))
