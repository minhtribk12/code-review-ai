"""Slash command system: user-defined reusable command workflows as markdown files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SlashCommand:
    """A user-defined slash command parsed from a markdown file."""

    name: str
    description: str
    commands: tuple[str, ...]
    source: str  # "user" or "project"


def parse_slash_command_file(path: Path, source: str) -> SlashCommand | None:
    """Parse a single markdown slash command file.

    Format: YAML frontmatter (between --- lines) with name/description,
    followed by one REPL command per line. Lines starting with # are comments.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        logger.warning(f"failed to read slash command file {path}")
        return None

    # Split frontmatter from body
    parts = text.split("---", maxsplit=2)
    if len(parts) < 3:
        logger.warning(f"slash command file {path} missing YAML frontmatter")
        return None

    try:
        meta = yaml.safe_load(parts[1])
    except Exception:
        logger.warning(f"invalid YAML frontmatter in {path}")
        return None

    if not isinstance(meta, dict):
        return None

    name = meta.get("name", path.stem)
    description = meta.get("description", "")

    # Parse command body: skip blank lines and comments
    body = parts[2]
    commands: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(stripped)

    if not commands:
        logger.warning(f"slash command {name} has no commands")
        return None

    return SlashCommand(
        name=str(name),
        description=str(description),
        commands=tuple(commands),
        source=source,
    )


def load_slash_commands(
    user_dir: Path | None = None,
    project_dir: Path | None = None,
) -> dict[str, SlashCommand]:
    """Load slash commands from user and project directories.

    Project commands override user commands with the same name.
    """
    result: dict[str, SlashCommand] = {}

    user_path = user_dir or Path("~/.cra/commands").expanduser()
    if user_path.is_dir():
        for md_file in sorted(user_path.glob("*.md")):
            cmd = parse_slash_command_file(md_file, source="user")
            if cmd is not None:
                result[cmd.name] = cmd

    if project_dir is not None and project_dir.is_dir():
        for md_file in sorted(project_dir.glob("*.md")):
            cmd = parse_slash_command_file(md_file, source="project")
            if cmd is not None:
                result[cmd.name] = cmd

    return result


def list_slash_commands(commands: dict[str, SlashCommand]) -> str:
    """Format available slash commands for display."""
    if not commands:
        return "  No slash commands defined."
    lines: list[str] = []
    for name, cmd in sorted(commands.items()):
        source_tag = f" ({cmd.source})" if cmd.source == "project" else ""
        lines.append(f"  /{name}{source_tag} - {cmd.description}")
    return "\n".join(lines)
