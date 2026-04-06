"""Composite relevance scoring engine.

Adopted from Last30Days: token-overlap relevance with three-component
weighted scoring (relevance 45%, recency 25%, engagement 30%).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.news.query import ProcessedQuery
    from code_review_agent.news.sources import RawNewsItem

logger = structlog.get_logger(__name__)

# --- Weights (from Last30Days score.py) ---
WEIGHT_RELEVANCE = 0.45
WEIGHT_RECENCY = 0.25
WEIGHT_ENGAGEMENT = 0.30

DATE_PENALTY_LOW = 5
DATE_PENALTY_MED = 2
UNKNOWN_ENGAGEMENT_PENALTY = 3
DEFAULT_ENGAGEMENT = 35
RELEVANCE_THRESHOLD = 0.3

# --- Stopwords for token overlap ---
STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "for",
        "how",
        "is",
        "in",
        "of",
        "on",
        "at",
        "by",
        "it",
        "be",
        "as",
        "do",
        "or",
        "if",
        "so",
        "no",
        "up",
        "my",
        "we",
        "he",
        "i",
        "me",
        "us",
        "am",
        "was",
        "are",
        "has",
        "had",
        "not",
        "but",
        "can",
        "its",
        "our",
        "you",
        "all",
        "did",
        "get",
        "got",
        "let",
        "may",
        "new",
        "now",
        "old",
        "one",
        "out",
        "own",
        "say",
        "she",
        "too",
        "two",
        "way",
    }
)

# --- Low-signal tokens (cap relevance at 0.24 if only these match) ---
LOW_SIGNAL_TOKENS: frozenset[str] = frozenset(
    {
        "advice",
        "best",
        "code",
        "comparison",
        "course",
        "debate",
        "discussion",
        "effect",
        "experience",
        "guide",
        "help",
        "idea",
        "impact",
        "issue",
        "latest",
        "news",
        "odds",
        "opinion",
        "option",
        "overview",
        "pattern",
        "practice",
        "problem",
        "pros",
        "rating",
        "recommendation",
        "resource",
        "result",
        "review",
        "risk",
        "solution",
        "strategy",
        "summary",
        "technique",
        "tip",
        "tips",
        "tool",
        "trend",
        "tutorial",
        "update",
        "worth",
    }
)

# --- Synonym expansion ---
SYNONYMS: dict[str, str] = {
    "js": "javascript",
    "javascript": "js",
    "ts": "typescript",
    "typescript": "ts",
    "react": "reactjs",
    "reactjs": "react",
    "py": "python",
    "python": "py",
    "k8s": "kubernetes",
    "kubernetes": "k8s",
    "ml": "machinelearning",
    "ai": "artificialintelligence",
}


@dataclass(frozen=True)
class ScoredItem:
    """A news item with composite score breakdown."""

    item: RawNewsItem
    relevance: float  # 0.0-1.0
    recency: float  # 0-100
    engagement: float  # 0-100
    overall: int  # 0-100
    why_relevant: str  # human-readable scoring explanation


def token_overlap_relevance(
    query_tokens: tuple[str, ...],
    text: str,
) -> float:
    """Score relevance using token overlap with informative weighting.

    Formula: 0.55 * coverage^1.35 + 0.25 * informative + 0.20 * precision + phrase_bonus
    """
    if not query_tokens:
        return 0.5

    text_lower = text.lower()
    text_tokens = set(re.findall(r"\w+", text_lower)) - STOPWORDS

    # Expand synonyms
    expanded_text = set(text_tokens)
    for t in text_tokens:
        if t in SYNONYMS:
            expanded_text.add(SYNONYMS[t])

    q_set = set(query_tokens) - STOPWORDS
    if not q_set:
        return 0.5

    overlap = q_set & expanded_text

    # Coverage: fraction of query tokens found in text
    coverage = len(overlap) / len(q_set) if q_set else 0.0

    # Informative overlap: fraction of non-low-signal query tokens found
    informative_q = q_set - LOW_SIGNAL_TOKENS
    if informative_q:
        informative = len(informative_q & expanded_text) / len(informative_q)
    else:
        informative = coverage

    # Precision: overlap relative to text size
    precision = len(overlap) / min(len(text_tokens), len(q_set) + 4) if text_tokens else 0.0

    base = 0.55 * (coverage**1.35) + 0.25 * informative + 0.20 * precision

    # Phrase bonus: exact multi-word match
    phrase_bonus = 0.0
    query_phrase = " ".join(query_tokens)
    if len(query_tokens) > 1 and query_phrase in text_lower:
        phrase_bonus = 0.12
    elif len(query_tokens) == 1 and query_tokens[0] in text_lower:
        phrase_bonus = 0.16

    score = base + phrase_bonus

    # Generic word penalty: if only low-signal tokens matched
    if overlap and overlap <= LOW_SIGNAL_TOKENS:
        return float(min(0.24, score))

    return float(min(1.0, score))


def recency_score(published_at: datetime | None) -> float:
    """Score 0-100 based on how recent the item is. Recent = higher."""
    if published_at is None:
        return 50.0

    now = datetime.now()
    age = now - published_at
    hours = max(0, age.total_seconds() / 3600)

    if hours <= 6:
        return 100.0
    if hours <= 24:
        return 90.0 - (hours - 6) * 0.5
    if hours <= 72:
        return 80.0 - (hours - 24) * 0.3
    if hours <= 168:  # 7 days
        return 65.0 - (hours - 72) * 0.15
    if hours <= 720:  # 30 days
        return 50.0 - (hours - 168) * 0.05
    return max(10.0, 30.0 - (hours - 720) * 0.01)


def engagement_score_hn(points: int, comments: int) -> float:
    """HN engagement: 0.55*log1p(points) + 0.45*log1p(comments)."""
    return 0.55 * math.log1p(points) + 0.45 * math.log1p(comments)


def engagement_score_reddit(
    score: int,
    comments: int,
    upvote_ratio: float = 0.0,
    top_comment_score: int = 0,
) -> float:
    """Reddit engagement: weighted log1p of score, comments, ratio, top comment."""
    return (
        0.50 * math.log1p(score)
        + 0.35 * math.log1p(comments)
        + 0.05 * (upvote_ratio * 10)
        + 0.10 * math.log1p(top_comment_score)
    )


def engagement_score_web() -> float:
    """Web search has no engagement data."""
    return 0.0


def normalize_engagement(raw_scores: list[float]) -> list[float]:
    """Min-max scale raw engagement scores to 0-100."""
    if not raw_scores:
        return []
    min_s = min(raw_scores)
    max_s = max(raw_scores)
    rng = max_s - min_s
    if rng < 0.001:
        return [DEFAULT_ENGAGEMENT] * len(raw_scores)
    return [((s - min_s) / rng) * 100 for s in raw_scores]


def score_item(
    query: ProcessedQuery,
    item: RawNewsItem,
    normalized_eng: float | None = None,
) -> ScoredItem:
    """Compute three-component composite score for a single item."""
    rel = token_overlap_relevance(query.core_terms, f"{item.title} {item.summary}")
    rec = recency_score(item.published_at)

    eng = normalized_eng if normalized_eng is not None else DEFAULT_ENGAGEMENT

    rel_score = rel * 100
    overall_raw = WEIGHT_RELEVANCE * rel_score + WEIGHT_RECENCY * rec + WEIGHT_ENGAGEMENT * eng

    # Date confidence penalties
    if item.date_confidence == "low":
        overall_raw -= DATE_PENALTY_LOW
    elif item.date_confidence == "med":
        overall_raw -= DATE_PENALTY_MED

    # Unknown engagement penalty
    if normalized_eng is None:
        overall_raw -= UNKNOWN_ENGAGEMENT_PENALTY

    overall = max(0, min(100, int(overall_raw)))

    why = f"rel={rel:.2f} rec={rec:.0f} eng={eng:.0f} -> {overall}/100 [{item.source}]"

    return ScoredItem(
        item=item,
        relevance=rel,
        recency=rec,
        engagement=eng,
        overall=overall,
        why_relevant=why,
    )


def score_all(
    query: ProcessedQuery,
    items: list[RawNewsItem],
) -> list[ScoredItem]:
    """Score and rank all items. Guarantees at least top 3 if all below threshold."""
    if not items:
        return []

    # Compute raw engagement per source type
    raw_eng: list[float] = []
    for item in items:
        if item.source == "hackernews":
            raw_eng.append(engagement_score_hn(item.score, item.comment_count))
        elif item.source == "reddit":
            raw_eng.append(engagement_score_reddit(item.score, item.comment_count))
        else:
            raw_eng.append(0.0)

    normalized = normalize_engagement(raw_eng)

    scored = [score_item(query, item, eng) for item, eng in zip(items, normalized, strict=False)]

    scored.sort(key=lambda s: s.overall, reverse=True)

    # Minimum guarantee: if all below threshold, keep top 3
    above = [s for s in scored if s.relevance >= RELEVANCE_THRESHOLD]
    if not above and scored:
        logger.debug(
            "all_below_threshold",
            threshold=RELEVANCE_THRESHOLD,
            top_relevance=scored[0].relevance,
            keeping=min(3, len(scored)),
        )
        return scored[:3]

    # Source-balanced interleaving: ensure diversity across sources
    scored = interleave_sources(scored)

    logger.info(
        "scoring_complete",
        total_items=len(items),
        above_threshold=len(above),
        top_score=scored[0].overall if scored else 0,
    )

    return scored


_MIN_PER_SOURCE = 3


def interleave_sources(scored: list[ScoredItem]) -> list[ScoredItem]:
    """Interleave top items from each source to ensure diversity.

    Guarantees at least _MIN_PER_SOURCE items from each source appear
    in the top results (if available). Prevents a single high-engagement
    source from dominating the entire list.
    """
    if not scored:
        return scored

    sources: dict[str, list[ScoredItem]] = {}
    for item in scored:
        sources.setdefault(item.item.source, []).append(item)

    if len(sources) <= 1:
        return scored

    # Take top _MIN_PER_SOURCE from each source
    guaranteed: list[ScoredItem] = []
    guaranteed_ids: set[str] = set()
    for source_items in sources.values():
        for item in source_items[:_MIN_PER_SOURCE]:
            guaranteed.append(item)
            guaranteed_ids.add(item.item.external_id)

    # Fill remaining with score-sorted items not already included
    remaining = [s for s in scored if s.item.external_id not in guaranteed_ids]

    # Merge: guaranteed first (sorted by score), then remaining
    guaranteed.sort(key=lambda s: s.overall, reverse=True)
    result = guaranteed + remaining

    return result
