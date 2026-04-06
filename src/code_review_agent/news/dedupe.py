"""Deduplication: trigram + token Jaccard similarity.

Within-source dedup at 0.7 threshold, cross-source linking at 0.40.
Adopted from Last30Days dedupe.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.news.scoring import ScoredItem

logger = structlog.get_logger(__name__)

WITHIN_SOURCE_THRESHOLD = 0.70
CROSS_SOURCE_THRESHOLD = 0.40
SOCIAL_TRUNCATE_LEN = 100

STRIP_PREFIXES = ("Show HN:", "Ask HN:", "Tell HN:", "Launch HN:")

DEDUPE_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "for",
        "is",
        "in",
        "of",
        "on",
        "and",
        "or",
        "it",
        "at",
        "by",
    }
)


def trigram_jaccard(a: str, b: str) -> float:
    """Character 3-gram Jaccard similarity."""
    if not a or not b:
        return 0.0
    tg_a = _char_trigrams(a)
    tg_b = _char_trigrams(b)
    if not tg_a or not tg_b:
        return 0.0
    intersection = len(tg_a & tg_b)
    union = len(tg_a | tg_b)
    return intersection / union if union > 0 else 0.0


def token_jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity with stopword filtering."""
    tokens_a = set(a.lower().split()) - DEDUPE_STOPWORDS
    tokens_b = set(b.lower().split()) - DEDUPE_STOPWORDS
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def hybrid_similarity(a: str, b: str) -> float:
    """Max of trigram and token Jaccard."""
    return max(trigram_jaccard(a, b), token_jaccard(a, b))


def deduplicate_within(items: list[ScoredItem]) -> list[ScoredItem]:
    """Remove near-duplicates within the same source.

    Items must be pre-sorted by score (descending). Lower-scored
    duplicate is removed at 0.7 similarity threshold.
    """
    if len(items) <= 1:
        return list(items)

    kept: list[ScoredItem] = []
    removed_count = 0

    for item in items:
        text = _normalize_for_comparison(item.item.title, item.item.source)
        is_dup = False
        for existing in kept:
            existing_text = _normalize_for_comparison(existing.item.title, existing.item.source)
            sim = hybrid_similarity(text, existing_text)
            if sim >= WITHIN_SOURCE_THRESHOLD:
                is_dup = True
                logger.debug(
                    "dedup_merge",
                    kept_title=existing.item.title[:60],
                    removed_title=item.item.title[:60],
                    similarity=round(sim, 3),
                    threshold=WITHIN_SOURCE_THRESHOLD,
                    reason="within_source",
                )
                removed_count += 1
                break
        if not is_dup:
            kept.append(item)

    logger.info(
        "dedup_within_complete",
        input_count=len(items),
        output_count=len(kept),
        removed_count=removed_count,
    )
    return kept


def link_cross_source(items: list[ScoredItem]) -> list[ScoredItem]:
    """Annotate items that appear on multiple sources (convergence signal).

    Uses 0.40 hybrid similarity threshold for cross-source linking.
    Adds cross_refs to matching items (modifies via new ScoredItem).
    """
    cross_link_count = 0

    # Build mutable cross_refs mapping
    cross_refs: dict[int, list[str]] = {i: [] for i in range(len(items))}

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i].item.source == items[j].item.source:
                continue
            text_i = _normalize_for_comparison(items[i].item.title, items[i].item.source)
            text_j = _normalize_for_comparison(items[j].item.title, items[j].item.source)
            sim = hybrid_similarity(text_i, text_j)
            if sim >= CROSS_SOURCE_THRESHOLD:
                cross_refs[i].append(items[j].item.source)
                cross_refs[j].append(items[i].item.source)
                cross_link_count += 1
                logger.debug(
                    "cross_source_link",
                    item_a=items[i].item.title[:60],
                    source_a=items[i].item.source,
                    item_b=items[j].item.title[:60],
                    source_b=items[j].item.source,
                    similarity=round(sim, 3),
                )

    # Rebuild items with cross_refs in why_relevant
    result: list[ScoredItem] = []
    for i, item in enumerate(items):
        refs = cross_refs[i]
        if refs:
            from code_review_agent.news.scoring import ScoredItem as SI

            new_why = f"{item.why_relevant} [also on: {', '.join(sorted(set(refs)))}]"
            result.append(
                SI(
                    item=item.item,
                    relevance=item.relevance,
                    recency=item.recency,
                    engagement=item.engagement,
                    overall=item.overall,
                    why_relevant=new_why,
                )
            )
        else:
            result.append(item)

    logger.info(
        "cross_link_complete",
        total_items=len(items),
        cross_links=cross_link_count,
    )
    return result


def _char_trigrams(text: str) -> set[str]:
    """Generate character 3-grams from text."""
    normalized = text.lower().strip()
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[i : i + 3] for i in range(len(normalized) - 2)}


def _normalize_for_comparison(text: str, source: str) -> str:
    """Normalize text for similarity comparison."""
    result = text.strip()
    # Strip HN prefixes
    for prefix in STRIP_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix) :].strip()
    # Truncate social media posts
    if source in ("reddit", "twitter", "bluesky"):
        result = result[:SOCIAL_TRUNCATE_LEN]
    return result.lower()
