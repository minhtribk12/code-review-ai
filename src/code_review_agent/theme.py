"""Terminal color theme with auto-detection for light/dark backgrounds.

Provides semantic color names that resolve to high-contrast colors based
on the terminal theme. Detection order:

1. ``COLOR_THEME`` env var (``light``, ``dark``, ``auto``)
2. ``COLORFGBG`` env var (set by many terminals: ``fg;bg`` format)
3. Falls back to universal colors that work on both themes

Usage::

    from code_review_agent.theme import theme

    # Rich markup
    console.print(f"[{theme.WARNING}]Watch out![/{theme.WARNING}]")

    # prompt_toolkit style tuple
    lines.append((theme.WARNING, "Watch out!"))
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class ColorTheme(StrEnum):
    """Terminal color theme."""

    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"


def _detect_theme() -> str:
    """Detect terminal background from environment.

    Returns ``"light"`` or ``"dark"``.
    """
    explicit = os.environ.get("COLOR_THEME", "auto").lower()
    if explicit in ("light", "dark"):
        return explicit

    # COLORFGBG is "fg;bg" -- bg > 6 usually means light background
    colorfgbg = os.environ.get("COLORFGBG", "")
    if ";" in colorfgbg:
        try:
            bg = int(colorfgbg.rsplit(";", 1)[1])
            return "light" if bg > 6 else "dark"
        except (ValueError, IndexError):
            pass

    return "dark"


@dataclass(frozen=True)
class ThemeColors:
    """Semantic color names resolved for the detected terminal theme."""

    # Severity colors
    severity_critical: str
    severity_high: str
    severity_medium: str
    severity_low: str

    # UI colors
    warning: str  # caution messages, cost warnings
    success: str  # confirmations, posted comments
    error: str  # failures, permission denied
    info: str  # status messages, informational
    accent: str  # key hints, file paths, interactive elements
    muted: str  # dim/secondary text
    highlight: str  # selected row, active element


_DARK_THEME = ThemeColors(
    severity_critical="bold red",
    severity_high="red",
    severity_medium="magenta",
    severity_low="green",
    warning="bold magenta",
    success="bold green",
    error="bold red",
    info="bold cyan",
    accent="cyan",
    muted="dim",
    highlight="reverse bold",
)

_LIGHT_THEME = ThemeColors(
    severity_critical="bold red",
    severity_high="dark_red",
    severity_medium="dark_magenta",
    severity_low="dark_green",
    warning="bold dark_magenta",
    success="bold dark_green",
    error="bold red",
    info="bold blue",
    accent="blue",
    muted="dim",
    highlight="reverse bold",
)


def _build_theme() -> ThemeColors:
    detected = _detect_theme()
    if detected == "light":
        return _LIGHT_THEME
    return _DARK_THEME


theme = _build_theme()

# Convenience: severity color lookup by value
SEVERITY_STYLES: dict[str, str] = {
    "critical": theme.severity_critical,
    "high": theme.severity_high,
    "medium": theme.severity_medium,
    "low": theme.severity_low,
}
