"""News tab: unread count badge and quick actions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState

_DEFAULT_DB = Path("~/.cra/reviews.db").expanduser()


class NewsTab(Vertical):
    """News overview with unread count and recent articles."""

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("", id="news-summary")
        yield DataTable(id="news-table")

    def on_mount(self) -> None:
        self._load_data()

    def _load_data(self) -> None:
        summary = self.query_one("#news-summary", Static)
        table = self.query_one("#news-table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("Score", "Title", "Source", "Age")

        try:
            from code_review_agent.news.storage import ArticleStore

            store = ArticleStore(db_path=_DEFAULT_DB)
            unread = store.get_unread_count()
            articles = store.load_articles(limit=20)

            summary.update(
                f" [bold]News[/bold] | {len(articles)} articles | "
                f"{unread} unread\n"
                " Use [bold]news <topic>[/bold] in REPL to fetch, "
                "[bold]read-news[/bold] to browse."
            )

            for article in articles:
                age = article.age_display or ""
                read_marker = "." if article.is_read else ""
                title = article.title[:60]
                table.add_row(
                    str(article.score),
                    f"{read_marker}{title}",
                    article.domain[:12],
                    age,
                )
        except Exception:
            summary.update(
                " [bold]News[/bold] | No articles yet\n"
                " Use [bold]news <topic>[/bold] in REPL to fetch."
            )

    def get_unread_count(self) -> int:
        """Return unread count for tab badge."""
        try:
            from code_review_agent.news.storage import ArticleStore

            store = ArticleStore(db_path=_DEFAULT_DB)
            return store.get_unread_count()
        except Exception:
            return 0
