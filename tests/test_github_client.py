from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from code_review_agent.github_client import fetch_pr_diff, parse_pr_reference


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


class TestFetchPrDiff:
    """Test fetching a PR diff with mocked HTTP responses."""

    def _mock_client(self, meta_json: dict, files_json: list) -> MagicMock:
        """Create a mock httpx.Client with get() returning different responses."""
        meta_response = MagicMock()
        meta_response.json.return_value = meta_json
        meta_response.raise_for_status = MagicMock()

        files_response = MagicMock()
        files_response.json.return_value = files_json
        files_response.raise_for_status = MagicMock()
        files_response.headers = {}

        # Pagination: second page returns empty list to stop the loop
        empty_page = MagicMock()
        empty_page.json.return_value = []
        empty_page.raise_for_status = MagicMock()
        empty_page.headers = {}

        mock_client = MagicMock()
        mock_client.get.side_effect = [
            meta_response,
            files_response,
            empty_page,
        ]
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
