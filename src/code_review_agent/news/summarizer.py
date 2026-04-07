"""Two-level LLM content summarization for news articles.

Level 1: Key takeaways (2-5 bullets for detail panel)
Level 2: Structured reading brief (2-5 min read for reader)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.llm_client import LLMClient

logger = structlog.get_logger(__name__)

_MAX_INPUT_CHARS = 12000  # ~3000 tokens

# --- Level 1: Key Takeaways ---

_TAKEAWAY_SYSTEM = """\
You are a news analyst. Extract the 2-5 most important takeaways \
from this article. Each takeaway must be:
- A single concrete insight (not generic filler)
- Include specific data points when available (numbers, quotes, names)
- Max 2 lines of text
- Ordered by importance (most impactful first)
- Strictly based on the article content (never invent facts)"""

_TAKEAWAY_USER = """\
Extract key takeaways from this article:

{content}"""


class TakeawayResponse(BaseModel, frozen=True):
    """LLM response for key takeaway extraction."""

    takeaways: list[str] = Field(default_factory=list)


def extract_takeaways(
    content: str,
    llm_client: LLMClient,
) -> list[str]:
    """Extract 2-5 key takeaways from article content."""
    if not content or len(content) < 50:
        return []

    truncated = content[:_MAX_INPUT_CHARS]
    user_prompt = _TAKEAWAY_USER.format(content=truncated)

    try:
        response = llm_client.complete(
            system_prompt=_TAKEAWAY_SYSTEM,
            user_prompt=user_prompt,
            response_model=TakeawayResponse,
        )
        takeaways = [t.strip() for t in response.takeaways if t.strip()][:5]
        logger.debug("takeaways_extracted", count=len(takeaways))
        return takeaways
    except Exception:
        logger.debug("takeaway_extraction_failed", exc_info=True)
        return []


def format_takeaways(takeaways: list[str]) -> str:
    """Format takeaways as bullet points for display."""
    if not takeaways:
        return ""
    lines = []
    for t in takeaways:
        lines.append(f"  * {t}")
    return "\n".join(lines)


def fallback_takeaways(content: str, count: int = 5) -> list[str]:
    """Extract first N sentences as fallback when LLM unavailable."""
    if not content:
        return []
    sentences: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 20:
            continue
        sentences.append(stripped)
        if len(sentences) >= count:
            break
    return sentences


# --- Level 2: Structured Brief ---

_BRIEF_SYSTEM = """\
You are a senior tech editor restructuring articles for busy \
professionals. Create a well-organized 2-5 minute reading brief.

Structure rules:
1. Start with a clear ## heading for the topic
2. Use 3-5 sections with ## headings (problem/context, findings, implications)
3. Preserve ALL key quotes as > blockquotes with attribution
4. Preserve ALL statistics, numbers, and data points verbatim
5. Use **bold** for key terms and emphasis
6. Target 800-1500 words
7. End with "Discussion Highlights" section if community comments available
8. End with numbered source links

Quality rules:
- Never invent facts not in the source
- Never add personal opinion or speculation
- Preserve the author's voice in quotes
- Use active voice and short paragraphs (max 3-4 sentences each)"""

_BRIEF_USER = """\
Restructure this article into a well-organized reading brief:

{content}"""


class BriefResponse(BaseModel, frozen=True):
    """LLM response for structured brief."""

    brief: str = ""


def generate_structured_brief(
    content: str,
    llm_client: LLMClient,
) -> str:
    """Generate a structured 2-5 minute reading brief."""
    if not content or len(content) < 50:
        return ""

    truncated = content[:_MAX_INPUT_CHARS]
    user_prompt = _BRIEF_USER.format(content=truncated)

    try:
        response = llm_client.complete(
            system_prompt=_BRIEF_SYSTEM,
            user_prompt=user_prompt,
            response_model=BriefResponse,
        )
        brief = response.brief.strip()
        logger.debug(
            "brief_generated",
            word_count=len(brief.split()),
        )
        return brief
    except Exception:
        logger.debug("brief_generation_failed", exc_info=True)
        return ""


def fallback_brief(content: str) -> str:
    """Basic auto-paragraphing fallback when LLM unavailable."""
    if not content:
        return ""
    lines: list[str] = []
    current_para: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_para:
                lines.append(" ".join(current_para))
                lines.append("")
                current_para = []
            continue
        current_para.append(stripped)

    if current_para:
        lines.append(" ".join(current_para))

    return "\n".join(lines)
