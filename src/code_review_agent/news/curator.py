"""LLM-powered news curation: rank, summarize, and deduplicate articles.

Sends batches of fetched articles to the LLM for intelligent curation.
Returns curated articles with summaries, relevance scores, and citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.llm_client import LLMClient
    from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a tech news curator. Analyze the articles below and:

1. Identify the most relevant and trending topics
2. Remove duplicates (same story from different sources)
3. Rank by importance: breaking news > trending > routine
4. Write a concise 1-2 sentence summary for each selected article
5. Include the article index for citation

Selection criteria:
- Prefer articles with high engagement (score, comments)
- Prefer recent articles (last 24h)
- Prefer original sources over aggregated reposts
- Keep diverse topics (don't select 5 articles about the same thing)

Return the top 10-15 most relevant articles as JSON."""

_USER_PROMPT_TEMPLATE = """\
Here are {count} articles from {domain}. Select and curate the top items.

Articles:
{articles_text}

Return JSON with the curated list. For each article include:
- title: the article title (can be improved for clarity)
- summary: 1-2 sentence summary capturing the key insight
- relevance_score: 0-100 (100 = breaking/must-read)
- category: one of [breaking, important, interesting, routine]
- article_index: the original article index number for citation
- tags: 1-3 topic tags
- further_reading: brief note on what the reader will learn"""


class CuratedArticle(BaseModel, frozen=True):
    """A single curated article from the LLM."""

    title: str
    summary: str
    relevance_score: int = Field(ge=0, le=100)
    category: str = "interesting"
    article_index: int = 0
    tags: list[str] = Field(default_factory=list)
    further_reading: str = ""


class CurationResponse(BaseModel, frozen=True):
    """LLM response for news curation."""

    curated_articles: list[CuratedArticle] = Field(default_factory=list)
    synthesis: str = ""


def format_articles_for_llm(articles: list[Article]) -> str:
    """Format articles as a compact text block for the LLM prompt."""
    lines: list[str] = []
    for i, article in enumerate(articles):
        parts = [f"[{i}] {article.title}"]
        if article.author:
            parts.append(f"by {article.author}")
        if article.score > 0:
            parts.append(f"score:{article.score}")
        if article.comment_count > 0:
            parts.append(f"comments:{article.comment_count}")
        if article.tags:
            parts.append(f"tags:{','.join(article.tags[:3])}")
        if article.summary:
            parts.append(f"- {article.summary[:150]}")
        parts.append(f"url:{article.url}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def curate_articles(
    articles: list[Article],
    llm_client: LLMClient,
    domain: str,
) -> CurationResponse:
    """Send articles to the LLM for intelligent curation.

    Returns a CurationResponse with ranked, summarized articles.
    Falls back to a basic response if the LLM call fails.
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
        logger.debug(f"curated {len(response.curated_articles)} articles from {len(articles)}")
        return response
    except Exception:
        logger.warning(f"LLM curation failed for {domain}, using basic ranking")
        return _fallback_curation(articles)


def _fallback_curation(articles: list[Article]) -> CurationResponse:
    """Basic curation without LLM: sort by score, take top 15."""
    sorted_articles = sorted(articles, key=lambda a: a.score, reverse=True)
    curated: list[CuratedArticle] = []
    for i, article in enumerate(sorted_articles[:15]):
        curated.append(
            CuratedArticle(
                title=article.title,
                summary=article.summary[:150] if article.summary else "",
                relevance_score=max(0, min(100, article.score)),
                category="important" if article.score >= 100 else "interesting",
                article_index=i,
                tags=list(article.tags[:3]),
                further_reading=article.url,
            )
        )
    return CurationResponse(curated_articles=curated)
