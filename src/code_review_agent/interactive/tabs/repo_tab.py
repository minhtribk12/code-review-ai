"""Repo tab: select and manage active repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState

logger = structlog.get_logger(__name__)


class RepoTab(Vertical):
    """Repository selection and management tab."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("", id="repo-current")
        yield DataTable(id="repo-table")

    def on_mount(self) -> None:
        self._refresh_current()
        table = self.query_one("#repo-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Repository", "Source", "Description")
        self._load_repos(table)

    def _refresh_current(self) -> None:
        label = self.query_one("#repo-current", Static)
        if self._session.active_repo:
            source = self._session.active_repo_source or "unknown"
            label.update(f" Active: [bold]{self._session.active_repo}[/bold] ({source})")
        else:
            label.update(" No repository selected. Press Enter to select.")

    def _load_repos(self, table: DataTable[str]) -> None:
        # Local repos from git remote
        try:
            remote = git_ops.remote_url()
            if remote:
                remote_clean = remote.rstrip("/").removesuffix(".git")
                if "github.com" in remote_clean:
                    parts = remote_clean.split("github.com")[-1].lstrip(":/").split("/")
                    if len(parts) >= 2:
                        name = f"{parts[0]}/{parts[1]}"
                        table.add_row(name, "local", "From git remote")
        except Exception:
            logger.debug("failed to read git remote", exc_info=True)

        # Remote repos via GitHub API
        try:
            settings = self._session.effective_settings
            token = (
                settings.github_token.get_secret_value()
                if settings.github_token is not None
                else None
            )
            if token:
                from code_review_agent.github_client import list_user_repos

                repos = list_user_repos(token=token)
                for repo in repos:
                    name = repo.get("full_name", "")
                    desc = repo.get("description", "") or ""
                    if len(desc) > 40:
                        desc = desc[:37] + "..."
                    table.add_row(name, "remote", desc)
        except Exception:
            logger.debug("failed to fetch remote repos", exc_info=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#repo-table", DataTable)
        row_data = table.get_row(event.row_key)
        repo_name = str(row_data[0])
        source = str(row_data[1])

        self._session.active_repo = repo_name
        self._session.active_repo_source = source
        self._session.pr_cache.invalidate()
        self._refresh_current()
        self.notify(f"Active repo: {repo_name}")
