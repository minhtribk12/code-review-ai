"""Full-screen article reader with scrolling and progress bar.

Launched from the news navigator via 'r' key. Fetches full article
content on demand, caches in SQLite, renders as rich terminal text.
"""

from __future__ import annotations

import contextlib
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

_S = "bold"
_SD = "dim"
_SA = "bold cyan"
_SC = "bg:ansibrightblack"
_SQ = "italic"
_Lines = list[tuple[str, str]]


class ArticleReader:
    """Full-screen article reader with scroll state."""

    def __init__(
        self,
        article: Article,
        store: ArticleStore | None = None,
        articles: list[Article] | None = None,
        article_index: int = 0,
    ) -> None:
        self.article = article
        self.store = store
        self.articles = articles or [article]
        self.article_index = article_index
        self.scroll_offset: int = 0
        self.content_lines: list[str] = []
        self.is_loading: bool = True
        self._fetch_content()

    def _fetch_content(self) -> None:
        """Fetch and cache article content."""
        a = self.article
        if a.content_text:
            self.content_lines = a.content_text.splitlines()
            self.is_loading = False
            self._restore_position()
            return
        try:
            from code_review_agent.news.content import fetch_article_content

            html, text = fetch_article_content(a.url)
            if text:
                self.content_lines = text.splitlines()
                if self.store:
                    self.store.update_content(a.id, html, text)
                    self.article = a.model_copy(
                        update={"content_text": text, "content_html": html},
                    )
            else:
                self._use_fallback()
        except Exception:
            self._use_fallback()
        self.is_loading = False
        self._restore_position()

    def _use_fallback(self) -> None:
        lines = (self.article.summary or "").splitlines()
        lines.append("")
        lines.append("[Press 'o' to open in browser]")
        self.content_lines = lines

    def _restore_position(self) -> None:
        if self.article.read_position > 0 and self.content_lines:
            target = int(self.article.read_position * len(self.content_lines))
            self.scroll_offset = max(0, min(target, len(self.content_lines) - 1))

    def scroll_down(self, n: int = 1) -> None:
        mx = max(0, len(self.content_lines) - self._vp_h())
        self.scroll_offset = min(mx, self.scroll_offset + n)

    def scroll_up(self, n: int = 1) -> None:
        self.scroll_offset = max(0, self.scroll_offset - n)

    def scroll_to_top(self) -> None:
        self.scroll_offset = 0

    def scroll_to_bottom(self) -> None:
        self.scroll_offset = max(0, len(self.content_lines) - self._vp_h())

    def _vp_h(self) -> int:
        return max(10, shutil.get_terminal_size((120, 40)).lines - 8)

    @property
    def progress(self) -> float:
        if not self.content_lines:
            return 0.0
        return min(1.0, (self.scroll_offset + self._vp_h()) / len(self.content_lines))

    @property
    def page_indicator(self) -> str:
        if not self.content_lines:
            return "0/0"
        vp = self._vp_h()
        total = max(1, (len(self.content_lines) + vp - 1) // vp)
        cur = min(total, (self.scroll_offset // vp) + 1)
        return f"{cur}/{total}"

    @property
    def reading_time(self) -> str:
        words = sum(len(line.split()) for line in self.content_lines)
        return f"{max(1, words // 200)} min read"

    def save_position(self) -> None:
        if self.store and self.content_lines:
            pos = self.scroll_offset / max(1, len(self.content_lines))
            self.store.update_read_position(self.article.id, min(1.0, pos))

    def next_article(self) -> bool:
        if self.article_index >= len(self.articles) - 1:
            return False
        self.save_position()
        self.article_index += 1
        self.article = self.articles[self.article_index]
        self.scroll_offset = 0
        self.is_loading = True
        self._fetch_content()
        if self.store:
            self.store.mark_read(self.article.id)
        return True

    def prev_article(self) -> bool:
        if self.article_index <= 0:
            return False
        self.save_position()
        self.article_index -= 1
        self.article = self.articles[self.article_index]
        self.scroll_offset = 0
        self.is_loading = True
        self._fetch_content()
        return True


def _render_reader(reader: ArticleReader) -> FormattedText:
    """Render the full-screen reader."""
    tw = shutil.get_terminal_size((120, 40)).columns
    lines: _Lines = []
    a = reader.article
    sep = "-" * min(tw - 4, 72)

    # Header
    lines.append((_S, f" {a.title}"))
    lines.append((_SD, f"  {reader.page_indicator}\n"))
    meta: list[str] = []
    if a.author:
        meta.append(f"by {a.author}")
    meta.append(a.domain)
    if a.age_display:
        meta.append(f"{a.age_display} ago")
    if a.score > 0:
        meta.append(f"{a.score_display} pts")
    if a.comment_count > 0:
        meta.append(f"{a.comment_count} comments")
    meta.append(reader.reading_time)
    lines.append((_SD, f" {' | '.join(meta)}\n"))
    lines.append((_SD, f" {sep}\n"))

    if reader.is_loading:
        lines.append((_SD, "\n Loading article...\n"))
        return FormattedText(lines)

    # Content viewport
    vp = reader._vp_h()
    wrap_w = max(40, tw - 6)
    wrapped: list[str] = []
    for line in reader.content_lines:
        if not line.strip():
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(line, width=wrap_w) or [""])

    visible = wrapped[reader.scroll_offset : reader.scroll_offset + vp]
    for line in visible:
        s = line.strip()
        if s.startswith("```") or s.startswith("    "):
            lines.append((_SC, f" {line}\n"))
        elif s.startswith("|") or s.startswith(">"):
            lines.append((_SQ, f" {line}\n"))
        elif s.startswith("#"):
            lines.append((_S, f" {line}\n"))
        else:
            lines.append(("", f" {line}\n"))

    for _ in range(vp - len(visible)):
        lines.append(("", "\n"))

    # Footer
    lines.append((_SD, f" {sep}\n"))
    bw = min(40, tw - 20)
    filled = int(bw * reader.progress)
    bar = "#" * filled + "-" * (bw - filled)
    lines.append((_SD, f" [{bar}] {reader.progress * 100:.0f}%\n"))
    lines.append((_SD, " j/k scroll | d/u half-page | g/G top/bottom | n/p next/prev | q back\n"))

    return FormattedText(lines)


def run_article_reader(
    article: Article,
    store: ArticleStore | None = None,
    articles: list[Article] | None = None,
    article_index: int = 0,
) -> None:
    """Launch the full-screen article reader."""
    reader = ArticleReader(article, store=store, articles=articles, article_index=article_index)
    if store:
        store.mark_read(article.id)

    kb = KeyBindings()

    @kb.add("j")
    @kb.add("down")
    def _dn(_e: KeyPressEvent) -> None:
        reader.scroll_down()

    @kb.add("k")
    @kb.add("up")
    def _up(_e: KeyPressEvent) -> None:
        reader.scroll_up()

    @kb.add("d")
    def _hd(_e: KeyPressEvent) -> None:
        reader.scroll_down(reader._vp_h() // 2)

    @kb.add("u")
    def _hu(_e: KeyPressEvent) -> None:
        reader.scroll_up(reader._vp_h() // 2)

    @kb.add("f")
    def _pd(_e: KeyPressEvent) -> None:
        reader.scroll_down(reader._vp_h())

    @kb.add("b")
    def _pu(_e: KeyPressEvent) -> None:
        reader.scroll_up(reader._vp_h())

    @kb.add("g")
    def _top(_e: KeyPressEvent) -> None:
        reader.scroll_to_top()

    @kb.add("G")
    def _bot(_e: KeyPressEvent) -> None:
        reader.scroll_to_bottom()

    @kb.add("n")
    def _nxt(_e: KeyPressEvent) -> None:
        reader.next_article()

    @kb.add("p")
    def _prv(_e: KeyPressEvent) -> None:
        reader.prev_article()

    @kb.add("o")
    def _opn(_e: KeyPressEvent) -> None:
        with contextlib.suppress(Exception):
            webbrowser.open(reader.article.url)

    @kb.add("s")
    def _sav(_e: KeyPressEvent) -> None:
        if store:
            new = not reader.article.is_saved
            store.mark_saved(reader.article.id, saved=new)
            reader.article = reader.article.model_copy(update={"is_saved": new})

    @kb.add(Keys.ScrollUp)
    def _su(_e: KeyPressEvent) -> None:
        reader.scroll_up(3)

    @kb.add(Keys.ScrollDown)
    def _sd(_e: KeyPressEvent) -> None:
        reader.scroll_down(3)

    @kb.add("q")
    @kb.add("escape")
    def _quit(event: KeyPressEvent) -> None:
        reader.save_position()
        event.app.exit()

    control = FormattedTextControl(lambda: _render_reader(reader))
    layout = Layout(HSplit([Window(content=control, wrap_lines=True)]))
    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
        refresh_interval=0.15,
    )
    app.run()
