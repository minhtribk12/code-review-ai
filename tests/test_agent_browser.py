"""Tests for the agent browser UI state and multiline buffer."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.formatted_text import FormattedText

from code_review_agent.agent_definition import AgentDefinition, AgentSource

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.interactive.commands.agent_browser import (
    AgentBrowser,
    MultilineBuffer,
    _is_multiline_mode,
    _is_text_mode,
    _Mode,
)

# ---------------------------------------------------------------------------
# MultilineBuffer tests
# ---------------------------------------------------------------------------


class TestMultilineBufferInit:
    def test_empty_string(self) -> None:
        buf = MultilineBuffer("")
        assert buf.lines == [""]
        assert buf.row == 0
        assert buf.col == 0

    def test_single_line(self) -> None:
        buf = MultilineBuffer("hello world")
        assert buf.lines == ["hello world"]
        assert buf.row == 0
        assert buf.col == 11

    def test_multiline(self) -> None:
        buf = MultilineBuffer("line1\nline2\nline3")
        assert buf.lines == ["line1", "line2", "line3"]
        assert buf.row == 2
        assert buf.col == 5

    def test_text_round_trip(self) -> None:
        original = "line1\nline2\nline3"
        buf = MultilineBuffer(original)
        assert buf.text == original


class TestMultilineNavigation:
    def test_move_up(self) -> None:
        buf = MultilineBuffer("a\nb\nc")
        buf.row = 2
        buf.col = 0
        buf.move_up()
        assert buf.row == 1

    def test_move_up_at_top(self) -> None:
        buf = MultilineBuffer("a\nb")
        buf.row = 0
        buf.move_up()
        assert buf.row == 0

    def test_move_down(self) -> None:
        buf = MultilineBuffer("a\nb\nc")
        buf.row = 0
        buf.col = 0
        buf.move_down()
        assert buf.row == 1

    def test_move_down_at_bottom(self) -> None:
        buf = MultilineBuffer("a\nb")
        buf.row = 1
        buf.move_down()
        assert buf.row == 1

    def test_move_up_clamps_col(self) -> None:
        buf = MultilineBuffer("ab\nxyz")
        buf.row = 1
        buf.col = 2
        buf.move_up()
        assert buf.row == 0
        assert buf.col == 2  # "ab" has len 2, col 2 is valid (end)

    def test_move_up_clamps_col_beyond(self) -> None:
        buf = MultilineBuffer("a\nxyz")
        buf.row = 1
        buf.col = 3
        buf.move_up()
        assert buf.row == 0
        assert buf.col == 1  # clamped to len("a")

    def test_move_left(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 2
        buf.move_left()
        assert buf.col == 1

    def test_move_left_at_start_wraps_to_prev_line(self) -> None:
        buf = MultilineBuffer("ab\ncd")
        buf.row = 1
        buf.col = 0
        buf.move_left()
        assert buf.row == 0
        assert buf.col == 2

    def test_move_left_at_very_start(self) -> None:
        buf = MultilineBuffer("ab")
        buf.row = 0
        buf.col = 0
        buf.move_left()
        assert buf.row == 0
        assert buf.col == 0

    def test_move_right(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 1
        buf.move_right()
        assert buf.col == 2

    def test_move_right_at_end_wraps_to_next_line(self) -> None:
        buf = MultilineBuffer("ab\ncd")
        buf.row = 0
        buf.col = 2
        buf.move_right()
        assert buf.row == 1
        assert buf.col == 0

    def test_move_right_at_very_end(self) -> None:
        buf = MultilineBuffer("ab")
        buf.row = 0
        buf.col = 2
        buf.move_right()
        assert buf.row == 0
        assert buf.col == 2

    def test_home(self) -> None:
        buf = MultilineBuffer("hello")
        buf.col = 3
        buf.move_home()
        assert buf.col == 0

    def test_end(self) -> None:
        buf = MultilineBuffer("hello")
        buf.col = 0
        buf.move_end()
        assert buf.col == 5


class TestMultilineWordMovement:
    def test_word_right_basic(self) -> None:
        buf = MultilineBuffer("hello world foo")
        buf.row = 0
        buf.col = 0
        buf.move_word_right()
        assert buf.col == 6  # after "hello "

    def test_word_right_at_end(self) -> None:
        buf = MultilineBuffer("abc\ndef")
        buf.row = 0
        buf.col = 3
        buf.move_word_right()
        assert buf.row == 1
        assert buf.col == 0

    def test_word_left_basic(self) -> None:
        buf = MultilineBuffer("hello world")
        buf.row = 0
        buf.col = 8
        buf.move_word_left()
        assert buf.col == 6  # start of "world"

    def test_word_left_at_start(self) -> None:
        buf = MultilineBuffer("abc\ndef")
        buf.row = 1
        buf.col = 0
        buf.move_word_left()
        assert buf.row == 0
        assert buf.col == 3  # end of prev line

    def test_word_right_multiple(self) -> None:
        buf = MultilineBuffer("one two three")
        buf.row = 0
        buf.col = 0
        buf.move_word_right()
        buf.move_word_right()
        assert buf.col == 8  # start of "three"


class TestMultilineEditing:
    def test_insert_char(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 1
        buf.insert("X")
        assert buf.lines == ["aXbc"]
        assert buf.col == 2

    def test_insert_multiline_paste(self) -> None:
        buf = MultilineBuffer("ab")
        buf.col = 1
        buf.insert("X\nY\nZ")
        assert buf.lines == ["aX", "Y", "Zb"]
        assert buf.row == 2
        assert buf.col == 1

    def test_insert_newline(self) -> None:
        buf = MultilineBuffer("abcd")
        buf.col = 2
        buf.insert_newline()
        assert buf.lines == ["ab", "cd"]
        assert buf.row == 1
        assert buf.col == 0

    def test_backspace_within_line(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 2
        buf.backspace()
        assert buf.lines == ["ac"]
        assert buf.col == 1

    def test_backspace_joins_lines(self) -> None:
        buf = MultilineBuffer("ab\ncd")
        buf.row = 1
        buf.col = 0
        buf.backspace()
        assert buf.lines == ["abcd"]
        assert buf.row == 0
        assert buf.col == 2

    def test_backspace_at_very_start(self) -> None:
        buf = MultilineBuffer("abc")
        buf.row = 0
        buf.col = 0
        buf.backspace()
        assert buf.lines == ["abc"]

    def test_delete_within_line(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 1
        buf.delete()
        assert buf.lines == ["ac"]

    def test_delete_joins_lines(self) -> None:
        buf = MultilineBuffer("ab\ncd")
        buf.row = 0
        buf.col = 2
        buf.delete()
        assert buf.lines == ["abcd"]

    def test_delete_at_very_end(self) -> None:
        buf = MultilineBuffer("abc")
        buf.col = 3
        buf.delete()
        assert buf.lines == ["abc"]

    def test_kill_to_end(self) -> None:
        buf = MultilineBuffer("abcdef")
        buf.col = 3
        buf.kill_to_end()
        assert buf.lines == ["abc"]

    def test_kill_to_start(self) -> None:
        buf = MultilineBuffer("abcdef")
        buf.col = 3
        buf.kill_to_start()
        assert buf.lines == ["def"]
        assert buf.col == 0


class TestMultilineScroll:
    def test_scroll_follows_cursor(self) -> None:
        buf = MultilineBuffer("\n".join(f"line{i}" for i in range(50)))
        buf.row = 40
        buf.scroll_offset = 0
        buf.ensure_scroll_visible(20)
        assert buf.scroll_offset == 21  # 40 - 20 + 1

    def test_scroll_up(self) -> None:
        buf = MultilineBuffer("\n".join(f"line{i}" for i in range(50)))
        buf.row = 5
        buf.scroll_offset = 10
        buf.ensure_scroll_visible(20)
        assert buf.scroll_offset == 5


# ---------------------------------------------------------------------------
# AgentBrowser tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session(tmp_path: Path) -> MagicMock:
    """Create a mock SessionState with a real storage backend."""
    session = MagicMock()
    session.effective_settings.history_db_path = str(tmp_path / "test.db")
    session.effective_settings.custom_agents_dir = str(tmp_path / "agents")
    return session


class TestAgentBrowserInit:
    def test_builds_agent_list(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="security", system_prompt="P", source=AgentSource.BUILTIN),
                AgentDefinition(name="style", system_prompt="P", source=AgentSource.BUILTIN),
            ]
            browser = AgentBrowser(mock_session)
            assert len(browser.agents) == 2
            assert browser.cursor == 0
            assert browser.mode == _Mode.NAVIGATE

    def test_empty_agent_list(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            assert len(browser.agents) == 0
            assert browser.cursor == 0


class TestAgentBrowserNavigation:
    def _make_browser(self, mock_session: MagicMock, count: int = 5) -> AgentBrowser:
        agents = [
            AgentDefinition(name=f"agent_{i}", system_prompt="P", priority=i) for i in range(count)
        ]
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = agents
            return AgentBrowser(mock_session)

    def test_move_down(self, mock_session: MagicMock) -> None:
        browser = self._make_browser(mock_session)
        browser.move_down()
        assert browser.cursor == 1

    def test_move_up(self, mock_session: MagicMock) -> None:
        browser = self._make_browser(mock_session)
        browser.cursor = 2
        browser.move_up()
        assert browser.cursor == 1

    def test_move_up_at_top(self, mock_session: MagicMock) -> None:
        browser = self._make_browser(mock_session)
        browser.move_up()
        assert browser.cursor == 0

    def test_move_down_at_bottom(self, mock_session: MagicMock) -> None:
        browser = self._make_browser(mock_session, count=3)
        browser.cursor = 2
        browser.move_down()
        assert browser.cursor == 2


class TestAgentBrowserViewDetail:
    def test_enter_detail_mode(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P", source=AgentSource.BUILTIN),
            ]
            browser = AgentBrowser(mock_session)
            browser.view_detail()
            assert browser.mode == _Mode.VIEW_DETAIL


class TestAgentBrowserEdit:
    def test_start_edit_opens_field_select(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P", source=AgentSource.BUILTIN),
            ]
            browser = AgentBrowser(mock_session)
            browser.start_edit()
            assert browser.mode == _Mode.FIELD_SELECT
            # "Reset to default" should NOT be in choices (not a DB agent)
            field_keys = [key for _, key, _ in browser.field_choices]
            assert "_reset" not in field_keys

    def test_start_edit_db_agent_includes_reset(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="db_agent", system_prompt="P", source=AgentSource.DB),
            ]
            browser = AgentBrowser(mock_session)
            browser.start_edit()
            field_keys = [key for _, key, _ in browser.field_choices]
            assert "_reset" in field_keys


class TestAgentBrowserDelete:
    def test_cannot_delete_builtin(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P", source=AgentSource.BUILTIN),
            ]
            browser = AgentBrowser(mock_session)
            browser.request_delete()
            assert browser.mode == _Mode.NAVIGATE
            assert "Cannot delete" in browser.status_message

    def test_can_delete_db_agent(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="db_agent", system_prompt="P", source=AgentSource.DB),
            ]
            browser = AgentBrowser(mock_session)
            browser.request_delete()
            assert browser.mode == _Mode.CONFIRM_DELETE


class TestAgentBrowserAdd:
    def test_start_add(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.start_add()
            assert browser.mode == _Mode.ADD_INPUT
            assert browser.add_step_index == 0
            assert browser.add_steps[0] == "name"

    def test_add_validation_empty_name(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.start_add()
            browser.edit_buffer = ""
            browser.add_next_step()
            assert browser.add_error == "Agent name is required"
            assert browser.add_step_index == 0

    def test_add_validation_invalid_name(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.start_add()
            browser.edit_buffer = "Invalid-Name"
            browser.add_next_step()
            assert "lowercase" in browser.add_error

    def test_add_validation_duplicate_name(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="existing", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            browser.start_add()
            browser.edit_buffer = "existing"
            browser.add_next_step()
            assert "already exists" in browser.add_error


class TestAgentBrowserFieldValidation:
    def test_validate_priority_non_integer(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            err = browser._validate_field("priority", "abc")
            assert "integer" in err

    def test_validate_priority_negative(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            err = browser._validate_field("priority", "-5")
            assert ">= 0" in err

    def test_validate_priority_valid(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            err = browser._validate_field("priority", "50")
            assert err == ""

    def test_validate_empty_system_prompt(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            err = browser._validate_field("system_prompt", "")
            assert "empty" in err


class TestAgentBrowserCoerceField:
    def test_coerce_priority(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            assert browser._coerce_field("priority", "42") == 42

    def test_coerce_file_patterns(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            result = browser._coerce_field("file_patterns", "*.py, *.js")
            assert result == ["*.py", "*.js"]

    def test_coerce_empty_file_patterns(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            result = browser._coerce_field("file_patterns", "")
            assert result is None


class TestAgentBrowserRender:
    def test_render_list_mode(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P", source=AgentSource.BUILTIN),
                AgentDefinition(
                    name="custom", system_prompt="P", source=AgentSource.DB, enabled=False
                ),
            ]
            browser = AgentBrowser(mock_session)
            result = browser.render()
            assert isinstance(result, FormattedText)
            # Check that agent names appear in the rendered output
            text = "".join(t[1] for t in result)
            assert "sec" in text
            assert "custom" in text
            assert "[disabled]" in text

    def test_render_detail_mode(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(
                    name="sec",
                    system_prompt="You review security.",
                    description="Security agent",
                    source=AgentSource.BUILTIN,
                ),
            ]
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.VIEW_DETAIL
            result = browser.render()
            text = "".join(t[1] for t in result)
            assert "Security agent" in text
            assert "You review security." in text

    def test_render_empty_list(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            result = browser.render()
            text = "".join(t[1] for t in result)
            assert "No agents" in text


class TestModeHelpers:
    def test_is_text_mode_edit_field(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.EDIT_FIELD
            assert _is_text_mode(browser) is True
            assert _is_multiline_mode(browser) is False

    def test_is_multiline_mode_edit(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.EDIT_MULTILINE
            assert _is_multiline_mode(browser) is True
            assert _is_text_mode(browser) is False

    def test_is_text_mode_add_non_multiline(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.ADD_INPUT
            browser.add_steps = ["name"]
            browser.add_step_index = 0
            assert _is_text_mode(browser) is True

    def test_is_multiline_mode_add_system_prompt(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = []
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.ADD_INPUT
            browser.add_steps = ["system_prompt"]
            browser.add_step_index = 0
            assert _is_multiline_mode(browser) is True


class TestCancelAction:
    def test_cancel_returns_to_navigate(self, mock_session: MagicMock) -> None:
        with patch(
            "code_review_agent.interactive.commands.agent_browser.resolve_all_agents"
        ) as mock_resolve:
            mock_resolve.return_value = [
                AgentDefinition(name="sec", system_prompt="P"),
            ]
            browser = AgentBrowser(mock_session)
            browser.mode = _Mode.EDIT_FIELD
            browser.ml_buffer = MultilineBuffer("test")
            browser.cancel_action()
            assert browser.mode == _Mode.NAVIGATE
            assert browser.ml_buffer is None
