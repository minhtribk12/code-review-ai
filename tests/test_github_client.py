from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from code_review_agent.github_client import (
    GitHubAuthError,
    _deduplicate_files,
    fetch_pr_diff,
    parse_pr_reference,
)


class TestParsePrReference:
    """Test parsing of PR references into (owner, repo, pr_number)."""

    def test_short_format(self) -> None:
        owner, repo, number = parse_pr_reference("acme/webapp#42")
        assert owner == "acme"
        assert repo == "webapp"
        assert number == 42

    def test_full_github_url(self) -> None:
        url = "https://github.com/acme/webapp/pull/123"
        owner, repo, number = parse_pr_reference(url)
        assert owner == "acme"
        assert repo == "webapp"
        assert number == 123

    def test_invalid_input_random_string(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid"):
            parse_pr_reference("not-a-pr-reference")

    def test_invalid_input_missing_number(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid"):
            parse_pr_reference("acme/webapp#")

    def test_invalid_input_wrong_url_format(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid"):
            parse_pr_reference("https://github.com/acme/webapp/issues/5")

    def test_invalid_input_empty_string(self) -> None:
        with pytest.raises(ValueError, match=r"[Ii]nvalid"):
            parse_pr_reference("")

    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("my-org/my-repo#1", ("my-org", "my-repo", 1)),
            ("a/b#999", ("a", "b", 999)),
            (
                "https://github.com/python/cpython/pull/100",
                ("python", "cpython", 100),
            ),
        ],
    )
    def test_various_valid_inputs(
        self,
        reference: str,
        expected: tuple[str, str, int],
    ) -> None:
        assert parse_pr_reference(reference) == expected


def _make_response(
    *, json_data: object = None, status_code: int = 200, headers: dict | None = None
) -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


class TestFetchPrDiff:
    """Test fetching a PR diff with mocked HTTP responses."""

    def _mock_client(
        self,
        meta_json: dict,
        files_json: list,
        *,
        page2_error: bool = False,
        page2_auth_error: bool = False,
    ) -> MagicMock:
        """Create a mock httpx.Client with get() returning different responses."""
        meta_response = _make_response(json_data=meta_json)
        files_response = _make_response(json_data=files_json)
        empty_page = _make_response(json_data=[])

        side_effects: list[MagicMock | Exception] = [meta_response, files_response]
        if page2_error:
            # Transient error -- retried 3 times then caught
            for _ in range(3):
                side_effects.append(httpx.ConnectError("connection refused"))
        elif page2_auth_error:
            # Auth error on page 2 -- should propagate, not partial-recover
            auth_resp = _make_response(json_data={"message": "Bad credentials"}, status_code=401)
            auth_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401", request=MagicMock(), response=auth_resp
            )
            side_effects.append(auth_resp)
        else:
            side_effects.append(empty_page)

        mock_client = MagicMock()
        mock_client.get.side_effect = side_effects
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    @patch("code_review_agent.github_client.httpx.Client")
    def test_returns_diff_files_from_api(self, mock_client_cls: MagicMock) -> None:
        mock_client_cls.return_value = self._mock_client(
            meta_json={
                "title": "Fix bug",
                "body": "Fixes #42",
                "html_url": "https://github.com/acme/webapp/pull/42",
            },
            files_json=[
                {
                    "filename": "src/main.py",
                    "patch": "@@ -1,3 +1,5 @@\n+import os\n",
                    "status": "modified",
                },
                {
                    "filename": "src/utils.py",
                    "patch": "@@ -10,2 +10,4 @@\n+# new comment\n",
                    "status": "modified",
                },
            ],
        )

        result = fetch_pr_diff(owner="acme", repo="webapp", pr_number=42, token="ghp_fake")

        assert len(result.diff_files) == 2
        assert result.diff_files[0].filename == "src/main.py"
        assert result.pr_title == "Fix bug"
        assert result.fetch_warnings == []

    @patch("code_review_agent.github_client.httpx.Client")
    def test_skips_files_without_patch(self, mock_client_cls: MagicMock) -> None:
        mock_client_cls.return_value = self._mock_client(
            meta_json={"title": "Update", "body": "", "html_url": "https://example.com"},
            files_json=[
                {"filename": "image.png", "status": "added"},
                {
                    "filename": "src/app.py",
                    "patch": "@@ -1 +1 @@\n-old\n+new\n",
                    "status": "modified",
                },
            ],
        )

        result = fetch_pr_diff(owner="acme", repo="webapp", pr_number=10, token="ghp_tok")

        assert len(result.diff_files) == 1
        assert result.diff_files[0].filename == "src/app.py"

    @patch("code_review_agent.github_client.httpx.Client")
    def test_partial_fetch_on_page2_error(self, mock_client_cls: MagicMock) -> None:
        """When page 2 fails after retries, return page 1 files with warning."""
        mock_client_cls.return_value = self._mock_client(
            meta_json={
                "title": "Big PR",
                "body": "",
                "html_url": "https://github.com/acme/webapp/pull/99",
            },
            files_json=[
                {
                    "filename": "src/a.py",
                    "patch": "@@ -1 +1 @@\n+line\n",
                    "status": "modified",
                },
            ],
            page2_error=True,
        )

        result = fetch_pr_diff(owner="acme", repo="webapp", pr_number=99, token="ghp_tok")

        assert len(result.diff_files) == 1
        assert result.diff_files[0].filename == "src/a.py"
        assert len(result.fetch_warnings) == 1
        assert "Failed to fetch page 2" in result.fetch_warnings[0]

    @patch("code_review_agent.github_client.httpx.Client")
    def test_auth_error_on_page2_propagates(self, mock_client_cls: MagicMock) -> None:
        """Auth errors (401/403) are NOT silently recovered -- they propagate."""
        mock_client_cls.return_value = self._mock_client(
            meta_json={
                "title": "Private PR",
                "body": "",
                "html_url": "https://github.com/acme/webapp/pull/50",
            },
            files_json=[
                {
                    "filename": "src/a.py",
                    "patch": "@@ -1 +1 @@\n+line\n",
                    "status": "modified",
                },
            ],
            page2_auth_error=True,
        )

        with pytest.raises(GitHubAuthError, match="401"):
            fetch_pr_diff(owner="acme", repo="webapp", pr_number=50, token="ghp_tok")

    @patch("code_review_agent.github_client.httpx.Client")
    def test_404_raises_value_error_with_hint(self, mock_client_cls: MagicMock) -> None:
        """404 on metadata fetch raises ValueError with GITHUB_TOKEN hint."""
        meta_response = _make_response(status_code=404)
        mock_client = MagicMock()
        mock_client.get.return_value = meta_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            fetch_pr_diff(owner="acme", repo="webapp", pr_number=999, token=None)

    @patch("code_review_agent.github_client.httpx.Client")
    def test_404_no_hint_when_token_provided(self, mock_client_cls: MagicMock) -> None:
        """404 with token provided gives no GITHUB_TOKEN hint."""
        meta_response = _make_response(status_code=404)
        mock_client = MagicMock()
        mock_client.get.return_value = meta_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError, match="PR not found") as exc_info:
            fetch_pr_diff(owner="acme", repo="webapp", pr_number=999, token="ghp_tok")

        assert "GITHUB_TOKEN" not in str(exc_info.value)

    @patch("code_review_agent.github_client.httpx.Client")
    def test_fetch_warnings_empty_by_default(self, mock_client_cls: MagicMock) -> None:
        mock_client_cls.return_value = self._mock_client(
            meta_json={
                "title": "Clean PR",
                "body": "",
                "html_url": "https://github.com/acme/webapp/pull/1",
            },
            files_json=[
                {
                    "filename": "src/x.py",
                    "patch": "@@ -1 +1 @@\n+x\n",
                    "status": "modified",
                },
            ],
        )

        result = fetch_pr_diff(owner="acme", repo="webapp", pr_number=1, token="ghp_tok")
        assert result.fetch_warnings == []


class TestDeduplicateFiles:
    """Test file deduplication by filename."""

    def test_no_duplicates(self) -> None:
        files = [
            {"filename": "a.py", "patch": "diff a"},
            {"filename": "b.py", "patch": "diff b"},
        ]
        warnings: list[str] = []
        result = _deduplicate_files(files, warnings)
        assert len(result) == 2
        assert warnings == []

    def test_duplicate_last_wins(self) -> None:
        files = [
            {"filename": "a.py", "patch": "old diff"},
            {"filename": "b.py", "patch": "diff b"},
            {"filename": "a.py", "patch": "new diff"},
        ]
        warnings: list[str] = []
        result = _deduplicate_files(files, warnings)
        assert len(result) == 2
        a_file = next(f for f in result if f["filename"] == "a.py")
        assert a_file["patch"] == "new diff"
        assert len(warnings) == 1
        assert "1 duplicate" in warnings[0]

    def test_empty_list(self) -> None:
        warnings: list[str] = []
        result = _deduplicate_files([], warnings)
        assert result == []
        assert warnings == []

    def test_all_same_filename(self) -> None:
        files = [
            {"filename": "x.py", "patch": "v1"},
            {"filename": "x.py", "patch": "v2"},
            {"filename": "x.py", "patch": "v3"},
        ]
        warnings: list[str] = []
        result = _deduplicate_files(files, warnings)
        assert len(result) == 1
        assert result[0]["patch"] == "v3"
        assert "2 duplicate" in warnings[0]

    def test_preserves_order_of_unique_files(self) -> None:
        files = [
            {"filename": "c.py", "patch": "c"},
            {"filename": "a.py", "patch": "a"},
            {"filename": "b.py", "patch": "b"},
        ]
        warnings: list[str] = []
        result = _deduplicate_files(files, warnings)
        assert [f["filename"] for f in result] == ["c.py", "a.py", "b.py"]


class TestFetchWarningsInModels:
    """Test that fetch_warnings field works on ReviewInput and ReviewReport."""

    def test_review_input_default_empty_warnings(self) -> None:
        from code_review_agent.models import DiffFile, ReviewInput

        ri = ReviewInput(
            diff_files=[DiffFile(filename="a.py", patch="diff", status="modified")],
        )
        assert ri.fetch_warnings == []

    def test_review_input_with_warnings(self) -> None:
        from code_review_agent.models import DiffFile, ReviewInput

        ri = ReviewInput(
            diff_files=[DiffFile(filename="a.py", patch="diff", status="modified")],
            fetch_warnings=["Page 2 failed", "Rate limit low"],
        )
        assert len(ri.fetch_warnings) == 2

    def test_review_input_default_not_shared_across_instances(self) -> None:
        """Verify default_factory creates separate lists (no shared mutable default)."""
        from code_review_agent.models import DiffFile, ReviewInput

        ri1 = ReviewInput(
            diff_files=[DiffFile(filename="a.py", patch="diff", status="modified")],
        )
        ri2 = ReviewInput(
            diff_files=[DiffFile(filename="b.py", patch="diff", status="modified")],
        )
        assert ri1.fetch_warnings is not ri2.fetch_warnings

    def test_review_report_default_empty_warnings(self) -> None:
        from datetime import UTC, datetime

        from code_review_agent.models import AgentResult, ReviewReport

        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="ok",
                    execution_time_seconds=1.0,
                )
            ],
            overall_summary="ok",
            risk_level="low",
        )
        assert report.fetch_warnings == []

    def test_review_report_with_warnings(self) -> None:
        from datetime import UTC, datetime

        from code_review_agent.models import AgentResult, ReviewReport

        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="ok",
                    execution_time_seconds=1.0,
                )
            ],
            overall_summary="ok",
            risk_level="low",
            fetch_warnings=["Page 3 failed after retries"],
        )
        assert len(report.fetch_warnings) == 1

    def test_review_report_warnings_in_json(self) -> None:
        """Verify fetch_warnings appear in JSON output."""
        import json
        from datetime import UTC, datetime

        from code_review_agent.models import AgentResult, ReviewReport

        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="ok",
                    execution_time_seconds=1.0,
                )
            ],
            overall_summary="ok",
            risk_level="low",
            fetch_warnings=["Rate limit low: 45/5000"],
        )
        data = json.loads(report.model_dump_json())
        assert data["fetch_warnings"] == ["Rate limit low: 45/5000"]


class TestFetchWarningsInReport:
    """Test that fetch_warnings render in Rich and markdown reports."""

    def test_rich_report_shows_warnings(self) -> None:
        from datetime import UTC, datetime
        from io import StringIO

        from rich.console import Console

        from code_review_agent.models import AgentResult, ReviewReport
        from code_review_agent.report import render_report_rich

        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="ok",
                    execution_time_seconds=1.0,
                )
            ],
            overall_summary="ok",
            risk_level="low",
            fetch_warnings=["Page 3 failed after retries"],
        )

        buf = StringIO()
        console = Console(file=buf, force_terminal=True)
        with patch("code_review_agent.report.Console", return_value=console):
            render_report_rich(report)

        output = buf.getvalue()
        assert "Page 3 failed" in output

    def test_markdown_report_shows_warnings(self) -> None:
        from datetime import UTC, datetime

        from code_review_agent.models import AgentResult, ReviewReport
        from code_review_agent.report import render_report_markdown

        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="ok",
                    execution_time_seconds=1.0,
                )
            ],
            overall_summary="ok",
            risk_level="low",
            fetch_warnings=["Removed 2 duplicate file(s)"],
        )

        md = render_report_markdown(report)
        assert "Removed 2 duplicate" in md
        assert "**WARNING:**" in md
