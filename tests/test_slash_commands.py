"""Tests for slash command system."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TestParseSlashCommand:
    """Test parsing individual slash command markdown files."""

    def test_valid_file(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import parse_slash_command_file

        md = tmp_path / "full-review.md"
        md.write_text(
            "---\nname: full-review\ndescription: Run a full review\n---\n"
            "# Run the review\nreview\n# Show findings\nfindings summary\n"
        )
        cmd = parse_slash_command_file(md, source="user")
        assert cmd is not None
        assert cmd.name == "full-review"
        assert cmd.description == "Run a full review"
        assert cmd.commands == ("review", "findings summary")
        assert cmd.source == "user"

    def test_missing_frontmatter(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import parse_slash_command_file

        md = tmp_path / "bad.md"
        md.write_text("no frontmatter here\nreview\n")
        assert parse_slash_command_file(md, source="user") is None

    def test_empty_commands(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import parse_slash_command_file

        md = tmp_path / "empty.md"
        md.write_text("---\nname: empty\ndescription: nothing\n---\n# only comments\n")
        assert parse_slash_command_file(md, source="user") is None

    def test_comments_and_blanks_skipped(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import parse_slash_command_file

        md = tmp_path / "test.md"
        md.write_text(
            "---\nname: test\ndescription: test\n---\n\n# comment\nreview\n\n# another\nstatus\n"
        )
        cmd = parse_slash_command_file(md, source="project")
        assert cmd is not None
        assert cmd.commands == ("review", "status")


class TestLoadSlashCommands:
    """Test loading slash commands from directories."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import load_slash_commands

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        result = load_slash_commands(user_dir=user_dir)
        assert result == {}

    def test_loads_from_user_dir(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import load_slash_commands

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "review.md").write_text(
            "---\nname: review\ndescription: quick review\n---\nreview\n"
        )
        result = load_slash_commands(user_dir=user_dir)
        assert "review" in result
        assert result["review"].source == "user"

    def test_project_overrides_user(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import load_slash_commands

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "review.md").write_text(
            "---\nname: review\ndescription: user review\n---\nreview\n"
        )
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "review.md").write_text(
            "---\nname: review\ndescription: project review\n---\nreview --deep\n"
        )
        result = load_slash_commands(user_dir=user_dir, project_dir=project_dir)
        assert result["review"].source == "project"
        assert result["review"].description == "project review"

    def test_nonexistent_dirs(self) -> None:
        from pathlib import Path

        from code_review_agent.interactive.slash_commands import load_slash_commands

        result = load_slash_commands(
            user_dir=Path("/nonexistent/user"),
            project_dir=Path("/nonexistent/project"),
        )
        assert result == {}


class TestListSlashCommands:
    """Test formatting slash commands for display."""

    def test_empty(self) -> None:
        from code_review_agent.interactive.slash_commands import list_slash_commands

        assert "No slash commands" in list_slash_commands({})

    def test_formatting(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.slash_commands import SlashCommand, list_slash_commands

        cmds = {
            "review": SlashCommand("review", "Run review", ("review",), "user"),
            "deploy": SlashCommand("deploy", "Deploy it", ("deploy",), "project"),
        }
        output = list_slash_commands(cmds)
        assert "/deploy (project)" in output
        assert "/review" in output
