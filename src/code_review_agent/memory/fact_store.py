"""Persistent fact store: SQLite-backed memory for review agents.

Inspired by DeerFlow's memory system. Facts are discrete items with
category, confidence, and source tracking. Top N facts are injected
into agent system prompts.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    last_reinforced TEXT NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_confidence ON facts(confidence);
"""


@dataclass(frozen=True)
class Fact:
    """A single remembered fact."""

    id: str
    content: str
    category: str  # project_convention, code_style, false_positive, tech_stack, team_preference
    confidence: float
    created_at: str
    last_reinforced: str
    source: str | None = None


class FactStore:
    """SQLite-backed persistent fact storage."""

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

    def add_fact(
        self,
        content: str,
        category: str,
        *,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> str:
        """Add a new fact. Returns the fact ID. Deduplicates by content."""
        # Check for duplicate (whitespace-normalized)
        normalized = " ".join(content.split())
        existing = self._find_similar(normalized)
        if existing:
            self.reinforce(existing.id)
            return existing.id

        fact_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO facts
                (id, content, category, confidence, created_at, last_reinforced, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (fact_id, normalized, category, confidence, now, now, source),
            )
        logger.debug("fact_added", id=fact_id, category=category, content=normalized[:60])
        return fact_id

    def reinforce(self, fact_id: str) -> None:
        """Reinforce a fact (seen again). Bumps confidence and timestamp."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE facts
                SET confidence = MIN(1.0, confidence + 0.1),
                    last_reinforced = ?
                WHERE id = ?
                """,
                (now, fact_id),
            )

    def decay_all(self, amount: float = 0.05) -> int:
        """Reduce confidence of all facts. Returns count affected."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE facts
                SET confidence = MAX(0.0, confidence - ?)
                WHERE confidence > 0
                """,
                (amount,),
            )
            # Remove facts below threshold
            conn.execute("DELETE FROM facts WHERE confidence <= 0.1")
        return cursor.rowcount

    def get_top_facts(self, limit: int = 10) -> list[Fact]:
        """Return top facts by confidence, most relevant first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM facts ORDER BY confidence DESC, last_reinforced DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def get_facts_by_category(self, category: str, limit: int = 10) -> list[Fact]:
        """Return facts in a specific category."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY confidence DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def remove_fact(self, fact_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))

    def count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return row[0] if row else 0

    def _find_similar(self, normalized_content: str) -> Fact | None:
        """Find existing fact with same normalized content."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE content = ? LIMIT 1",
                (normalized_content,),
            ).fetchone()
        return self._row_to_fact(row) if row else None

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> Fact:
        return Fact(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            last_reinforced=row["last_reinforced"],
            source=row["source"],
        )


def format_facts_for_prompt(facts: list[Fact]) -> str:
    """Format facts for injection into agent system prompts."""
    if not facts:
        return ""
    lines = ["<memory>", "Known facts about this project and user:"]
    for f in facts:
        lines.append(f"  - [{f.category}] {f.content}")
    lines.append("</memory>")
    return "\n".join(lines)
