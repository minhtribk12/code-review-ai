"""Tests for Phase 8: DeerFlow pattern adoption."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path


# ==================== Sprint 1: Middleware ====================


class TestMiddlewareChain:
    def test_pre_command_all_pass(self) -> None:
        from code_review_agent.interactive.middleware import Middleware, MiddlewareChain

        chain = MiddlewareChain([Middleware(), Middleware()])
        assert chain.run_pre("review", [], MagicMock())

    def test_pre_command_blocks(self) -> None:
        from code_review_agent.interactive.middleware import Middleware, MiddlewareChain

        class Blocker(Middleware):
            def pre_command(self, cmd: str, args: list[str], session: object) -> bool:
                return False

        chain = MiddlewareChain([Blocker()])
        assert not chain.run_pre("review", [], MagicMock())

    def test_post_command_runs(self) -> None:
        from code_review_agent.interactive.middleware import Middleware, MiddlewareChain

        calls: list[str] = []

        class Tracker(Middleware):
            def post_command(self, cmd: str, args: list[str], session: object) -> None:
                calls.append(cmd)

        chain = MiddlewareChain([Tracker()])
        chain.run_post("review", [], MagicMock())
        assert calls == ["review"]

    def test_exception_in_middleware_doesnt_crash(self) -> None:
        from code_review_agent.interactive.middleware import Middleware, MiddlewareChain

        class Broken(Middleware):
            def pre_command(self, cmd: str, args: list[str], session: object) -> bool:
                msg = "boom"
                raise RuntimeError(msg)

        chain = MiddlewareChain([Broken()])
        assert chain.run_pre("test", [], MagicMock())

    def test_build_default_chain(self) -> None:
        from code_review_agent.interactive.middleware import build_default_chain

        chain = build_default_chain()
        assert len(chain._middlewares) >= 3


# ==================== Sprint 2: Persistent Memory ====================


class TestFactStore:
    def test_add_and_retrieve(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("Team uses snake_case", "code_style")
        facts = store.get_top_facts()
        assert len(facts) == 1
        assert facts[0].content == "Team uses snake_case"
        assert facts[0].category == "code_style"

    def test_deduplication(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        id1 = store.add_fact("Uses Python 3.12", "tech_stack")
        id2 = store.add_fact("Uses Python 3.12", "tech_stack")
        assert id1 == id2
        assert store.count() == 1

    def test_whitespace_normalized_dedup(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("Uses  Python   3.12", "tech_stack")
        store.add_fact("Uses Python 3.12", "tech_stack")
        assert store.count() == 1

    def test_reinforce_bumps_confidence(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        fid = store.add_fact("test fact", "code_style", confidence=0.5)
        store.reinforce(fid)
        facts = store.get_top_facts()
        assert facts[0].confidence > 0.5

    def test_decay_reduces_confidence(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("test", "code_style", confidence=0.5)
        store.decay_all(amount=0.1)
        facts = store.get_top_facts()
        assert facts[0].confidence < 0.5

    def test_decay_removes_low_confidence(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("weak", "code_style", confidence=0.1)
        store.decay_all(amount=0.1)
        assert store.count() == 0

    def test_get_by_category(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("Python 3.12", "tech_stack")
        store.add_fact("snake_case", "code_style")
        tech = store.get_facts_by_category("tech_stack")
        assert len(tech) == 1
        assert tech[0].category == "tech_stack"

    def test_format_for_prompt(self, tmp_path: Path) -> None:
        from code_review_agent.memory.fact_store import FactStore, format_facts_for_prompt

        store = FactStore(db_path=tmp_path / "test.db")
        store.add_fact("Uses FastAPI", "tech_stack")
        facts = store.get_top_facts()
        prompt = format_facts_for_prompt(facts)
        assert "<memory>" in prompt
        assert "FastAPI" in prompt
        assert "</memory>" in prompt

    def test_empty_format(self) -> None:
        from code_review_agent.memory.fact_store import format_facts_for_prompt

        assert format_facts_for_prompt([]) == ""


class TestFactExtractor:
    def test_extract_returns_facts(self) -> None:
        from code_review_agent.memory.extractor import extract_facts_from_review

        mock_report = MagicMock()
        mock_result = MagicMock()
        mock_result.agent_name = "security"
        mock_result.findings = [
            MagicMock(
                severity="high", title="SQL injection", file_path="db.py", suggestion="use params"
            )
        ]
        mock_report.agent_results = [mock_result]

        mock_llm = MagicMock()

        from code_review_agent.memory.extractor import ExtractedFact, ExtractionResponse

        mock_llm.complete.return_value = ExtractionResponse(
            facts=[ExtractedFact(content="Project uses raw SQL", category="tech_stack")]
        )
        facts = extract_facts_from_review(mock_report, mock_llm)
        assert len(facts) == 1
        assert facts[0].content == "Project uses raw SQL"

    def test_extract_handles_failure(self) -> None:
        from code_review_agent.memory.extractor import extract_facts_from_review

        mock_report = MagicMock()
        mock_report.agent_results = [
            MagicMock(
                findings=[MagicMock(severity="low", title="x", file_path="y", suggestion="z")]
            )
        ]
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM down")
        facts = extract_facts_from_review(mock_report, mock_llm)
        assert facts == []


# ==================== Sprint 3: Review Skills ====================


class TestSkills:
    def test_builtin_skills_loaded(self) -> None:
        from code_review_agent.skills.loader import load_all_skills

        skills = load_all_skills()
        assert "strict-security" in skills
        assert "api-design" in skills
        assert "performance-deep-dive" in skills
        assert "test-quality" in skills

    def test_skill_has_instructions(self) -> None:
        from code_review_agent.skills.loader import load_all_skills

        skills = load_all_skills()
        sec = skills["strict-security"]
        assert "OWASP" in sec.instructions
        assert sec.category == "security"
        assert sec.source == "builtin"

    def test_format_for_prompt(self) -> None:
        from code_review_agent.skills.loader import format_skills_for_prompt

        prompt = format_skills_for_prompt(["strict-security", "api-design"])
        assert "<skills>" in prompt
        assert "OWASP" in prompt
        assert "REST API" in prompt
        assert "</skills>" in prompt

    def test_format_empty(self) -> None:
        from code_review_agent.skills.loader import format_skills_for_prompt

        assert format_skills_for_prompt([]) == ""
        assert format_skills_for_prompt(["nonexistent"]) == ""

    def test_list_skills(self) -> None:
        from code_review_agent.skills.loader import list_skills

        output = list_skills()
        assert "strict-security" in output
        assert "security" in output

    def test_load_from_file(self, tmp_path: Path) -> None:
        from code_review_agent.skills.loader import load_skill_from_file

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: test\ncategory: custom\n---\n"
            "Do something specific for this project.\n"
        )
        skill = load_skill_from_file(skill_dir / "SKILL.md", "user")
        assert skill is not None
        assert skill.name == "my-skill"
        assert "specific" in skill.instructions


# ==================== Sprint 4: Guardrails ====================


class TestGuardrails:
    def _finding(
        self,
        title: str = "Test",
        confidence: str = "high",
        file_path: str | None = "app.py",
        severity: str = "medium",
    ) -> object:
        from code_review_agent.models import Confidence, Finding, Severity

        return Finding(
            severity=Severity(severity),
            category="test",
            title=title,
            description="desc",
            file_path=file_path,
            confidence=Confidence(confidence),
        )

    def test_keeps_high_confidence(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(confidence="high")]
        result = apply_guardrails(findings)  # type: ignore[arg-type]
        assert len(result.kept) == 1
        assert len(result.filtered) == 0

    def test_filters_low_confidence(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(confidence="low")]
        result = apply_guardrails(findings, confidence_threshold=0.5)  # type: ignore[arg-type]
        assert len(result.kept) == 0
        assert result.filtered[0].reason.startswith("low_confidence")

    def test_filters_test_files(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(file_path="tests/test_app.py")]
        result = apply_guardrails(findings)  # type: ignore[arg-type]
        assert len(result.kept) == 0
        assert "test_file" in result.filtered[0].reason

    def test_skips_test_filter_when_disabled(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(file_path="tests/test_app.py")]
        result = apply_guardrails(findings, exclude_test_files=False)  # type: ignore[arg-type]
        assert len(result.kept) == 1

    def test_filters_suppressed_titles(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(title="eval usage")]
        result = apply_guardrails(findings, suppressed_titles={"eval usage"})  # type: ignore[arg-type]
        assert len(result.kept) == 0

    def test_filters_duplicate_from_previous(self) -> None:
        from code_review_agent.guardrails import apply_guardrails

        findings = [self._finding(title="SQL injection")]
        result = apply_guardrails(findings, previous_titles={"SQL injection"})  # type: ignore[arg-type]
        assert len(result.kept) == 0


# ==================== Sprint 5: Context Summarization ====================


class TestContextSummary:
    def test_summarize_findings(self) -> None:
        from code_review_agent.context_summary import summarize_findings_for_deepening
        from code_review_agent.models import Confidence, Finding, Severity

        findings = [
            Finding(
                severity=Severity.HIGH,
                category="security",
                title="SQL injection",
                description="d",
                file_path="db.py",
                line_number=42,
                confidence=Confidence.HIGH,
            ),
            Finding(
                severity=Severity.MEDIUM,
                category="style",
                title="Long function",
                description="d",
                file_path="app.py",
                line_number=100,
                confidence=Confidence.MEDIUM,
            ),
        ]
        summary = summarize_findings_for_deepening(findings)
        assert "SQL injection" in summary
        assert "db.py:42" in summary
        assert "Long function" in summary
        assert "Do not re-report" in summary

    def test_empty_findings(self) -> None:
        from code_review_agent.context_summary import summarize_findings_for_deepening

        assert summarize_findings_for_deepening([]) == ""

    def test_token_savings(self) -> None:
        from code_review_agent.context_summary import (
            estimate_token_savings,
            summarize_findings_for_deepening,
        )
        from code_review_agent.models import Confidence, Finding, Severity

        findings = [
            Finding(
                severity=Severity.HIGH,
                category="sec",
                title=f"Issue {i}",
                description="A" * 200,
                suggestion="B" * 100,
                file_path=f"file{i}.py",
                confidence=Confidence.HIGH,
            )
            for i in range(10)
        ]
        summary = summarize_findings_for_deepening(findings)
        full_tokens, summary_tokens = estimate_token_savings(findings, summary)
        assert summary_tokens < full_tokens
