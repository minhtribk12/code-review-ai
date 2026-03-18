"""Config tab: view and edit configuration settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState


class ConfigTab(Vertical):
    """Configuration viewer with launch to full-screen editor."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static(
            " Press [bold]Enter[/bold] on a setting to edit."
            " Press [bold]e[/bold] to open the full config editor.",
            id="config-hint",
        )
        yield DataTable(id="config-table")

    def on_mount(self) -> None:
        table = self.query_one("#config-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Setting", "Value", "Override")
        self._load_settings(table)

    def _load_settings(self, table: DataTable[str]) -> None:
        settings = self._session.effective_settings
        overrides = self._session.config_overrides

        fields = [
            ("llm_provider", str(settings.llm_provider)),
            ("llm_model", settings.llm_model),
            ("llm_temperature", str(settings.llm_temperature)),
            ("token_tier", str(settings.token_tier)),
            ("max_deepening_rounds", str(settings.max_deepening_rounds)),
            ("is_validation_enabled", str(settings.is_validation_enabled)),
            ("max_validation_rounds", str(settings.max_validation_rounds)),
            ("dedup_strategy", str(settings.dedup_strategy)),
            ("max_review_seconds", str(settings.max_review_seconds)),
            ("max_concurrent_agents", str(settings.max_concurrent_agents)),
            ("max_pr_files", str(settings.max_pr_files)),
            ("log_level", str(settings.log_level)),
            ("auto_save_history", str(settings.auto_save_history)),
        ]

        for key, value in fields:
            override = "session" if key in overrides else ""
            table.add_row(key, value, override)

    def launch_editor(self) -> None:
        """Open the full-screen config editor (prompt_toolkit)."""
        from code_review_agent.interactive.commands.config_edit import cmd_config_edit

        with self.app.suspend():
            cmd_config_edit([], self._session)

        # Refresh table after editor closes
        table = self.query_one("#config-table", DataTable)
        table.clear()
        self._load_settings(table)
