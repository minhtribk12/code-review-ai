"""SQLite storage for news articles."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    tags TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    content_html TEXT DEFAULT '',
    content_text TEXT DEFAULT '',
    is_read INTEGER DEFAULT 0,
    is_saved INTEGER DEFAULT 0,
    read_position REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_articles_domain ON articles(domain);
CREATE INDEX IF NOT EXISTS idx_articles_saved ON articles(is_saved);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
"""


class ArticleStore:
    """SQLite-backed article storage."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(_SCHEMA)

    def save_articles(self, articles: list[Article]) -> int:
        """Upsert articles. Returns count of inserted/updated."""
        count = 0
        with self._get_connection() as conn:
            for article in articles:
                conn.execute(
                    """
                    INSERT INTO articles (id, domain, title, url, author, published_at,
                        fetched_at, score, comment_count, tags, summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        score = excluded.score,
                        comment_count = excluded.comment_count,
                        summary = CASE WHEN excluded.summary != '' THEN excluded.summary
                                       ELSE articles.summary END
                    """,
                    (
                        article.id,
                        article.domain,
                        article.title,
                        article.url,
                        article.author,
                        article.published_at.isoformat() if article.published_at else None,
                        article.fetched_at.isoformat(),
                        article.score,
                        article.comment_count,
                        ",".join(article.tags),
                        article.summary,
                    ),
                )
                count += 1
        return count

    def load_articles(
        self,
        domain: str | None = None,
        *,
        limit: int = 100,
        saved_only: bool = False,
    ) -> list[Article]:
        """Load articles, optionally filtered by domain."""
        conditions: list[str] = []
        params: list[object] = []

        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if saved_only:
            conditions.append("is_saved = 1")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM articles {where} ORDER BY fetched_at DESC LIMIT ?"  # noqa: S608
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_article(row) for row in rows]

    def mark_read(self, article_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE articles SET is_read = 1 WHERE id = ?", (article_id,))

    def mark_saved(self, article_id: str, *, saved: bool) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE articles SET is_saved = ? WHERE id = ?",
                (1 if saved else 0, article_id),
            )

    def update_read_position(self, article_id: str, position: float) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE articles SET read_position = ? WHERE id = ?",
                (position, article_id),
            )

    def update_content(self, article_id: str, html: str, text: str) -> None:
        """Cache fetched article content."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE articles SET content_html = ?, content_text = ? WHERE id = ?",
                (html, text, article_id),
            )

    def delete_article(self, article_id: str) -> None:
        """Delete a single article by ID."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))

    def delete_articles(self, article_ids: list[str]) -> int:
        """Delete multiple articles by ID. Returns count deleted."""
        if not article_ids:
            return 0
        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in article_ids)
            cursor = conn.execute(
                f"DELETE FROM articles WHERE id IN ({placeholders})",  # noqa: S608
                article_ids,
            )
        return cursor.rowcount

    def mark_all_read(self, domain: str | None = None) -> int:
        """Mark all articles as read, optionally filtered by domain."""
        with self._get_connection() as conn:
            if domain:
                cursor = conn.execute(
                    "UPDATE articles SET is_read = 1 WHERE domain = ? AND is_read = 0",
                    (domain,),
                )
            else:
                cursor = conn.execute("UPDATE articles SET is_read = 1 WHERE is_read = 0")
        return cursor.rowcount

    def clear_content(self, article_id: str) -> None:
        """Reset cached content for an article (force re-fetch)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE articles SET content_html = '', content_text = '' WHERE id = ?",
                (article_id,),
            )

    def get_unread_count(self, domain: str | None = None) -> int:
        with self._get_connection() as conn:
            if domain:
                row = conn.execute(
                    "SELECT COUNT(*) FROM articles WHERE is_read = 0 AND domain = ?",
                    (domain,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM articles WHERE is_read = 0").fetchone()
        return row[0] if row else 0

    def cleanup_old(self, days: int = 30) -> int:
        """Delete unsaved articles older than N days."""
        cutoff = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM articles
                WHERE is_saved = 0
                AND fetched_at < datetime(?, '-' || ? || ' days')
                """,
                (cutoff, days),
            )
        return cursor.rowcount

    def get_stats(self) -> dict[str, dict[str, int]]:
        """Return per-domain stats: fetched, read, saved counts."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT domain,
                       COUNT(*) as total,
                       SUM(is_read) as read_count,
                       SUM(is_saved) as saved_count
                FROM articles
                GROUP BY domain
                ORDER BY domain
                """
            ).fetchall()
        return {
            row["domain"]: {
                "total": row["total"],
                "read": row["read_count"],
                "saved": row["saved_count"],
            }
            for row in rows
        }

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> Article:
        tags_str = row["tags"] or ""
        tags = tuple(t.strip() for t in tags_str.split(",") if t.strip())
        published = None
        if row["published_at"]:
            with contextlib.suppress(ValueError):
                published = datetime.fromisoformat(row["published_at"])
        return Article(
            id=row["id"],
            domain=row["domain"],
            title=row["title"],
            url=row["url"],
            author=row["author"],
            published_at=published,
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            score=row["score"],
            comment_count=row["comment_count"],
            tags=tags,
            summary=row["summary"],
            content_html=row["content_html"],
            content_text=row["content_text"],
            is_read=bool(row["is_read"]),
            is_saved=bool(row["is_saved"]),
            read_position=row["read_position"],
        )
