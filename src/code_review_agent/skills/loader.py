"""Review skills: domain-specific instructions injected into agent prompts.

Inspired by DeerFlow's SKILL.md system. Each skill is a markdown file
with YAML frontmatter (name, description, category) and body text
containing instructions for the review agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)

_USER_SKILLS_DIR = Path("~/.cra/skills").expanduser()
_PROJECT_SKILLS_DIR = Path(".cra/skills")

# Built-in skills embedded in the package
_BUILTIN_SKILLS: dict[str, dict[str, str]] = {
    "strict-security": {
        "name": "strict-security",
        "description": "Flag ALL security issues including low-severity",
        "category": "security",
        "instructions": (
            "Review with maximum security scrutiny. Flag ALL potential security issues "
            "including low-severity ones. Check OWASP Top 10 categories systematically: "
            "injection, broken auth, sensitive data exposure, XXE, broken access control, "
            "security misconfiguration, XSS, insecure deserialization, known vulnerabilities, "
            "insufficient logging. For each finding, rate the exploitability and impact."
        ),
    },
    "api-design": {
        "name": "api-design",
        "description": "Review REST API design patterns and conventions",
        "category": "architecture",
        "instructions": (
            "Focus on REST API design quality: resource naming conventions (plural nouns, "
            "kebab-case), HTTP status codes (200/201/204/400/401/403/404/409/422), "
            "pagination (cursor vs offset, Link headers), filtering (query params, not path), "
            "error response format (RFC 7807), versioning strategy, rate limiting headers, "
            "request/response envelope consistency, HATEOAS links where appropriate."
        ),
    },
    "performance-deep-dive": {
        "name": "performance-deep-dive",
        "description": "Deep analysis of algorithmic complexity and resource usage",
        "category": "performance",
        "instructions": (
            "Analyze every function for algorithmic complexity (Big-O notation). Flag: "
            "O(n^2) or worse in hot paths, unnecessary memory allocations, missing caching "
            "opportunities, N+1 query patterns, unbounded collection growth, blocking I/O "
            "in async contexts, string concatenation in loops, redundant computation. "
            "Suggest concrete optimizations with expected improvement."
        ),
    },
    "test-quality": {
        "name": "test-quality",
        "description": "Assess test coverage, edge cases, and test design",
        "category": "testing",
        "instructions": (
            "Evaluate test quality: coverage of happy path + edge cases + error cases, "
            "test isolation (no shared mutable state), determinism (no sleep/real network), "
            "mock vs real dependencies (prefer real at boundaries), assertion quality "
            "(specific not assertTrue(x)), test naming (describes behavior not implementation), "
            "arrange-act-assert structure, fixture reuse patterns."
        ),
    },
}


@dataclass(frozen=True)
class ReviewSkill:
    """A loaded review skill with instructions."""

    name: str
    description: str
    category: str
    instructions: str
    source: str  # "builtin", "user", "project"


def load_skill_from_file(path: Path, source: str) -> ReviewSkill | None:
    """Load a skill from a SKILL.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    parts = text.split("---", maxsplit=2)
    if len(parts) < 3:
        return None

    try:
        meta = yaml.safe_load(parts[1])
    except Exception:
        return None

    if not isinstance(meta, dict):
        return None

    instructions = parts[2].strip()
    if not instructions:
        return None

    return ReviewSkill(
        name=str(meta.get("name", path.parent.name)),
        description=str(meta.get("description", "")),
        category=str(meta.get("category", "general")),
        instructions=instructions,
        source=source,
    )


def load_all_skills() -> dict[str, ReviewSkill]:
    """Load all available skills: builtin + user + project."""
    skills: dict[str, ReviewSkill] = {}

    # Built-in skills
    for name, data in _BUILTIN_SKILLS.items():
        skills[name] = ReviewSkill(
            name=data["name"],
            description=data["description"],
            category=data["category"],
            instructions=data["instructions"],
            source="builtin",
        )

    # User skills (~/.cra/skills/)
    if _USER_SKILLS_DIR.is_dir():
        for skill_dir in sorted(_USER_SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md" if skill_dir.is_dir() else None
            if skill_file and skill_file.is_file():
                skill = load_skill_from_file(skill_file, "user")
                if skill:
                    skills[skill.name] = skill

    # Project skills (.cra/skills/)
    if _PROJECT_SKILLS_DIR.is_dir():
        for skill_dir in sorted(_PROJECT_SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md" if skill_dir.is_dir() else None
            if skill_file and skill_file.is_file():
                skill = load_skill_from_file(skill_file, "project")
                if skill:
                    skills[skill.name] = skill

    logger.debug("skills_loaded", count=len(skills))
    return skills


def format_skills_for_prompt(skill_names: list[str]) -> str:
    """Format selected skills for injection into agent system prompts."""
    all_skills = load_all_skills()
    active = [all_skills[n] for n in skill_names if n in all_skills]
    if not active:
        return ""

    lines = ["<skills>", "Active review skills:"]
    for s in active:
        lines.append(f"\n[{s.name}] ({s.category})")
        lines.append(s.instructions)
    lines.append("</skills>")
    return "\n".join(lines)


def list_skills() -> str:
    """Format all skills for display."""
    skills = load_all_skills()
    if not skills:
        return "  No skills available."
    lines: list[str] = []
    for name, skill in sorted(skills.items()):
        lines.append(f"  {name:<25} [{skill.category}] {skill.description} ({skill.source})")
    return "\n".join(lines)
