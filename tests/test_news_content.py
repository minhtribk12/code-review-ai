"""Tests for article content fetcher and HTML renderer."""

from __future__ import annotations

from code_review_agent.news.content import html_to_terminal_text


class TestHtmlToTerminalText:
    def test_basic_paragraph(self) -> None:
        html = "<p>Hello world</p>"
        text = html_to_terminal_text(html)
        assert "Hello world" in text

    def test_headings(self) -> None:
        html = "<h1>Title</h1><h2>Subtitle</h2>"
        text = html_to_terminal_text(html)
        assert "# Title" in text
        assert "## Subtitle" in text

    def test_code_block(self) -> None:
        html = '<pre><code class="language-python">x = 1</code></pre>'
        text = html_to_terminal_text(html)
        assert "```python" in text
        assert "x = 1" in text

    def test_blockquote(self) -> None:
        html = "<blockquote>Important quote</blockquote>"
        text = html_to_terminal_text(html)
        assert "| Important quote" in text

    def test_unordered_list(self) -> None:
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        text = html_to_terminal_text(html)
        assert "- Item 1" in text
        assert "- Item 2" in text

    def test_ordered_list(self) -> None:
        html = "<ol><li>First</li><li>Second</li></ol>"
        text = html_to_terminal_text(html)
        assert "1. First" in text
        assert "2. Second" in text

    def test_bold_and_italic(self) -> None:
        html = "<p>This is <strong>bold</strong> and <em>italic</em></p>"
        text = html_to_terminal_text(html)
        assert "**bold**" in text
        assert "*italic*" in text

    def test_image_placeholder(self) -> None:
        html = '<img src="test.png" alt="A diagram">'
        text = html_to_terminal_text(html)
        assert "[image: A diagram]" in text

    def test_strips_scripts(self) -> None:
        html = "<p>Text</p><script>alert('xss')</script>"
        text = html_to_terminal_text(html)
        assert "alert" not in text
        assert "Text" in text

    def test_strips_nav_footer(self) -> None:
        html = "<nav>Nav</nav><article><p>Content</p></article><footer>Footer</footer>"
        text = html_to_terminal_text(html)
        assert "Nav" not in text
        assert "Content" in text
        assert "Footer" not in text

    def test_empty_html(self) -> None:
        assert html_to_terminal_text("") == ""

    def test_inline_code(self) -> None:
        html = "<p>Use <code>pip install</code> to install</p>"
        text = html_to_terminal_text(html)
        assert "`pip install`" in text
