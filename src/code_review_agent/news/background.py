"""Background news fetch with LLM curation.

Runs feed fetching and LLM curation in a daemon thread while the REPL
stays responsive. Shows progress in the toolbar status bar.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState
    from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)

_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class BackgroundNewsFetch:
    """Non-blocking news fetch + LLM curation.

    The REPL toolbar reads ``format_status_line()`` for progress display.
    """

    def __init__(
        self,
        domain: str,
        session: SessionState,
    ) -> None:
        self.domain = domain
        self._session = session
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._phase = "fetching"
        self._article_count = 0
        self._curated_count = 0
        self._error: str | None = None
        self._result: list[Article] | None = None
        self._synthesis: str = ""
        self._started_at = time.monotonic()
        self._frame = 0
        self._thread: threading.Thread | None = None
        self._prompt_app: Any = None

    def start(self) -> None:
        """Spawn the background daemon thread."""
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        """Fetch, curate, and save articles."""
        from code_review_agent.news.fetcher import fetch_news
        from code_review_agent.news.storage import ArticleStore

        try:
            # Phase 1: Fetch from RSS
            articles = fetch_news(self.domain)
            with self._lock:
                self._article_count = len(articles)
                if not articles:
                    self._phase = "done"
                    self._done.set()
                    return
                self._phase = "curating"

            # Phase 2: LLM curation
            curated = self._curate(articles)

            # Phase 3: Save to DB
            with self._lock:
                self._phase = "saving"

            from pathlib import Path

            store = ArticleStore(db_path=Path("~/.cra/reviews.db").expanduser())
            to_save = curated if curated else articles
            store.save_articles(to_save)

            with self._lock:
                self._result = to_save
                self._curated_count = len(to_save)
                self._phase = "done"
        except Exception as exc:
            logger.debug(f"background news fetch failed: {exc}", exc_info=True)
            with self._lock:
                self._error = str(exc)
                self._phase = "failed"
        finally:
            self._done.set()
            self._interrupt_prompt()

    def _curate(self, articles: list[Article]) -> list[Article]:
        """Run LLM curation. Returns enriched articles or empty on failure."""
        try:
            from code_review_agent.llm_client import LLMClient
            from code_review_agent.news.curator import curate_articles

            settings = self._session.effective_settings
            llm = LLMClient(settings)
            response = curate_articles(articles, llm, self.domain)

            if not response.curated_articles:
                return articles

            with self._lock:
                self._synthesis = response.synthesis

            enriched: list[Article] = []
            for curated in response.curated_articles:
                idx = curated.article_index
                if 0 <= idx < len(articles):
                    original = articles[idx]
                    enriched.append(
                        original.model_copy(
                            update={
                                "summary": curated.summary,
                                "tags": tuple(curated.tags[:5]),
                                "score": max(original.score, curated.relevance_score),
                            }
                        )
                    )
            return enriched if enriched else articles
        except Exception:
            logger.debug("LLM curation failed in background", exc_info=True)
            return articles

    # -- Status ----------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return not self._done.is_set()

    @property
    def is_done(self) -> bool:
        return self._done.is_set()

    @property
    def result(self) -> list[Article] | None:
        return self._result

    @property
    def synthesis(self) -> str:
        return self._synthesis

    @property
    def error(self) -> str | None:
        return self._error

    def format_status_line(self) -> str:
        """Return a status string for the REPL toolbar."""
        with self._lock:
            phase = self._phase
            count = self._article_count
            curated = self._curated_count
            error = self._error

        elapsed = time.monotonic() - self._started_at
        elapsed_str = f"{elapsed:.0f}s"
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        spinner = _SPINNER_FRAMES[self._frame]

        if phase == "done":
            return f"News: {curated} articles from {self.domain} ({elapsed_str})"
        if phase == "failed":
            return f"News: failed - {error}"
        if phase == "saving":
            return f"{spinner} News: saving {count} articles..."
        if phase == "curating":
            return f"{spinner} News: curating {count} articles with LLM... {elapsed_str}"
        return f"{spinner} News: fetching {self.domain}... {elapsed_str}"

    def _interrupt_prompt(self) -> None:
        """Nudge the prompt to redraw (shows completion in toolbar)."""
        if self._prompt_app is not None:
            import contextlib

            with contextlib.suppress(Exception):
                self._prompt_app.invalidate()
