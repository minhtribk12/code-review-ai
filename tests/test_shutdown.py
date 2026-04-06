"""Tests for graceful shutdown."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest  # noqa: TC002 - used at runtime for MonkeyPatch type

if TYPE_CHECKING:
    from pathlib import Path


class TestGracefulShutdown:
    """Test shutdown phase execution."""

    def test_reset_terminal_writes_escape_codes(self) -> None:
        from code_review_agent.interactive.shutdown import _reset_terminal

        with patch("sys.stdout") as mock_stdout:
            _reset_terminal()
            written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
            assert "\033[?1000l" in written  # disable mouse
            assert "\033[?1049l" in written  # exit alt screen
            assert "\033[?25h" in written  # show cursor

    def test_reset_terminal_handles_broken_stdout(self) -> None:
        from code_review_agent.interactive.shutdown import _reset_terminal

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write.side_effect = OSError("broken pipe")
            _reset_terminal()  # should not raise

    def test_save_state_uses_pre_imported_fn(self) -> None:
        import code_review_agent.interactive.shutdown as mod

        mock_session = MagicMock()
        mock_save = MagicMock()

        old_ref = mod._session_ref
        old_fn = mod._save_fn
        try:
            mod._session_ref = mock_session
            mod._save_fn = mock_save
            mod._save_state()
            mock_save.assert_called_once_with(mock_session)
        finally:
            mod._session_ref = old_ref
            mod._save_fn = old_fn

    def test_save_state_skips_when_no_session(self) -> None:
        import code_review_agent.interactive.shutdown as mod

        old_ref = mod._session_ref
        try:
            mod._session_ref = None
            mod._save_state()  # should not crash
        finally:
            mod._session_ref = old_ref

    def test_register_shutdown_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import code_review_agent.interactive.shutdown as mod

        monkeypatch.setattr(mod, "_shutdown_registered", False)
        monkeypatch.setattr(mod, "_session_ref", None)

        mock_session = MagicMock()
        with patch("atexit.register") as mock_atexit:
            mod.register_shutdown(mock_session)
            mod.register_shutdown(mock_session)  # second call is no-op
            assert mock_atexit.call_count == 1
