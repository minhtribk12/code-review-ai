"""Background news intelligence pipeline.

Runs the full Last30Days-inspired pipeline in a daemon thread:
1. Query preprocessing
2. Parallel multi-source fetching (HN + Reddit)
3. Local scoring + deduplication (no I/O)
4. LLM synthesis on pre-scored items
5. Save to SQLite

Shows progress in the toolbar status bar.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from code_review_agent.interactive.session import SessionState
    from code_review_agent.news.models import Article
    from code_review_agent.news.query import ProcessedQuery
    from code_review_agent.news.scoring import ScoredItem
    from code_review_agent.news.sources import RawNewsItem

logger = structlog.get_logger(__name__)

_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
_DEFAULT_DB = Path("~/.cra/reviews.db").expanduser()


class BackgroundNewsFetch:
    """Non-blocking news intelligence pipeline.

    The REPL toolbar reads ``format_status_line()`` for progress display.
    Pipeline phases: query -> fetch(parallel) -> score -> dedup -> curate -> save
    """

    def __init__(self, domain: str, session: SessionState) -> None:
        self.domain = domain
        self._session = session
        self._fetch_id = uuid.uuid4().hex[:8]
        self._done = threading.Event()
        self._lock = threading.Lock()

        # Progress tracking
        self._phase = "preprocessing"
        self._sources_done = 0
        self._sources_total = 0
        self._source_status: dict[str, str] = {}
        self._raw_count = 0
        self._scored_count = 0
        self._deduped_count = 0
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
        """Execute the full pipeline."""
        bound_logger = logger.bind(fetch_id=self._fetch_id)
        bound_logger.info("news_pipeline_started", domain=self.domain)

        try:
            # Phase 1: Preprocess query
            from code_review_agent.news.query import preprocess_query

            query = preprocess_query(self.domain)
            with self._lock:
                self._phase = "fetching"

            # Phase 2: Parallel source fetching
            raw_items = self._fetch_sources(query)
            with self._lock:
                self._raw_count = len(raw_items)
                if not raw_items:
                    self._phase = "done"
                    self._curated_count = 0
                    self._done.set()
                    self._interrupt_prompt()
                    bound_logger.info("news_pipeline_complete", domain=self.domain, raw_items=0)
                    return
                self._phase = "scoring"

            # Phase 3: Score + normalize (pure, no I/O)
            from code_review_agent.news.scoring import score_all

            scored = score_all(query, raw_items)
            with self._lock:
                self._scored_count = len(scored)
                self._phase = "deduplicating"

            # Phase 4: Deduplicate (pure, no I/O)
            from code_review_agent.news.dedupe import deduplicate_within, link_cross_source

            deduped = deduplicate_within(scored)
            deduped = link_cross_source(deduped)
            with self._lock:
                self._deduped_count = len(deduped)
                self._phase = "curating"

            # Phase 5: LLM synthesis on top 15
            articles = self._curate_and_convert(deduped[:15])
            with self._lock:
                self._curated_count = len(articles)
                self._phase = "saving"

            # Phase 6: Save to SQLite
            from code_review_agent.news.storage import ArticleStore

            store = ArticleStore(db_path=_DEFAULT_DB)
            store.save_articles(articles)

            with self._lock:
                self._result = articles
                self._phase = "done"

            elapsed = time.monotonic() - self._started_at
            bound_logger.info(
                "news_pipeline_complete",
                domain=self.domain,
                sources_succeeded=self._sources_done,
                raw_items=self._raw_count,
                scored_items=self._scored_count,
                deduped_items=self._deduped_count,
                curated_items=self._curated_count,
                total_elapsed_s=round(elapsed, 1),
            )
        except Exception as exc:
            bound_logger.error(
                "news_pipeline_failed",
                domain=self.domain,
                phase=self._phase,
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
            )
            with self._lock:
                self._error = str(exc)[:200]
                self._phase = "failed"
        finally:
            self._done.set()
            self._interrupt_prompt()

    def _fetch_sources(self, query: ProcessedQuery) -> list[RawNewsItem]:
        """Fetch from all available sources in parallel."""
        from code_review_agent.news.sources import hackernews, reddit
        from code_review_agent.news.sources import web as web_source

        def _hn() -> list[RawNewsItem]:
            return hackernews.fetch(query, timeout=30)

        def _reddit() -> list[RawNewsItem]:
            return reddit.fetch(query, timeout=30)

        def _web() -> list[RawNewsItem]:
            return web_source.fetch(query, timeout=30)

        source_fns: dict[str, Callable[[], list[RawNewsItem]]] = {
            "hackernews": _hn,
            "reddit": _reddit,
            "web": _web,
        }

        with self._lock:
            self._sources_total = len(source_fns)

        all_items: list[RawNewsItem] = []

        with ThreadPoolExecutor(max_workers=len(source_fns)) as pool:
            future_to_source: dict[Future[list[RawNewsItem]], str] = {
                pool.submit(fn): name for name, fn in source_fns.items()
            }

            for future in as_completed(future_to_source, timeout=60):
                source_name = future_to_source[future]
                try:
                    items = future.result()
                    all_items.extend(items)
                    with self._lock:
                        self._sources_done += 1
                        self._source_status[source_name] = f"ok ({len(items)})"
                        self._raw_count = len(all_items)
                    logger.debug(
                        "source_fetch_done",
                        fetch_id=self._fetch_id,
                        source=source_name,
                        items=len(items),
                    )
                except Exception as exc:
                    with self._lock:
                        self._sources_done += 1
                        self._source_status[source_name] = f"failed: {exc!s}"
                    logger.warning(
                        "source_fetch_failed",
                        fetch_id=self._fetch_id,
                        source=source_name,
                        error=str(exc)[:100],
                    )

        return all_items

    def _curate_and_convert(self, scored_items: list[ScoredItem]) -> list[Article]:
        """Run LLM synthesis on scored items, convert to Article models."""
        from datetime import datetime

        from code_review_agent.news.models import Article

        # Convert scored items to Article format
        articles: list[Article] = []
        for scored in scored_items:
            item = scored.item
            why = scored.why_relevant
            overall = scored.overall

            # Build summary with scoring context
            summary_parts: list[str] = []
            if item.summary:
                summary_parts.append(item.summary)
            if item.top_comments:
                summary_parts.append(f"Top comment: {item.top_comments[0]}")

            articles.append(
                Article(
                    id=f"{item.source}:{item.external_id}",
                    domain=item.source,
                    title=item.title,
                    url=item.url,
                    author=item.author,
                    published_at=item.published_at,
                    fetched_at=datetime.now(),
                    score=overall,
                    comment_count=item.comment_count,
                    tags=item.tags,
                    summary=" | ".join(summary_parts) if summary_parts else why,
                )
            )

        # Try LLM synthesis for enriched summaries
        try:
            from code_review_agent.llm_client import LLMClient
            from code_review_agent.news.curator import curate_articles

            settings = self._session.effective_settings
            llm = LLMClient(settings)
            response = curate_articles(articles, llm, self.domain)

            if response.curated_articles:
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
                if enriched:
                    return enriched
        except Exception:
            logger.debug("llm_synthesis_skipped", fetch_id=self._fetch_id, exc_info=True)

        return articles

    # --- Status for toolbar ---

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
            src_done = self._sources_done
            src_total = self._sources_total
            raw = self._raw_count
            scored = self._scored_count
            deduped = self._deduped_count
            curated = self._curated_count
            error = self._error

        elapsed = time.monotonic() - self._started_at
        elapsed_str = f"{elapsed:.0f}s"
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        sp = _SPINNER_FRAMES[self._frame]

        if phase == "done":
            return f"News: {curated} curated ({raw} raw, {src_done} sources, {elapsed_str})"
        if phase == "failed":
            return f"News: failed - {error}"
        if phase == "saving":
            return f"{sp} News: saving {curated} articles... {elapsed_str}"
        if phase == "curating":
            return (
                f"{sp} News: LLM synthesis on {deduped} items... "
                f"[{src_done}/{src_total} sources] {elapsed_str}"
            )
        if phase == "deduplicating":
            return f"{sp} News: deduplicating {scored} items... {elapsed_str}"
        if phase == "scoring":
            return f"{sp} News: scoring {raw} items... {elapsed_str}"
        if phase == "fetching":
            return (
                f"{sp} News: fetching {self.domain} "
                f"[{src_done}/{src_total} sources, {raw} items] {elapsed_str}"
            )
        return f"{sp} News: preparing query... {elapsed_str}"

    def _interrupt_prompt(self) -> None:
        """Nudge the prompt to redraw."""
        if self._prompt_app is not None:
            with contextlib.suppress(Exception):
                self._prompt_app.invalidate()
