"""Article content fetcher and HTML-to-terminal renderer.

Fetches full article content on demand and converts HTML to
rich terminal text with formatting preserved.
"""

from __future__ import annotations

import re

import httpx
import structlog

logger = structlog.get_logger(__name__)

_USER_AGENT = "CRA-NewsReader/1.0 (+https://github.com/minhtribk12/code-review-ai)"
_FETCH_TIMEOUT = 15


def fetch_article_content(url: str) -> tuple[str, str]:
    """Fetch and parse article content.

    Returns (html, plain_text). On failure returns empty strings.
    """
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception:
        logger.debug(f"failed to fetch article content from {url}")
        return "", ""

    html = response.text
    text = html_to_terminal_text(html)
    return html, text


def html_to_terminal_text(html: str) -> str:
    """Convert HTML to rich terminal text with formatting.

    Preserves headings, code blocks, links, lists, bold/italic,
    blockquotes, and images as text placeholders.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove script, style, nav, footer, header elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find main content
    main = soup.find("article") or soup.find("main") or soup.find(class_="post-content")
    if main is None:
        main = soup.find("body") or soup

    lines: list[str] = []
    _process_element(main, lines, depth=0)

    # Clean up excessive blank lines
    result: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank

    return "\n".join(result)


def _process_element(element: object, lines: list[str], depth: int) -> None:
    """Recursively process an HTML element into text lines."""
    from bs4 import NavigableString, Tag  # type: ignore[attr-defined]

    if isinstance(element, NavigableString):
        text = str(element).strip()
        if text:
            lines.append(text)
        return

    if not isinstance(element, Tag):
        return

    tag_name = element.name

    # Headings
    if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag_name[1])
        prefix = "#" * level
        text = element.get_text(strip=True)
        lines.append("")
        lines.append(f"{prefix} {text}")
        lines.append("")
        return

    # Code blocks
    if tag_name == "pre":
        code = element.get_text()
        lang = ""
        code_tag = element.find("code")
        if code_tag and code_tag.get("class"):
            classes: list[str] = list(code_tag.get("class") or [])
            for cls in classes:
                if str(cls).startswith("language-"):
                    lang = str(cls)[9:]
                    break
        lines.append("")
        lines.append(f"```{lang}")
        for code_line in code.splitlines():
            lines.append(f"    {code_line}")
        lines.append("```")
        lines.append("")
        return

    # Inline code
    if tag_name == "code" and element.parent and element.parent.name != "pre":
        lines.append(f"`{element.get_text()}`")
        return

    # Blockquotes
    if tag_name == "blockquote":
        text = element.get_text(strip=True)
        for bq_line in text.splitlines():
            lines.append(f"  | {bq_line}")
        lines.append("")
        return

    # Lists
    if tag_name in ("ul", "ol"):
        lines.append("")
        for i, li in enumerate(element.find_all("li", recursive=False)):
            prefix = f"  {i + 1}. " if tag_name == "ol" else "  - "
            lines.append(f"{prefix}{li.get_text(strip=True)}")
        lines.append("")
        return

    # Images
    if tag_name == "img":
        alt = str(element.get("alt", "image"))
        lines.append(f"  [image: {alt}]")
        return

    # Links
    if tag_name == "a":
        text = element.get_text(strip=True)
        href = str(element.get("href", ""))
        if text and href and not href.startswith("#"):
            lines.append(f"{text} [{href}]")
        elif text:
            lines.append(text)
        return

    # Paragraphs and divs
    if tag_name in ("p", "div"):
        text = ""
        for child in element.children:
            if isinstance(child, NavigableString):
                text += str(child)
            elif isinstance(child, Tag):
                if child.name in ("strong", "b"):
                    text += f"**{child.get_text()}**"
                elif child.name in ("em", "i"):
                    text += f"*{child.get_text()}*"
                elif child.name == "code":
                    text += f"`{child.get_text()}`"
                elif child.name == "a":
                    text += child.get_text()
                elif child.name == "br":
                    text += "\n"
                else:
                    text += child.get_text()
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned:
            lines.append("")
            # Word wrap at ~78 chars
            words = cleaned.split()
            current_line = ""
            for word in words:
                if len(current_line) + len(word) + 1 > 78:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = f"{current_line} {word}" if current_line else word
            if current_line:
                lines.append(current_line)
        return

    # Default: recurse into children
    for child in element.children:
        _process_element(child, lines, depth + 1)
