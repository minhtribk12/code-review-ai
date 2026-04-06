"""Query preprocessor: strip noise, detect compounds, prepare per-source queries.

Three-phase extraction adopted from Last30Days:
1. Multi-word prefix stripping (longest-first)
2. Noise word filtering (42+ words)
3. Compound term detection (title-case, hyphenated)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

MULTI_WORD_PREFIXES: tuple[str, ...] = (
    "what are the best",
    "what is the best",
    "what are the latest",
    "how do i use",
    "how to use",
    "how to",
    "what are",
    "what is",
    "tips for",
    "best practices for",
    "tell me about",
    "show me",
    "find me",
    "search for",
)

NOISE_WORDS: frozenset[str] = frozenset(
    {
        # Articles / determiners
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        # Q words
        "what",
        "how",
        "why",
        "when",
        "where",
        "which",
        "who",
        # Meta descriptors
        "best",
        "top",
        "latest",
        "new",
        "recent",
        "popular",
        "trending",
        "good",
        "great",
        "awesome",
        "amazing",
        "cool",
        # Action words
        "use",
        "try",
        "build",
        "make",
        "create",
        "get",
        "find",
        # Filler
        "about",
        "really",
        "very",
        "just",
        "some",
        "any",
        "all",
        "do",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "be",
        "for",
        "with",
        "and",
        "or",
        "but",
        "in",
        "on",
        "of",
        "to",
    }
)

# Compound term pattern: title-case multi-word ("Claude Code", "Hacker News")
_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
# Hyphenated compound: "multi-agent", "real-time"
_HYPHENATED_RE = re.compile(r"\b(\w+-\w+(?:-\w+)*)\b")


@dataclass(frozen=True)
class ProcessedQuery:
    """Preprocessed query ready for multi-source search."""

    raw: str
    core_terms: tuple[str, ...]
    quoted_phrases: tuple[str, ...]
    hn_query: str
    reddit_query: str
    web_query: str


def preprocess_query(raw: str) -> ProcessedQuery:
    """Strip noise, detect compounds, prepare per-source queries."""
    if not raw or not raw.strip():
        return ProcessedQuery(
            raw=raw,
            core_terms=(),
            quoted_phrases=(),
            hn_query="",
            reddit_query="",
            web_query="",
        )

    text = raw.strip()

    # Phase 0: Detect compound terms BEFORE lowercasing
    compounds = _detect_compounds(text)

    # Phase 1: Strip multi-word prefixes (longest-first)
    text = _strip_prefix(text)

    # Phase 2: Filter noise words
    tokens = text.lower().split()
    filtered = _filter_noise(tokens)

    if not filtered:
        # All words were noise -- use original minus prefix
        filtered = [t for t in text.lower().split() if len(t) > 1]

    core_terms = tuple(filtered)
    quoted_phrases = tuple(compounds)

    # Build per-source queries
    core_str = " ".join(core_terms)
    hn_query = core_str
    reddit_query = core_str
    web_query = " ".join(f'"{c}"' if " " in c else c for c in compounds) if compounds else core_str

    logger.debug(
        "query_preprocessed",
        raw=raw,
        core_terms=core_terms,
        quoted_phrases=quoted_phrases,
    )

    return ProcessedQuery(
        raw=raw,
        core_terms=core_terms,
        quoted_phrases=quoted_phrases,
        hn_query=hn_query,
        reddit_query=reddit_query,
        web_query=web_query,
    )


def _strip_prefix(text: str) -> str:
    """Strip the longest matching multi-word prefix."""
    lower = text.lower()
    for prefix in sorted(MULTI_WORD_PREFIXES, key=len, reverse=True):
        if lower.startswith(prefix):
            stripped = text[len(prefix) :].strip()
            return stripped if stripped else text
    return text


def _detect_compounds(text: str) -> list[str]:
    """Detect compound terms that should be quoted in search."""
    compounds: list[str] = []

    # Title-case multi-word: "Claude Code", "Hacker News"
    for match in _TITLE_CASE_RE.finditer(text):
        compound = match.group(1)
        if len(compound.split()) <= 4:
            compounds.append(compound)

    # Hyphenated: "multi-agent", "real-time"
    for match in _HYPHENATED_RE.finditer(text):
        compounds.append(match.group(1))

    return compounds


def _filter_noise(tokens: list[str]) -> list[str]:
    """Remove noise words from token list."""
    return [t for t in tokens if t not in NOISE_WORDS and len(t) > 1]
