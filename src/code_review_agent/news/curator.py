"""LLM-powered news intelligence: multi-source synthesis and analysis.

Produces professional-grade intelligence briefs from raw RSS articles.
The LLM acts as a senior tech analyst, cross-referencing sources,
identifying trends, and explaining why each story matters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.llm_client import LLMClient
    from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt engineering: persona + chain-of-thought + structured output
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior technology intelligence analyst producing a daily briefing \
for a CTO. Your brief must be concise, factual, and actionable.

Your process (chain-of-thought):
1. SCAN all articles for recurring themes and breaking developments
2. CROSS-REFERENCE: identify stories reported by multiple sources -- these \
are higher signal
3. DEDUPLICATE: merge near-identical stories, keep the most informative source
4. ANALYZE: for each selected story, determine WHY it matters and WHO is affected
5. SYNTHESIZE: write a 2-3 sentence executive summary of the overall landscape

Quality standards:
- Every claim must be traceable to a source article (use article_index)
- Never invent facts not present in the source material
- Use precise language: version numbers, company names, dates when available
- "Why it matters" must explain concrete impact (not vague "this is important")
- Mark uncertainty explicitly: "reportedly", "claims that", "if confirmed"
- Distinguish between announcements, releases, research, and opinion pieces

Output the top 8-12 stories ranked by impact, not by recency."""

_USER_PROMPT_TEMPLATE = """\
Produce an intelligence brief from these {count} articles about "{domain}".

SOURCE ARTICLES:
{articles_text}

For each selected story, provide:
- title: clear, specific headline (rewrite if the original is clickbait)
- summary: 2-3 sentences covering WHAT happened, WHY it matters, and \
WHO is affected. Include specific details (versions, metrics, company names).
- relevance_score: 0-100 based on impact (100 = industry-shifting, \
50 = noteworthy, 20 = niche interest)
- category: "breaking" (new today, high impact), "important" (significant \
development), "interesting" (worth knowing), "research" (academic/technical)
- article_index: index of the primary source article
- tags: 2-4 specific topic tags (e.g., "rust-2.0", "openai", "supply-chain")
- further_reading: one sentence on what deeper insight the full article provides
- citations: list of article indices that cover this same story (for \
cross-reference)

Also provide a synthesis: a 2-3 sentence executive summary of the key \
themes and trends across all articles. What should a tech leader pay \
attention to today?"""


class CuratedArticle(BaseModel, frozen=True):
    """A single curated article from the LLM intelligence brief."""

    title: str
    summary: str
    relevance_score: int = Field(ge=0, le=100)
    category: str = "interesting"
    article_index: int = 0
    tags: list[str] = Field(default_factory=list)
    further_reading: str = ""
    citations: list[int] = Field(default_factory=list)


class CurationResponse(BaseModel, frozen=True):
    """LLM response: curated intelligence brief."""

    curated_articles: list[CuratedArticle] = Field(default_factory=list)
    synthesis: str = ""


def format_articles_for_llm(articles: list[Article]) -> str:
    """Format articles with full context for LLM analysis.

    Includes title, author, engagement metrics, tags, summary, and URL.
    Each article is indexed for citation tracking.
    """
    lines: list[str] = []
    for i, article in enumerate(articles):
        header = f"[{i}] {article.title}"
        meta: list[str] = []
        if article.author:
            meta.append(f"by {article.author}")
        meta.append(f"source:{article.domain}")
        if article.score > 0:
            meta.append(f"score:{article.score}")
        if article.comment_count > 0:
            meta.append(f"comments:{article.comment_count}")
        if article.age_display:
            meta.append(f"age:{article.age_display}")
        if article.tags:
            meta.append(f"tags:{','.join(article.tags[:4])}")

        parts = [header]
        if meta:
            parts.append("  " + " | ".join(meta))
        if article.summary:
            # Include more summary text for better LLM analysis
            parts.append("  " + article.summary[:300])
        parts.append(f"  url: {article.url}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def curate_articles(
    articles: list[Article],
    llm_client: LLMClient,
    domain: str,
) -> CurationResponse:
    """Produce an intelligence brief from raw articles.

    The LLM cross-references sources, deduplicates stories, analyzes
    impact, and produces professional-grade summaries with citations.
    Falls back to score-based ranking if the LLM call fails.
    """
    if not articles:
        return CurationResponse()

    articles_text = format_articles_for_llm(articles)
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        count=len(articles),
        domain=domain,
        articles_text=articles_text,
    )

    try:
        response = llm_client.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=CurationResponse,
        )
        logger.debug(
            f"curated {len(response.curated_articles)} articles from {len(articles)} sources"
        )
        return response
    except Exception:
        logger.warning(f"LLM curation failed for {domain}, using score-based ranking")
        return _fallback_curation(articles)


def _fallback_curation(articles: list[Article]) -> CurationResponse:
    """Score-based curation without LLM. Used as fallback."""
    sorted_articles = sorted(articles, key=lambda a: a.score, reverse=True)
    curated: list[CuratedArticle] = []
    for i, article in enumerate(sorted_articles[:15]):
        curated.append(
            CuratedArticle(
                title=article.title,
                summary=article.summary[:200] if article.summary else "",
                relevance_score=max(0, min(100, article.score)),
                category="important" if article.score >= 100 else "interesting",
                article_index=i,
                tags=list(article.tags[:3]),
                further_reading=article.url,
                citations=[i],
            )
        )
    return CurationResponse(curated_articles=curated)
