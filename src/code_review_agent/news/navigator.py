"""News navigator TUI: browse, read, and save articles.

Full-screen prompt_toolkit application reusing the findings navigator
UX pattern (vim keys, detail panel, modes).
"""

from __future__ import annotations

import shutil
import textwrap
import webbrowser
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.news.models import Article
    from code_review_agent.news.storage import ArticleStore

# Style constants
_STYLE_HEADER = "bold"
_STYLE_MUTED = "dim"
_STYLE_ACCENT = "bold cyan"
_STYLE_HIGHLIGHT = "reverse bold"
_STYLE_SAVED = "bold green"
_STYLE_READ = "dim"

# Type alias
_Lines = list[tuple[str, str]]


class NewsViewer:
    """State machine for the news navigator."""

    def __init__(
        self,
        articles: list[Article],
        store: ArticleStore | None = None,
        source_status: dict[str, str] | None = None,
    ) -> None:
        self.articles = articles
        self.store = store
        self.source_status = source_status or {}
        self.cursor: int = 0
        self.is_detail_open: bool = False
        self.status_message: str = ""
        self.wants_reader: bool = False
        self.selected: set[int] = set()

    def move_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def move_down(self) -> None:
        if self.cursor < len(self.articles) - 1:
            self.cursor += 1

    def toggle_detail(self) -> None:
        self.is_detail_open = not self.is_detail_open

    def toggle_select(self) -> None:
        """Toggle multi-select on current article (Space key)."""
        if not self.articles:
            return
        if self.cursor in self.selected:
            self.selected.discard(self.cursor)
        else:
            self.selected.add(self.cursor)

    def select_all(self) -> None:
        """Select all visible articles."""
        self.selected = set(range(len(self.articles)))
        self.status_message = f"Selected all {len(self.articles)}"

    def clear_selection(self) -> None:
        """Clear multi-selection."""
        self.selected.clear()

    def toggle_save(self) -> None:
        if not self.articles or self.store is None:
            return
        article = self.articles[self.cursor]
        new_saved = not article.is_saved
        self.store.mark_saved(article.id, saved=new_saved)
        self.articles[self.cursor] = article.model_copy(update={"is_saved": new_saved})
        self.status_message = "Saved" if new_saved else "Unsaved"

    def mark_read(self) -> None:
        if not self.articles or self.store is None:
            return
        article = self.articles[self.cursor]
        if not article.is_read:
            self.store.mark_read(article.id)
            self.articles[self.cursor] = article.model_copy(update={"is_read": True})

    def mark_selected_read(self) -> None:
        """Mark all selected (or current) articles as read."""
        if not self.articles or self.store is None:
            return
        targets = self.selected if self.selected else {self.cursor}
        count = 0
        for idx in targets:
            if 0 <= idx < len(self.articles) and not self.articles[idx].is_read:
                self.store.mark_read(self.articles[idx].id)
                self.articles[idx] = self.articles[idx].model_copy(update={"is_read": True})
                count += 1
        self.selected.clear()
        self.status_message = f"Marked {count} as read"

    def delete_current(self) -> None:
        """Delete the current article."""
        if not self.articles or self.store is None:
            return
        article = self.articles[self.cursor]
        self.store.delete_article(article.id)
        self.articles.pop(self.cursor)
        self.selected.discard(self.cursor)
        # Reindex selections above removed item
        self.selected = {i - 1 if i > self.cursor else i for i in self.selected}
        self.cursor = min(self.cursor, max(0, len(self.articles) - 1))
        self.status_message = "Deleted"

    def delete_selected(self) -> None:
        """Delete all selected articles (or current if none selected)."""
        if not self.articles or self.store is None:
            return
        targets = sorted(self.selected, reverse=True) if self.selected else [self.cursor]
        ids = [self.articles[i].id for i in targets if 0 <= i < len(self.articles)]
        self.store.delete_articles(ids)
        for idx in sorted(targets, reverse=True):
            if 0 <= idx < len(self.articles):
                self.articles.pop(idx)
        self.selected.clear()
        self.cursor = min(self.cursor, max(0, len(self.articles) - 1))
        self.status_message = f"Deleted {len(ids)}"

    def mark_all_read(self) -> None:
        """Mark ALL articles as read."""
        if not self.articles or self.store is None:
            return
        count = self.store.mark_all_read()
        for i in range(len(self.articles)):
            if not self.articles[i].is_read:
                self.articles[i] = self.articles[i].model_copy(update={"is_read": True})
        self.status_message = f"Marked {count} as read"

    def open_in_browser(self) -> None:
        if not self.articles:
            return
        try:
            webbrowser.open(self.articles[self.cursor].url)
            self.status_message = "Opened in browser"
        except Exception:
            self.status_message = "Failed to open browser"

    @property
    def current_article(self) -> Article | None:
        if not self.articles:
            return None
        return self.articles[self.cursor]

    @property
    def unread_count(self) -> int:
        return sum(1 for a in self.articles if not a.is_read)

    @property
    def saved_count(self) -> int:
        return sum(1 for a in self.articles if a.is_saved)

    @property
    def selected_count(self) -> int:
        return len(self.selected)


def _render(viewer: NewsViewer) -> FormattedText:
    """Render the complete navigator screen."""
    tw = shutil.get_terminal_size((120, 40)).columns
    lines: _Lines = []

    # Header
    total = len(viewer.articles)
    lines.append((_STYLE_HEADER, " News Reader\n"))
    stats = f" {total} articles | {viewer.unread_count} unread | {viewer.saved_count} saved"
    if viewer.selected_count > 0:
        stats += f" | {viewer.selected_count} selected"
    lines.append((_STYLE_MUTED, stats + "\n"))
    if viewer.status_message:
        lines.append(("green", f" {viewer.status_message}\n"))
        viewer.status_message = ""
    # Source status (quality nudge)
    if viewer.source_status:
        parts = []
        for src, status in sorted(viewer.source_status.items()):
            icon = "+" if status.startswith("ok") else "-"
            parts.append(f"{icon}{src}: {status}")
        lines.append((_STYLE_MUTED, f" Sources: {' | '.join(parts)}\n"))
    lines.append(("", "\n"))

    if not viewer.articles:
        lines.append((_STYLE_MUTED, "  No articles. Fetch some first: news hackernews\n"))
        return FormattedText(lines)

    # Table header
    lines.append(
        (
            _STYLE_MUTED,
            "   SCORE  TITLE                                         SOURCE     AGE  CMTS\n",
        )
    )
    lines.append((_STYLE_MUTED, "   " + "-" * min(tw - 4, 76) + "\n"))

    # Viewport -- shrink list when detail is open
    detail_h = 18 if viewer.is_detail_open else 0
    vp = max(5, 25 - detail_h)
    v_start = max(0, viewer.cursor - vp // 2)
    v_end = min(len(viewer.articles), v_start + vp)

    for i in range(v_start, v_end):
        article = viewer.articles[i]
        is_cursor = i == viewer.cursor
        is_multi_sel = i in viewer.selected

        # Prefix: cursor + selection indicator
        if is_cursor:
            lines.append((_STYLE_HIGHLIGHT, " > "))
        elif is_multi_sel:
            lines.append((_STYLE_SAVED, " x "))
        else:
            lines.append(("", "   "))

        # Score
        score = article.score_display.rjust(5)
        base_style = _STYLE_READ if article.is_read else ""
        if is_cursor:
            base_style = "bold"
        elif is_multi_sel:
            base_style = "bold green"

        lines.append((base_style, f"{score}  "))

        # Title (truncated)
        title_width = max(20, tw - 35)
        title = article.title[:title_width]
        if len(article.title) > title_width:
            title = title[: title_width - 3] + "..."
        lines.append((base_style, f"{title:<{title_width}}"))

        # Source domain
        domain = article.domain[:10]
        lines.append((_STYLE_MUTED, f" {domain:<10}"))

        # Age
        lines.append((_STYLE_MUTED, f" {article.age_display:>4}"))

        # Comments
        cmts = str(article.comment_count) if article.comment_count > 0 else ""
        lines.append((_STYLE_MUTED, f" {cmts:>5}"))

        # Status indicators
        flags = ""
        if article.is_saved:
            flags += "*"
        if article.is_read:
            flags += "."
        if is_multi_sel:
            flags += "x"
        lines.append((_STYLE_SAVED if article.is_saved else _STYLE_MUTED, f" {flags}"))
        lines.append(("", "\n"))

    # Detail panel
    if viewer.is_detail_open and viewer.current_article:
        lines.extend(_render_detail(viewer.current_article, tw))

    # Footer
    lines.append(("", "\n"))
    if viewer.selected_count > 0:
        lines.append(
            (
                _STYLE_MUTED,
                " Space select | D delete sel | R read sel | a all | c clear | q quit\n",
            )
        )
    else:
        lines.append(
            (
                _STYLE_MUTED,
                " j/k nav | Enter detail | r read | s save | d del | Space select | q quit\n",
            )
        )

    return FormattedText(lines)


def _render_detail(article: Article, tw: int) -> _Lines:
    """Enhanced detail panel: summary, comments, convergence."""
    lines: _Lines = []
    sep = "=" * min(tw - 6, 70)
    wrap_w = max(40, tw - 10)

    lines.append(("", "\n"))
    lines.append((_STYLE_MUTED, f"   {sep}\n"))
    lines.append((_STYLE_HEADER, f"   {article.title}\n"))

    # Metadata
    meta: list[str] = []
    if article.author:
        meta.append(f"by {article.author}")
    meta.append(article.domain)
    if article.age_display:
        meta.append(f"{article.age_display} ago")
    if article.score > 0:
        meta.append(f"{article.score_display} pts")
    if article.comment_count > 0:
        meta.append(f"{article.comment_count} comments")
    lines.append((_STYLE_MUTED, f"   {' | '.join(meta)}\n"))

    if article.tags:
        lines.append((_STYLE_MUTED, f"   Tags: {', '.join(article.tags)}\n"))

    # Summary section
    text = article.summary or article.content_text
    if text:
        lines.append(("", "\n"))
        lines.append(("bold underline", "   SUMMARY\n"))
        # Split on pipe delimiter from pipeline enrichment
        summary = text.split(" | Top comment:")[0] if " | Top comment:" in text else text
        for line in summary.splitlines()[:6]:
            for w in textwrap.wrap(line, width=wrap_w) or [""]:
                lines.append(("", f"   {w}\n"))

    # Top community comment
    top_comment = ""
    if " | Top comment:" in (article.summary or ""):
        top_comment = (article.summary or "").split(" | Top comment:")[1].strip()
    if top_comment:
        lines.append(("", "\n"))
        lines.append(("bold underline", "   TOP DISCUSSION\n"))
        for w in textwrap.wrap(f'"{top_comment}"', width=wrap_w) or [""]:
            lines.append(("italic", f"   {w}\n"))

    # Source
    lines.append(("", "\n"))
    lines.append((_STYLE_ACCENT, f"   Source: {article.url}\n"))
    lines.append((_STYLE_MUTED, "   [r] read full | [o] browser | [s] save | [n] next\n"))

    return lines


def run_news_navigator(
    articles: list[Article],
    store: ArticleStore | None = None,
    source_status: dict[str, str] | None = None,
) -> None:
    """Launch the full-screen news navigator."""
    viewer = NewsViewer(articles, store=store, source_status=source_status)
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        viewer.move_up()

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        viewer.move_down()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        viewer.toggle_detail()
        viewer.mark_read()

    @kb.add("r")
    def on_read(event: KeyPressEvent) -> None:
        """Open full-screen reader for current article."""
        if viewer.current_article:
            viewer.mark_read()
            viewer.wants_reader = True
            event.app.exit()

    @kb.add("s")
    def on_save(_event: KeyPressEvent) -> None:
        viewer.toggle_save()

    @kb.add("o")
    def on_open(_event: KeyPressEvent) -> None:
        viewer.open_in_browser()

    @kb.add("n")
    def on_next(_event: KeyPressEvent) -> None:
        viewer.mark_read()
        viewer.move_down()

    @kb.add("d")
    def on_delete(_event: KeyPressEvent) -> None:
        """Delete current article."""
        viewer.delete_current()

    @kb.add("D")
    def on_delete_selected(_event: KeyPressEvent) -> None:
        """Delete all selected (or current if none selected)."""
        viewer.delete_selected()

    @kb.add("space")
    def on_toggle_select(_event: KeyPressEvent) -> None:
        """Toggle multi-select on current article."""
        viewer.toggle_select()
        viewer.move_down()

    @kb.add("a")
    def on_select_all(_event: KeyPressEvent) -> None:
        """Select all articles."""
        viewer.select_all()

    @kb.add("c")
    def on_clear_selection(_event: KeyPressEvent) -> None:
        """Clear multi-selection."""
        viewer.clear_selection()

    @kb.add("R")
    def on_mark_selected_read(_event: KeyPressEvent) -> None:
        """Mark selected (or current) as read."""
        viewer.mark_selected_read()

    @kb.add("A")
    def on_mark_all_read(_event: KeyPressEvent) -> None:
        """Mark ALL articles as read."""
        viewer.mark_all_read()

    @kb.add(Keys.ScrollUp)
    def on_scroll_up(_event: KeyPressEvent) -> None:
        viewer.move_up()
        viewer.move_up()
        viewer.move_up()

    @kb.add(Keys.ScrollDown)
    def on_scroll_down(_event: KeyPressEvent) -> None:
        viewer.move_down()
        viewer.move_down()
        viewer.move_down()

    @kb.add("escape")
    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if viewer.is_detail_open:
            viewer.is_detail_open = False
        else:
            event.app.exit()

    # Main loop: navigator -> reader -> navigator
    while True:
        viewer.wants_reader = False

        control = FormattedTextControl(lambda: _render(viewer))
        window = Window(content=control, wrap_lines=True)
        layout = Layout(HSplit([window]))

        app: Application[None] = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.1,
        )
        app.run()

        if viewer.wants_reader and viewer.current_article:
            from code_review_agent.news.reader import run_article_reader

            run_article_reader(
                article=viewer.current_article,
                store=viewer.store,
                articles=viewer.articles,
                article_index=viewer.cursor,
            )
            # After reader exits, loop back to navigator
        else:
            break  # User pressed q/Esc -> exit

    # Post-TUI summary
    from rich.console import Console

    con = Console()
    read_count = sum(1 for a in viewer.articles if a.is_read)
    saved_count = viewer.saved_count
    if read_count or saved_count:
        con.print(f"  {read_count} read, {saved_count} saved")
