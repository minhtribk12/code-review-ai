"""YAML-based configuration store and secrets.env store.

Replaces SQLite-based config persistence with two files:
- ``~/.cra/config.yaml`` for all non-secret configuration
- ``~/.cra/secrets.env`` for API keys only
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any  # YAML dicts have dynamic shape by nature

import structlog
import yaml

logger = structlog.get_logger(__name__)

_CRA_DIR = Path("~/.cra").expanduser()
_DEFAULT_CONFIG_PATH = _CRA_DIR / "config.yaml"
_DEFAULT_SECRETS_PATH = _CRA_DIR / "secrets.env"

# Top-level keys in config.yaml that are NOT user config overrides.
_RESERVED_SECTIONS = frozenset({"state", "health"})


class ConfigStore:
    """Read/write ``~/.cra/config.yaml`` for non-secret configuration.

    The YAML file has three sections:
    - Top-level keys: user config overrides (flat key-value)
    - ``state:``: transient app state (active_repo, etc.)
    - ``health:``: provider/model health marks
    """

    def __init__(self, path: Path | None = None, project_path: Path | None = None) -> None:
        self.path = path or _DEFAULT_CONFIG_PATH
        self.project_path = project_path

    # -- Low-level I/O --------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Read the YAML file and return its contents as a dict."""
        if not self.path.is_file():
            return {}
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            return raw
        except Exception:
            logger.debug("failed to read config yaml", path=str(self.path), exc_info=True)
            return {}

    def save(self, data: dict[str, Any]) -> None:
        """Write data to the YAML file atomically (write tmp + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent),
                suffix=".yaml.tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        data,
                        f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                os.replace(tmp_path, str(self.path))
            except Exception:
                # Clean up temp file on failure
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except Exception:
            logger.debug("failed to write config yaml", path=str(self.path), exc_info=True)

    # -- Config overrides (flat key-value) ------------------------------------

    def get(self, key: str) -> str | None:
        """Get a single config override value."""
        data = self.load()
        val = data.get(key)
        if val is None or key in _RESERVED_SECTIONS:
            return None
        return str(val)

    def set_value(self, key: str, value: str) -> None:
        """Set a single config override value."""
        data = self.load()
        data[key] = value
        self.save(data)

    def delete(self, key: str) -> None:
        """Remove a config override key."""
        data = self.load()
        if key in data and key not in _RESERVED_SECTIONS:
            del data[key]
            self.save(data)

    def load_all_overrides(self) -> dict[str, str]:
        """Return all config override key-value pairs (excludes state/health).

        If a project_path is set, project values override user values.
        """
        data = self.load()
        result = {
            k: str(v) for k, v in data.items() if k not in _RESERVED_SECTIONS and v is not None
        }
        # Merge project overrides on top (project wins)
        for k, v in self.load_project_overrides().items():
            result[k] = v
        return result

    def load_project_overrides(self) -> dict[str, str]:
        """Return overrides from the project-level config only."""
        if self.project_path is None or not self.project_path.is_file():
            return {}
        try:
            raw = yaml.safe_load(self.project_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            return {
                k: str(v) for k, v in raw.items() if k not in _RESERVED_SECTIONS and v is not None
            }
        except Exception:
            logger.debug(
                "failed to read project config",
                path=str(self.project_path),
                exc_info=True,
            )
            return {}

    def clear_overrides(self) -> None:
        """Remove all config override keys, preserve state and health."""
        data = self.load()
        preserved = {}
        for section in _RESERVED_SECTIONS:
            if section in data:
                preserved[section] = data[section]
        self.save(preserved)

    # -- Health marks ---------------------------------------------------------

    def set_health(self, kind: str, name: str, status: str) -> None:
        """Set a health mark (e.g., kind='provider', name='nvidia', status='not_working')."""
        data = self.load()
        health = data.setdefault("health", {})
        kind_map = health.setdefault(kind, {})
        kind_map[name] = status
        self.save(data)

    def clear_health(self, kind: str, name: str) -> None:
        """Remove a health mark."""
        data = self.load()
        health = data.get("health", {})
        kind_map = health.get(kind, {})
        if name in kind_map:
            del kind_map[name]
            # Clean up empty dicts
            if not kind_map and kind in health:
                del health[kind]
            if not health and "health" in data:
                del data["health"]
            self.save(data)

    def load_health(self) -> dict[str, set[str]]:
        """Load all health marks. Returns {kind: {name, ...}}."""
        data = self.load()
        health = data.get("health", {})
        result: dict[str, set[str]] = {"model": set(), "provider": set()}
        for kind, entries in health.items():
            if isinstance(entries, dict):
                for name, status in entries.items():
                    if status == "not_working":
                        result.setdefault(kind, set()).add(name)
        return result

    # -- Transient state ------------------------------------------------------

    def set_state(self, key: str, value: str) -> None:
        """Set a transient state value (under 'state:' section)."""
        data = self.load()
        state = data.setdefault("state", {})
        state[key] = value
        self.save(data)

    def get_state(self, key: str) -> str | None:
        """Get a transient state value."""
        data = self.load()
        state = data.get("state", {})
        val = state.get(key)
        return str(val) if val is not None else None

    def delete_state(self, key: str) -> None:
        """Remove a transient state value."""
        data = self.load()
        state = data.get("state", {})
        if key in state:
            del state[key]
            if not state and "state" in data:
                del data["state"]
            self.save(data)


class SecretsStore:
    """Read/write ``~/.cra/secrets.env`` for API keys.

    Format: one ``KEY=value`` per line, no quoting, no comments.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_SECRETS_PATH

    @staticmethod
    def _env_key(provider: str) -> str:
        """Return the environment variable name for a provider's API key."""
        return f"{provider.upper()}_API_KEY"

    def _read_lines(self) -> list[str]:
        """Read secrets.env lines, returning empty list if missing."""
        if not self.path.is_file():
            return []
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except Exception:
            logger.debug("failed to read secrets.env", path=str(self.path), exc_info=True)
            return []

    def _write_lines(self, lines: list[str]) -> None:
        """Write lines to secrets.env atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent),
                suffix=".env.tmp",
            )
            try:
                content = "\n".join(lines)
                if content and not content.endswith("\n"):
                    content += "\n"
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, str(self.path))
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except Exception:
            logger.debug("failed to write secrets.env", path=str(self.path), exc_info=True)

    def _parse(self) -> dict[str, str]:
        """Parse secrets.env into a dict."""
        result: dict[str, str] = {}
        for line in self._read_lines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip()
        return result

    def load_key(self, provider: str) -> str:
        """Load an API key for a provider from secrets.env."""
        env_key = self._env_key(provider)
        return self._parse().get(env_key, "")

    def save_key(self, provider: str, value: str) -> None:
        """Save or update an API key in secrets.env and inject into os.environ."""
        env_key = self._env_key(provider)
        lines = self._read_lines()
        found = False
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{env_key}=") or stripped.startswith(f"{env_key} ="):
                new_lines.append(f"{env_key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{env_key}={value}")
        self._write_lines(new_lines)
        os.environ[env_key] = value

    def delete_key(self, provider: str) -> None:
        """Remove an API key from secrets.env and clear os.environ."""
        env_key = self._env_key(provider)
        lines = self._read_lines()
        new_lines = [
            line
            for line in lines
            if not line.strip().startswith(f"{env_key}=")
            and not line.strip().startswith(f"{env_key} =")
        ]
        if len(new_lines) != len(lines):
            self._write_lines(new_lines)
        os.environ.pop(env_key, None)

    def load_all_keys(self) -> dict[str, str]:
        """Return all API keys as {provider_lower: value}.

        Only returns entries matching the ``*_API_KEY`` pattern.
        """
        result: dict[str, str] = {}
        for env_key, value in self._parse().items():
            if env_key.endswith("_API_KEY") and value:
                # Convert NVIDIA_API_KEY -> nvidia
                provider = env_key.removesuffix("_API_KEY").lower()
                result[provider] = value
        return result

    def inject_to_env(self) -> None:
        """Load all keys from secrets.env into os.environ."""
        for env_key, value in self._parse().items():
            if env_key.endswith("_API_KEY") and value:
                os.environ[env_key] = value


def migrate_from_db(db_path: str | Path) -> bool:
    """One-time migration: move config from SQLite DB to YAML + secrets.env.

    Reads all ``config:*`` entries from the ``finding_settings`` table and
    writes them to ``~/.cra/config.yaml`` (non-secret) and
    ``~/.cra/secrets.env`` (API keys). Also migrates ``~/.cra/providers.json``
    to ``~/.cra/providers.yaml`` if present.

    Returns True if any data was migrated.
    """
    import sqlite3

    db_path = Path(db_path).expanduser()
    if not db_path.is_file():
        _migrate_providers_json()
        return False

    config_store = ConfigStore()
    secrets_store = SecretsStore()

    # Skip if config.yaml already exists (already migrated)
    if config_store.path.is_file():
        _migrate_providers_json()
        return False

    migrated = False
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT key, value FROM finding_settings WHERE key LIKE 'config:%'"
        ).fetchall()
        conn.close()

        if not rows:
            _migrate_providers_json()
            return False

        config_data: dict[str, Any] = {}
        health: dict[str, dict[str, str]] = {}
        state: dict[str, str] = {}

        for row in rows:
            raw_key = str(row["key"])[7:]  # strip "config:" prefix
            value = str(row["value"])

            if raw_key.endswith("_api_key") and value:
                # API key -> secrets.env
                provider = raw_key.removesuffix("_api_key")
                secrets_store.save_key(provider, value)
                migrated = True
            elif raw_key.startswith("health:"):
                # health:provider:nvidia -> health section
                parts = raw_key.split(":", 2)
                if len(parts) == 3:
                    kind, name = parts[1], parts[2]
                    health.setdefault(kind, {})[name] = value
                    migrated = True
            elif raw_key in ("active_repo", "active_repo_source"):
                state[raw_key] = value
                migrated = True
            else:
                config_data[raw_key] = value
                migrated = True

        if health:
            config_data["health"] = health
        if state:
            config_data["state"] = state

        if config_data:
            config_store.save(config_data)

        if migrated:
            logger.info(
                "migrated config from database to YAML",
                config_keys=len(config_data),
                api_keys=len(secrets_store.load_all_keys()),
            )
    except Exception:
        logger.debug("failed to migrate config from database", exc_info=True)

    _migrate_providers_json()
    return migrated


def _migrate_providers_json() -> None:
    """Migrate ~/.cra/providers.json to ~/.cra/providers.yaml if needed."""
    import json

    json_path = Path("~/.cra/providers.json").expanduser()
    yaml_path = Path("~/.cra/providers.yaml").expanduser()

    if not json_path.is_file() or yaml_path.is_file():
        return

    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        yaml_path.write_text(
            yaml.safe_dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.info("migrated providers.json to providers.yaml")
    except Exception:
        logger.debug("failed to migrate providers.json", exc_info=True)
