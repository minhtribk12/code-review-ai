"""User-customizable keybindings loaded from ~/.cra/keybindings.yaml."""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)

DEFAULT_KEYBINDINGS: dict[str, str] = {
    "agent_selector": "c-a",
    "provider_selector": "c-p",
    "repo_selector": "c-o",
    "git_graph": "c-l",
}


def load_keybindings(path: Path | None = None) -> dict[str, str]:
    """Load keybindings from YAML, merging user overrides over defaults."""
    resolved = path or Path("~/.cra/keybindings.yaml").expanduser()
    if not resolved.is_file():
        return dict(DEFAULT_KEYBINDINGS)
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULT_KEYBINDINGS)
        merged = dict(DEFAULT_KEYBINDINGS)
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and k in DEFAULT_KEYBINDINGS:
                merged[k] = v
        return merged
    except Exception:
        logger.warning(f"failed to parse keybindings from {resolved}, using defaults")
        return dict(DEFAULT_KEYBINDINGS)


def get_key_for_action(bindings: dict[str, str], action: str) -> str:
    """Return the key string for an action, or empty string if unknown."""
    return bindings.get(action, "")
