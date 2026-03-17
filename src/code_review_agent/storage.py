"""Review history storage backed by SQLite.

Stores ReviewReport metadata in indexed columns for fast queries, with the
full report JSON preserved for complete retrieval. Individual findings are
stored as first-class rows with triage and posting state. Uses WAL mode
for safe concurrent access between TUI and CLI.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from code_review_agent.models import ReviewReport

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = "~/.cra/reviews.db"

_SCHEMA_VERSION = 4

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL DEFAULT 4,
    reviewed_at TEXT NOT NULL,
    repo TEXT,
    pr_number INTEGER,
    pr_url TEXT,
    pr_title TEXT,
    risk_level TEXT NOT NULL,
    overall_summary TEXT,
    -- Finding counts (queryable without parsing JSON)
    total_findings INTEGER NOT NULL DEFAULT 0,
    critical_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    medium_count INTEGER NOT NULL DEFAULT 0,
    low_count INTEGER NOT NULL DEFAULT 0,
    -- Token and cost tracking
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    -- Execution context
    llm_model TEXT,
    token_tier TEXT,
    dedup_strategy TEXT,
    total_execution_seconds REAL,
    files_reviewed INTEGER NOT NULL DEFAULT 0,
    agents_count INTEGER NOT NULL DEFAULT 0,
    agents_used TEXT,
    rounds_completed INTEGER NOT NULL DEFAULT 1,
    -- Full report for complete retrieval
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    finding_count INTEGER NOT NULL DEFAULT 0,
    critical_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    medium_count INTEGER NOT NULL DEFAULT 0,
    low_count INTEGER NOT NULL DEFAULT 0,
    execution_time_seconds REAL NOT NULL DEFAULT 0.0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    finding_index INTEGER NOT NULL,
    severity TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    file_path TEXT,
    line_number INTEGER,
    suggestion TEXT,
    confidence TEXT NOT NULL DEFAULT 'medium',
    repo TEXT,
    pr_number INTEGER,
    triage_action TEXT NOT NULL DEFAULT 'none',
    is_posted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(review_id, finding_index)
);

CREATE TABLE IF NOT EXISTS finding_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_reviews_repo ON reviews(repo);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewed_at ON reviews(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_reviews_risk_level ON reviews(risk_level);
CREATE INDEX IF NOT EXISTS idx_reviews_llm_model ON reviews(llm_model);
CREATE INDEX IF NOT EXISTS idx_agent_results_review
    ON agent_results(review_id);
CREATE INDEX IF NOT EXISTS idx_agent_results_name
    ON agent_results(agent_name);
CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
CREATE INDEX IF NOT EXISTS idx_findings_triage ON findings(triage_action);
CREATE INDEX IF NOT EXISTS idx_findings_repo ON findings(repo);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
"""

_MIGRATE_V1_TO_V2 = """
ALTER TABLE reviews ADD COLUMN pr_title TEXT;
ALTER TABLE reviews ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN llm_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN llm_model TEXT;
ALTER TABLE reviews ADD COLUMN token_tier TEXT;
ALTER TABLE reviews ADD COLUMN dedup_strategy TEXT;
ALTER TABLE reviews ADD COLUMN total_execution_seconds REAL;
ALTER TABLE reviews ADD COLUMN files_reviewed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN agents_count INTEGER NOT NULL DEFAULT 0;
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
        """Create tables, indexes, and run migrations if needed."""
        with self._get_connection() as conn:
            conn.executescript(_CREATE_TABLES + _CREATE_INDEXES)
            self._migrate(conn)
            logger.debug("review storage initialized", path=str(self._db_path))

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Run schema migrations for existing databases."""
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        # v1 -> v2: add missing columns to reviews, create agent_results
        if "agent_results" not in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()}
            if "prompt_tokens" not in columns:
                logger.info("migrating review storage from v1 to v2")
                for statement in _MIGRATE_V1_TO_V2.strip().split(";"):
                    statement = statement.strip()
                    if statement:
                        with contextlib.suppress(sqlite3.OperationalError):
                            conn.execute(statement)

        # v3 -> v4: migrate finding_triage into findings table
        if "finding_triage" in tables and "findings" in tables:
            self._migrate_v3_to_v4(conn)

        # Backfill: populate findings from report_json if table exists but is empty
        if "findings" in tables:
            self._backfill_findings(conn)

    def _migrate_v3_to_v4(self, conn: sqlite3.Connection) -> None:
        """Migrate from finding_triage to the findings table.

        Parses report_json for each review, flattens findings into the
        findings table, and merges triage/posted state from finding_triage.
        """
        # Check if findings table already has data (already migrated)
        count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        if count > 0:
            # Already migrated, just clean up old table
            conn.execute("DROP TABLE IF EXISTS finding_triage")
            return

        reviews = conn.execute(
            "SELECT id, repo, pr_url, report_json FROM reviews WHERE total_findings > 0"
        ).fetchall()

        migrated_count = 0
        for review in reviews:
            try:
                self._migrate_review_findings(conn, dict(review))
                migrated_count += 1
            except Exception as exc:
                logger.warning(
                    "skipping review during v3->v4 migration",
                    review_id=review["id"],
                    error=str(exc),
                )

        conn.execute("DROP TABLE IF EXISTS finding_triage")

        if migrated_count:
            logger.info(
                "migrated findings from v3 to v4",
                reviews_migrated=migrated_count,
            )

    def _migrate_review_findings(
        self,
        conn: sqlite3.Connection,
        review: dict[str, Any],
    ) -> None:
        """Migrate a single review's findings from report_json to findings table."""
        from code_review_agent.github_client import parse_pr_reference
        from code_review_agent.models import ReviewReport

        report = ReviewReport.model_validate_json(review["report_json"])
        review_id = review["id"]
        repo = review["repo"]
        pr_number: int | None = None

        if report.pr_url:
            with contextlib.suppress(ValueError):
                _, _, pr_number = parse_pr_reference(report.pr_url)

        # Load existing triage data
        triage_rows = conn.execute(
            "SELECT finding_index, triage_action, is_posted "
            "FROM finding_triage WHERE review_id = ?",
            (review_id,),
        ).fetchall()
        triage_map = {row["finding_index"]: dict(row) for row in triage_rows}

        idx = 0
        for result in report.agent_results:
            for finding in result.findings:
                triage = triage_map.get(idx, {})
                conn.execute(
                    """
                    INSERT OR IGNORE INTO findings (
                        review_id, finding_index, severity, agent_name,
                        category, title, description, file_path, line_number,
                        suggestion, confidence, repo, pr_number,
                        triage_action, is_posted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        idx,
                        str(finding.severity),
                        result.agent_name,
                        finding.category,
                        finding.title,
                        finding.description,
                        finding.file_path,
                        finding.line_number,
                        finding.suggestion,
                        str(finding.confidence),
                        repo,
                        pr_number,
                        triage.get("triage_action", "none"),
                        triage.get("is_posted", 0),
                    ),
                )
                idx += 1

    def _backfill_findings(self, conn: sqlite3.Connection) -> None:
        """Populate findings table from report_json for reviews that have none.

        Handles the case where migration failed or old reviews exist without
        corresponding findings rows.
        """
        from code_review_agent.models import ReviewReport

        reviews = conn.execute(
            """
            SELECT r.id, r.repo, r.pr_url, r.report_json
            FROM reviews r
            WHERE r.total_findings > 0
              AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.review_id = r.id)
            """
        ).fetchall()

        if not reviews:
            return

        backfilled = 0
        for review in reviews:
            try:
                report = ReviewReport.model_validate_json(review["report_json"])
                pr_number = _extract_pr_number(review["pr_url"])
                repo = review["repo"]
                review_id = review["id"]

                idx = 0
                for result in report.agent_results:
                    for finding in result.findings:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO findings (
                                review_id, finding_index, severity, agent_name,
                                category, title, description, file_path,
                                line_number, suggestion, confidence, repo,
                                pr_number
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                review_id,
                                idx,
                                str(finding.severity),
                                result.agent_name,
                                finding.category,
                                finding.title,
                                finding.description,
                                finding.file_path,
                                finding.line_number,
                                finding.suggestion,
                                str(finding.confidence),
                                repo,
                                pr_number,
                            ),
                        )
                        idx += 1
                backfilled += 1
            except Exception:
                logger.debug(
                    "failed to backfill findings for review",
                    review_id=review["id"],
                )

        if backfilled:
            logger.info("backfilled findings from report_json", count=backfilled)

    # -- Save --

    def save(
        self,
        report: ReviewReport,
        repo: str | None = None,
        *,
        llm_model: str | None = None,
        token_tier: str | None = None,
        dedup_strategy: str | None = None,
    ) -> int:
        """Save a review report, per-agent results, and individual findings.

        Returns the review ID.
        """
        findings = report.total_findings
        pr_number = _extract_pr_number(report.pr_url)

        usage = report.token_usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0
        llm_calls = usage.llm_calls if usage else 0
        cost = usage.estimated_cost_usd if usage else None

        agents = ",".join(r.agent_name for r in report.agent_results)
        agents_count = len(report.agent_results)
        total_exec = sum(r.execution_time_seconds for r in report.agent_results)
        files_reviewed = 0
        report_json = report.model_dump_json(indent=2)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reviews (
                    schema_version, reviewed_at, repo, pr_number, pr_url,
                    pr_title, risk_level, overall_summary, total_findings,
                    critical_count, high_count, medium_count, low_count,
                    prompt_tokens, completion_tokens, total_tokens, llm_calls,
                    estimated_cost_usd, llm_model, token_tier, dedup_strategy,
                    total_execution_seconds, files_reviewed, agents_count,
                    agents_used, rounds_completed, report_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    _SCHEMA_VERSION,
                    report.reviewed_at.isoformat(),
                    repo,
                    pr_number,
                    report.pr_url,
                    getattr(report, "pr_title", None),
                    str(report.risk_level),
                    report.overall_summary,
                    sum(findings.values()),
                    findings.get("critical", 0),
                    findings.get("high", 0),
                    findings.get("medium", 0),
                    findings.get("low", 0),
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    llm_calls,
                    cost,
                    llm_model,
                    token_tier,
                    dedup_strategy,
                    total_exec,
                    files_reviewed,
                    agents_count,
                    agents,
                    report.rounds_completed,
                    report_json,
                ),
            )
            review_id = cursor.lastrowid or 0

            # Save per-agent results
            for result in report.agent_results:
                agent_findings = {"critical": 0, "high": 0, "medium": 0, "low": 0}
                for f in result.findings:
                    sev = str(f.severity)
                    if sev in agent_findings:
                        agent_findings[sev] += 1

                conn.execute(
                    """
                    INSERT INTO agent_results (
                        review_id, agent_name, status, finding_count,
                        critical_count, high_count, medium_count, low_count,
                        execution_time_seconds, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        result.agent_name,
                        str(result.status),
                        len(result.findings),
                        agent_findings["critical"],
                        agent_findings["high"],
                        agent_findings["medium"],
                        agent_findings["low"],
                        result.execution_time_seconds,
                        result.error_message,
                    ),
                )

            # Save individual findings
            self._save_findings(conn, review_id, report, repo, pr_number)

        logger.info(
            "review saved",
            id=review_id,
            repo=repo,
            agents=agents_count,
            findings=sum(findings.values()),
        )
        return review_id

    def _save_findings(
        self,
        conn: sqlite3.Connection,
        review_id: int,
        report: ReviewReport,
        repo: str | None,
        pr_number: int | None,
    ) -> None:
        """Bulk-insert individual findings from a report."""
        idx = 0
        for result in report.agent_results:
            for finding in result.findings:
                conn.execute(
                    """
                    INSERT INTO findings (
                        review_id, finding_index, severity, agent_name,
                        category, title, description, file_path, line_number,
                        suggestion, confidence, repo, pr_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        idx,
                        str(finding.severity),
                        result.agent_name,
                        finding.category,
                        finding.title,
                        finding.description,
                        finding.file_path,
                        finding.line_number,
                        finding.suggestion,
                        str(finding.confidence),
                        repo,
                        pr_number,
                    ),
                )
                idx += 1

    # -- Query --

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

    def get_agent_stats(self, *, days: int = 30) -> list[dict[str, Any]]:
        """Get per-agent performance stats over a time window."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    ar.agent_name,
                    COUNT(*) as runs,
                    SUM(CASE WHEN ar.status = 'success' THEN 1 ELSE 0 END)
                        as success_count,
                    SUM(CASE WHEN ar.status = 'failed' THEN 1 ELSE 0 END)
                        as failed_count,
                    COALESCE(SUM(ar.finding_count), 0) as total_findings,
                    COALESCE(SUM(ar.critical_count), 0) as total_critical,
                    COALESCE(SUM(ar.high_count), 0) as total_high,
                    COALESCE(SUM(ar.medium_count), 0) as total_medium,
                    COALESCE(SUM(ar.low_count), 0) as total_low,
                    COALESCE(AVG(ar.execution_time_seconds), 0.0)
                        as avg_execution_seconds,
                    COALESCE(AVG(ar.finding_count), 0.0)
                        as avg_findings_per_run
                FROM agent_results ar
                JOIN reviews r ON ar.review_id = r.id
                WHERE r.reviewed_at >= datetime('now', ?)
                GROUP BY ar.agent_name
                ORDER BY total_findings DESC
                """,
                (f"-{days} days",),
            ).fetchall()

        return [dict(row) for row in rows]

    def export_json(self, *, repo: str | None = None, limit: int = 1000) -> str:
        """Export reviews as a JSON array string."""
        reviews = self.list_reviews(repo=repo, limit=limit)
        return json.dumps(reviews, indent=2, default=str)

    # -- Findings --

    def load_unsolved_findings(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Load all unsolved findings across all reviews.

        Returns findings where triage_action is NOT 'solved', ordered by
        severity (critical first) then by creation time (newest first).
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT f.*, r.reviewed_at, r.pr_url
                FROM findings f
                JOIN reviews r ON f.review_id = r.id
                WHERE f.triage_action != 'solved'
                ORDER BY
                    CASE f.severity
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                    END,
                    r.reviewed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_findings_for_review(self, review_id: int) -> list[dict[str, Any]]:
        """Load all findings for a specific review."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT f.*, r.reviewed_at, r.pr_url
                FROM findings f
                JOIN reviews r ON f.review_id = r.id
                WHERE f.review_id = ?
                ORDER BY f.finding_index
                """,
                (review_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_finding_triage(self, finding_id: int, triage_action: str) -> None:
        """Update triage action on a finding by its primary key."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE findings SET triage_action = ? WHERE id = ?",
                (triage_action, finding_id),
            )

    def update_finding_posted(self, finding_id: int, *, is_posted: bool) -> None:
        """Update posted status on a finding by its primary key."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE findings SET is_posted = ? WHERE id = ?",
                (int(is_posted), finding_id),
            )

    # -- Settings --

    def save_finding_setting(self, key: str, value: str) -> None:
        """Persist a findings navigator setting (e.g. visible columns)."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO finding_settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def load_finding_setting(self, key: str) -> str | None:
        """Load a findings navigator setting by key."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM finding_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

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
