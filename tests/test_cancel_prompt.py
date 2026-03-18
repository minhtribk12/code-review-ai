"""Tests for cancellation prompt and orchestrator abort/cancel behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_review_agent.cancel_prompt import CancelChoice, prompt_cancel_choice


class TestPromptCancelChoice:
    """Tests for the 3-option cancel prompt."""

    def test_choice_abort(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value="1"):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.ABORT

    def test_choice_finish_partial(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value="2"):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.FINISH_PARTIAL

    def test_choice_continue(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value="3"):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.CONTINUE

    def test_invalid_input_defaults_to_abort(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value="x"):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.ABORT

    def test_empty_input_defaults_to_abort(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value=""):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.ABORT

    def test_keyboard_interrupt_defaults_to_abort(self) -> None:
        console = MagicMock()
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.ABORT

    def test_eof_defaults_to_abort(self) -> None:
        console = MagicMock()
        with patch("builtins.input", side_effect=EOFError):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.ABORT

    def test_whitespace_around_choice(self) -> None:
        console = MagicMock()
        with patch("builtins.input", return_value="  2  "):
            result = prompt_cancel_choice(console)
        assert result == CancelChoice.FINISH_PARTIAL


class TestOrchestratorAbort:
    """Tests for the abort() vs cancel() distinction on Orchestrator."""

    def test_abort_sets_both_events(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        settings = MagicMock()
        settings.token_tier = "free"  # noqa: S105
        settings.max_prompt_tokens = None
        llm_client = MagicMock()

        orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
        orchestrator.abort()

        assert orchestrator._aborted.is_set()
        assert orchestrator._cancelled.is_set()

    def test_cancel_does_not_set_aborted(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        settings = MagicMock()
        settings.token_tier = "free"  # noqa: S105
        settings.max_prompt_tokens = None
        llm_client = MagicMock()

        orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
        orchestrator.cancel()

        assert orchestrator._cancelled.is_set()
        assert not orchestrator._aborted.is_set()

    def test_abort_skips_synthesis(self) -> None:
        """When aborted, run() should not call _synthesize."""
        from code_review_agent.models import ReviewInput
        from code_review_agent.orchestrator import Orchestrator

        settings = MagicMock()
        settings.token_tier = "free"  # noqa: S105
        settings.max_prompt_tokens = None
        settings.max_deepening_rounds = 1
        settings.dedup_strategy = "exact"
        settings.max_concurrent_agents = 4
        settings.max_review_seconds = 60
        settings.max_tokens_per_review = None
        settings.is_validation_enabled = False

        llm_client = MagicMock()
        llm_client.get_usage.return_value = MagicMock(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            llm_calls=0,
        )

        orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
        orchestrator.abort()

        review_input = ReviewInput(
            diff_files=[],
            pr_url=None,
            pr_title=None,
            pr_description=None,
        )

        report = orchestrator.run(review_input=review_input, agent_names=[])
        assert report is not None
        llm_client.complete.assert_not_called()


class TestRunWithCancelSupport:
    """Tests for the shared run_with_cancel_support function."""

    def test_normal_completion_returns_report(self) -> None:
        from code_review_agent.cancel_prompt import run_with_cancel_support
        from code_review_agent.models import ReviewInput

        mock_report = MagicMock()
        orchestrator = MagicMock()
        orchestrator.run.return_value = mock_report

        review_input = ReviewInput(
            diff_files=[],
            pr_url=None,
            pr_title=None,
            pr_description=None,
        )
        console = MagicMock()

        result = run_with_cancel_support(
            orchestrator,
            review_input,
            None,
            None,
            console,
        )
        assert result is mock_report
