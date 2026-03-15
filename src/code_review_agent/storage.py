"""Review history storage backed by SQLite.

Stores ReviewReport metadata in indexed columns for fast queries, with the
full report JSON preserved for complete retrieval. Uses WAL mode for safe
concurrent access between TUI and CLI.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from code_review_agent.models import ReviewReport

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = "~/.cra/reviews.db"

_SCHEMA_VERSION = 1

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    reviewed_at TEXT NOT NULL,
    repo TEXT,
    pr_number INTEGER,
    pr_url TEXT,
    risk_level TEXT NOT NULL,
    overall_summary TEXT,
    total_findings INTEGER NOT NULL DEFAULT 0,
    critical_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    medium_count INTEGER NOT NULL DEFAULT 0,
    low_count INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    agents_used TEXT,
    report_json TEXT NOT NULL
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_reviews_repo ON reviews(repo);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewed_at ON reviews(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_reviews_risk_level ON reviews(risk_level);
"""


class ReviewStorage:
    """SQLite-backed review history storage."""

    def __init__(self, db_path: str | None = None) -> None:
        resolved = os.path.expanduser(db_path or _DEFAULT_DB_PATH)
        self._db_path = Path(resolved)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._get_connection() as conn:
            conn.executescript(_CREATE_TABLE + _CREATE_INDEXES)
            logger.debug("review storage initialized", path=str(self._db_path))

    def save(self, report: ReviewReport, repo: str | None = None) -> int:
        """Save a review report and return its ID."""
        findings = report.total_findings
        pr_number = _extract_pr_number(report.pr_url)

        token_usage = report.token_usage
        total_tokens = token_usage.total_tokens if token_usage else 0
        cost = token_usage.estimated_cost_usd if token_usage else None

        agents = ",".join(r.agent_name for r in report.agent_results)
        report_json = report.model_dump_json(indent=2)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reviews (
                    schema_version, reviewed_at, repo, pr_number, pr_url,
                    risk_level, overall_summary, total_findings,
                    critical_count, high_count, medium_count, low_count,
                    total_tokens, estimated_cost_usd, agents_used, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _SCHEMA_VERSION,
                    report.reviewed_at.isoformat(),
                    repo,
                    pr_number,
                    report.pr_url,
                    str(report.risk_level),
                    report.overall_summary,
                    sum(findings.values()),
                    findings.get("critical", 0),
                    findings.get("high", 0),
                    findings.get("medium", 0),
                    findings.get("low", 0),
                    total_tokens,
                    cost,
                    agents,
                    report_json,
                ),
            )
            review_id = cursor.lastrowid or 0

        logger.info("review saved", id=review_id, repo=repo)
        return review_id

    def list_reviews(
        self,
        *,
        repo: str | None = None,
        days: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent reviews with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if repo:
            conditions.append("repo = ?")
            params.append(repo)
        if days:
            conditions.append("reviewed_at >= datetime('now', ?)")
            params.append(f"-{days} days")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, reviewed_at, repo, pr_number, risk_level,
                       total_findings, critical_count, high_count,
                       medium_count, low_count, total_tokens,
                       estimated_cost_usd, agents_used
                FROM reviews
                {where}
                ORDER BY reviewed_at DESC
                LIMIT ?
                """,  # noqa: S608
                [*params, limit],
            ).fetchall()

        return [dict(row) for row in rows]

    def get_review(self, review_id: int) -> dict[str, Any] | None:
        """Get a single review by ID, including full report JSON."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE id = ?",
                (review_id,),
            ).fetchone()

        if row is None:
            return None
        return dict(row)

    def get_trends(
        self,
        *,
        repo: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get aggregated trend data over a time window."""
        conditions = ["reviewed_at >= datetime('now', ?)"]
        params: list[Any] = [f"-{days} days"]

        if repo:
            conditions.append("repo = ?")
            params.append(repo)

        where = f"WHERE {' AND '.join(conditions)}"

        with self._get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) as review_count,
                    COALESCE(SUM(total_findings), 0) as total_findings,
                    COALESCE(SUM(critical_count), 0) as total_critical,
                    COALESCE(SUM(high_count), 0) as total_high,
                    COALESCE(SUM(medium_count), 0) as total_medium,
                    COALESCE(SUM(low_count), 0) as total_low,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost,
                    COALESCE(AVG(total_findings), 0.0) as avg_findings,
                    COALESCE(AVG(total_tokens), 0.0) as avg_tokens,
                    COALESCE(AVG(estimated_cost_usd), 0.0) as avg_cost
                FROM reviews
                {where}
                """,  # noqa: S608
                params,
            ).fetchone()

            if row is None:
                return {"review_count": 0}

            result = dict(row)

            # Risk level distribution
            risk_rows = conn.execute(
                f"""
                SELECT risk_level, COUNT(*) as count
                FROM reviews
                {where}
                GROUP BY risk_level
                ORDER BY count DESC
                """,  # noqa: S608
                params,
            ).fetchall()
            result["risk_distribution"] = {r["risk_level"]: r["count"] for r in risk_rows}

        return result

    def export_json(self, *, repo: str | None = None, limit: int = 1000) -> str:
        """Export reviews as a JSON array string."""
        import json

        reviews = self.list_reviews(repo=repo, limit=limit)
        return json.dumps(reviews, indent=2, default=str)

    @property
    def db_path(self) -> Path:
        """Return the database file path."""
        return self._db_path


def _extract_pr_number(pr_url: str | None) -> int | None:
    """Extract PR number from a GitHub PR URL."""
    if pr_url is None:
        return None
    parts = pr_url.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "pull":
        try:
            return int(parts[-1])
        except ValueError:
            pass
    return None
