from __future__ import annotations

import pytest

from code_review_agent.models import DiffFile, DiffStatus, ReviewInput
from code_review_agent.token_budget import (
    _MODEL_CONTEXT_WINDOWS,
    _MODEL_PRICING,
    _TIER_BUDGETS,
    CharBasedEstimator,
    TokenTier,
    estimate_cost,
    resolve_prompt_budget,
)

# ---------------------------------------------------------------------------
# CharBasedEstimator
# ---------------------------------------------------------------------------


class TestCharBasedEstimator:
    """Test the character-based token estimator."""

    def test_default_chars_per_token(self) -> None:
        est = CharBasedEstimator()
        # 12 chars / 3 = 4 tokens (exact division)
        assert est.estimate("hello world!") == 4

    def test_custom_chars_per_token(self) -> None:
        est = CharBasedEstimator(chars_per_token=4)
        # 12 chars / 4 = 3 tokens (exact division)
        assert est.estimate("hello world!") == 3

    def test_empty_string(self) -> None:
        est = CharBasedEstimator()
        assert est.estimate("") == 0

    def test_code_like_string(self) -> None:
        est = CharBasedEstimator()
        code = "def authenticate(username: str, password: str) -> bool:\n"
        # Ceiling division overestimates to avoid exceeding context limits
        assert est.estimate(code) > 0
        expected = -(-len(code) // 3)
        assert est.estimate(code) == expected

    def test_long_diff(self) -> None:
        est = CharBasedEstimator()
        long_text = "+new line\n" * 1000  # 10000 chars
        # 10000 / 3 = 3333.33 -> ceiling = 3334
        assert est.estimate(long_text) == -(-10000 // 3)

    def test_ceiling_rounds_up(self) -> None:
        est = CharBasedEstimator()
        # 7 chars / 3 = 2.33 -> ceiling = 3 (not floor 2)
        assert est.estimate("abcdefg") == 3


# ---------------------------------------------------------------------------
# TokenEstimator Protocol -- pluggability
# ---------------------------------------------------------------------------


class TestTokenEstimatorProtocol:
    """Verify custom estimators can be swapped in."""

    def test_custom_estimator_accepted(self) -> None:
        class FixedEstimator:
            def estimate(self, text: str) -> int:
                return 42

        est = FixedEstimator()
        assert est.estimate("anything") == 42


# ---------------------------------------------------------------------------
# resolve_prompt_budget
# ---------------------------------------------------------------------------


class TestResolvePromptBudget:
    """Test the budget resolution hierarchy."""

    def test_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("MAX_PROMPT_TOKENS", "99999")
        monkeypatch.setenv("TOKEN_TIER", "free")
        monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")  # known model

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        budget = resolve_prompt_budget(settings)
        assert budget == 99999

    def test_model_auto_detect_when_no_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("MAX_PROMPT_TOKENS", raising=False)
        monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
        monkeypatch.setenv("TOKEN_TIER", "free")

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        budget = resolve_prompt_budget(settings)
        # nemotron-3-super has 1000000 context, budget = 1000000 * 0.4 = 400000
        assert budget == int(1_000_000 * 0.4)

    def test_tier_fallback_for_unknown_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("MAX_PROMPT_TOKENS", raising=False)
        monkeypatch.setenv("LLM_MODEL", "unknown/custom-model-xyz")
        monkeypatch.setenv("TOKEN_TIER", "standard")

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        budget = resolve_prompt_budget(settings)
        assert budget == _TIER_BUDGETS[TokenTier.STANDARD]

    def test_free_tier_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("MAX_PROMPT_TOKENS", raising=False)
        monkeypatch.setenv("LLM_MODEL", "unknown/model")
        monkeypatch.setenv("TOKEN_TIER", "free")

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        budget = resolve_prompt_budget(settings)
        assert budget == 5000


# ---------------------------------------------------------------------------
# Orchestrator truncation
# ---------------------------------------------------------------------------


def _make_diff_file(filename: str, lines: int) -> DiffFile:
    """Create a DiffFile with a patch of N lines."""
    patch = "".join(f"+line {i}\n" for i in range(lines))
    return DiffFile(filename=filename, patch=patch, status=DiffStatus.MODIFIED)


def _make_review_input(files: list[DiffFile]) -> ReviewInput:
    return ReviewInput(
        diff_files=files,
        pr_url="https://github.com/test/repo/pull/1",
        pr_title="Test PR",
    )


class TestOrchestratorTruncation:
    """Test token budget enforcement and two-pass truncation."""

    def test_under_budget_no_truncation(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        small_file = _make_diff_file("small.py", 5)
        review_input = _make_review_input([small_file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 999999  # huge budget

        result = orch._apply_token_budget(review_input)
        assert result is review_input  # no change, same object

    def test_over_budget_triggers_truncation(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        big_file = _make_diff_file("big.py", 500)
        review_input = _make_review_input([big_file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 10  # tiny budget

        result = orch._apply_token_budget(review_input)
        assert result is not review_input
        assert "[TRUNCATED]" in result.diff_files[0].patch

    def test_two_pass_keeps_most_changed(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        big_file = _make_diff_file("big.py", 200)  # ~2000 chars
        medium_file = _make_diff_file("med.py", 50)  # ~500 chars
        small_file = _make_diff_file("small.py", 5)  # ~50 chars

        review_input = _make_review_input([small_file, big_file, medium_file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        # Budget fits big + small but not medium
        big_tokens = orch._estimator.estimate(big_file.patch)
        small_tokens = orch._estimator.estimate(small_file.patch)
        orch._budget = big_tokens + small_tokens + 10  # tight

        result = orch._truncate_review_input(review_input)

        filenames_full = [f.filename for f in result.diff_files if "[TRUNCATED]" not in f.patch]
        filenames_truncated = [f.filename for f in result.diff_files if "[TRUNCATED]" in f.patch]

        # big.py should be kept (most changed), small.py fits too
        assert "big.py" in filenames_full
        # medium or small may be truncated depending on exact budget
        assert len(filenames_truncated) >= 1

    def test_truncated_file_has_line_counts(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        file = _make_diff_file("truncated.py", 100)
        review_input = _make_review_input([file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 1  # force truncation

        result = orch._truncate_review_input(review_input)
        patch = result.diff_files[0].patch
        assert "[TRUNCATED]" in patch
        assert "+" in patch  # has added count
        assert "/" in patch  # has separator

    def test_preserves_pr_metadata(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        file = _make_diff_file("f.py", 100)
        review_input = _make_review_input([file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 1

        result = orch._truncate_review_input(review_input)
        assert result.pr_url == review_input.pr_url
        assert result.pr_title == review_input.pr_title

    def test_all_files_fit(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        files = [_make_diff_file(f"f{i}.py", 3) for i in range(5)]
        review_input = _make_review_input(files)

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 999999

        result = orch._apply_token_budget(review_input)
        assert result is review_input
        assert len(result.diff_files) == 5

    def test_empty_diff_files(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        review_input = _make_review_input([])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 5000

        result = orch._apply_token_budget(review_input)
        assert result is review_input
        assert len(result.diff_files) == 0

    def test_single_file_over_entire_budget(self) -> None:
        from code_review_agent.orchestrator import Orchestrator

        huge_file = _make_diff_file("huge.py", 10000)
        review_input = _make_review_input([huge_file])

        orch = Orchestrator.__new__(Orchestrator)
        orch._estimator = CharBasedEstimator()
        orch._budget = 10

        result = orch._truncate_review_input(review_input)
        assert len(result.diff_files) == 1
        assert "[TRUNCATED]" in result.diff_files[0].patch


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class TestModelRegistry:
    """Verify model context window registry."""

    def test_known_models_have_positive_context(self) -> None:
        for model, context in _MODEL_CONTEXT_WINDOWS.items():
            assert context > 0, f"{model} has non-positive context window"

    def test_nemotron_is_registered(self) -> None:
        assert "nvidia/nemotron-3-super-120b-a12b" in _MODEL_CONTEXT_WINDOWS

    def test_nemotron_nano_is_registered(self) -> None:
        assert "nvidia/nemotron-3-nano-30b-a3b" in _MODEL_CONTEXT_WINDOWS


# ---------------------------------------------------------------------------
# TokenTier enum
# ---------------------------------------------------------------------------


class TestTokenTier:
    """Verify tier values."""

    def test_free_tier_value(self) -> None:
        assert _TIER_BUDGETS[TokenTier.FREE] == 5000

    def test_standard_tier_value(self) -> None:
        assert _TIER_BUDGETS[TokenTier.STANDARD] == 16000

    def test_premium_tier_value(self) -> None:
        assert _TIER_BUDGETS[TokenTier.PREMIUM] == 48000


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class TestEstimateCost:
    """Test cost estimation with custom and auto-detected pricing."""

    def test_custom_pricing(self) -> None:
        cost = estimate_cost(
            model="unknown/model",
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            input_price_per_m=1.00,
            output_price_per_m=2.00,
        )
        assert cost is not None
        assert cost == pytest.approx(2.0)

    def test_auto_detect_known_model(self) -> None:
        cost = estimate_cost(
            model="nvidia/nemotron-3-super-120b-a12b",
            prompt_tokens=10_000,
            completion_tokens=5_000,
        )
        assert cost is not None
        # Free model, pricing is 0
        assert cost >= 0

    def test_unknown_model_returns_none(self) -> None:
        cost = estimate_cost(
            model="unknown/mystery-model",
            prompt_tokens=10_000,
            completion_tokens=5_000,
        )
        assert cost is None

    def test_custom_overrides_auto_detect(self) -> None:
        # Custom pricing should be used even for known models
        cost = estimate_cost(
            model="nvidia/nemotron-3-super-120b-a12b",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            input_price_per_m=99.0,
            output_price_per_m=0.0,
        )
        assert cost == pytest.approx(99.0)

    def test_zero_tokens_zero_cost(self) -> None:
        cost = estimate_cost(
            model="nvidia/nemotron-3-super-120b-a12b",
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert cost == pytest.approx(0.0)

    def test_pricing_registry_matches_context_registry(self) -> None:
        """All models with pricing should also have context windows."""
        for model in _MODEL_PRICING:
            assert model in _MODEL_CONTEXT_WINDOWS, f"{model} has pricing but no context window"

    def test_pricing_values_positive(self) -> None:
        for model, (input_p, output_p) in _MODEL_PRICING.items():
            assert input_p >= 0, f"{model} has negative input price"
            assert output_p >= 0, f"{model} has negative output price"

    def test_partial_custom_pricing_ignored(self) -> None:
        """If only one price is provided, fall back to auto-detect."""
        cost = estimate_cost(
            model="nvidia/nemotron-3-super-120b-a12b",
            prompt_tokens=10_000,
            completion_tokens=5_000,
            input_price_per_m=1.0,
            output_price_per_m=None,
        )
        # Should use auto-detect (not custom, since output is None)
        assert cost is not None
