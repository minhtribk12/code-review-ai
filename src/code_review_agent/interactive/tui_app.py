"""Tabbed TUI application using Textual.

Provides a 7-tab interface grouping related commands: Repo, PR,
Findings, Git, Config, Usage, and More. Launched via
``code-review-agent tui``.

Design follows lazygit's multi-panel model and JiraTUI's tabbed
content architecture with numeric tab switching and persistent
status bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from code_review_agent.interactive.tabs.config_tab import ConfigTab
from code_review_agent.interactive.tabs.findings_tab import FindingsTab
from code_review_agent.interactive.tabs.git_tab import GitTab
from code_review_agent.interactive.tabs.more_tab import MoreTab
from code_review_agent.interactive.tabs.news_tab import NewsTab
from code_review_agent.interactive.tabs.pr_tab import PrTab
from code_review_agent.interactive.tabs.repo_tab import RepoTab
from code_review_agent.interactive.tabs.usage_tab import UsageTab

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState


class CodeReviewTUI(App[None]):
    """Tabbed TUI for the code review agent."""

    TITLE = "Code Review Agent"
    SUB_TITLE = "Multi-agent code review tool"

    CSS = """
    TabbedContent {
        height: 1fr;
    }
    TabPane {
        padding: 0 1;
    }
    #repo-current, #pr-header, #findings-summary, #config-hint {
        height: auto;
        padding: 1 0;
    }
    #repo-table, #pr-table, #git-commands, #config-table, #more-commands {
        height: 1fr;
        max-height: 50%;
    }
    #pr-detail, #git-output, #more-output, #findings-hint {
        height: auto;
        padding: 1 0;
    }
    #usage-dashboard {
        height: 1fr;
        padding: 1 0;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("1", "switch_tab('tab-repo')", "Repo", show=True),
        Binding("2", "switch_tab('tab-pr')", "PR", show=True),
        Binding("3", "switch_tab('tab-findings')", "Findings", show=True),
        Binding("4", "switch_tab('tab-git')", "Git", show=True),
        Binding("5", "switch_tab('tab-config')", "Config", show=True),
        Binding("6", "switch_tab('tab-usage')", "Usage", show=True),
        Binding("7", "switch_tab('tab-news')", "News", show=True),
        Binding("8", "switch_tab('tab-more')", "More", show=True),
        Binding("r", "refresh_tab", "Refresh", show=True),
        Binding("e", "edit_config", "Edit Config", show=False),
        Binding("enter", "activate_item", "Select", show=False),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("1:Repo", id="tab-repo"):
                yield RepoTab(self._session)
            with TabPane("2:PR", id="tab-pr"):
                yield PrTab(self._session)
            with TabPane("3:Findings", id="tab-findings"):
                yield FindingsTab(self._session)
            with TabPane("4:Git", id="tab-git"):
                yield GitTab(self._session)
            with TabPane("5:Config", id="tab-config"):
                yield ConfigTab(self._session)
            with TabPane("6:Usage", id="tab-usage"):
                yield UsageTab(self._session)
            with TabPane("7:News", id="tab-news"):
                yield NewsTab(self._session)
            with TabPane("8:More", id="tab-more"):
                yield MoreTab(self._session)
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to a specific tab by ID."""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

        # Refresh tab content on switch
        self._refresh_active_tab(tab_id)

    def _refresh_active_tab(self, tab_id: str) -> None:
        """Refresh the content of a tab when it becomes active."""
        if tab_id == "tab-pr":
            pr_tab = self.query_one(PrTab)
            pr_tab.refresh_prs()
        elif tab_id == "tab-findings":
            findings_tab = self.query_one(FindingsTab)
            findings_tab._refresh_summary()
        elif tab_id == "tab-usage":
            usage_tab = self.query_one(UsageTab)
            usage_tab.refresh_usage()

    def action_refresh_tab(self) -> None:
        """Refresh the currently active tab."""
        tabbed = self.query_one(TabbedContent)
        self._refresh_active_tab(tabbed.active)

    def action_edit_config(self) -> None:
        """Open the full-screen config editor."""
        tabbed = self.query_one(TabbedContent)
        if tabbed.active == "tab-config":
            config_tab = self.query_one(ConfigTab)
            config_tab.launch_editor()

    def action_activate_item(self) -> None:
        """Handle Enter key based on active tab context."""
        tabbed = self.query_one(TabbedContent)
        if tabbed.active == "tab-findings":
            findings_tab = self.query_one(FindingsTab)
            findings_tab.launch_navigator()


def run_tui(session: SessionState) -> None:
    """Launch the tabbed TUI application."""
    app = CodeReviewTUI(session)
    app.run()
