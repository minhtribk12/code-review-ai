"""Config commands: show, get, set, save, reset, validate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

# Config keys grouped by category for display.
_CONFIG_CATEGORIES: dict[str, list[str]] = {
    "LLM": [
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "llm_temperature",
        "llm_api_key",
        "request_timeout_seconds",
    ],
    "Token Budget": [
        "token_tier",
        "max_prompt_tokens",
        "max_tokens_per_review",
        "llm_input_price_per_m",
        "llm_output_price_per_m",
        "rate_limit_rpm",
    ],
    "Review": [
        "dedup_strategy",
        "max_review_seconds",
        "max_concurrent_agents",
    ],
    "GitHub": [
        "github_token",
        "max_pr_files",
        "github_rate_limit_warn_threshold",
    ],
}


def _mask_secret(value: object) -> str:
    """Mask secret values for display."""
    if isinstance(value, SecretStr):
        raw = value.get_secret_value()
        if len(raw) <= 8:
            return "****"
        return f"{raw[:4]}****{raw[-4:]}"
    return str(value) if value is not None else "[dim]not set[/dim]"


def _get_config_value(session: SessionState, key: str) -> object:
    """Get a config value, checking session overrides first."""
    if key in session.config_overrides:
        return session.config_overrides[key]
    return getattr(session.settings, key, None)


def cmd_config(args: list[str], session: SessionState) -> None:
    """Show all config or a specific category."""
    if args and args[0] == "show":
        args = args[1:]
    if args and args[0] == "edit":
        from code_review_agent.interactive.commands.config_edit import cmd_config_edit

        return cmd_config_edit(args[1:], session)
    if args and args[0] == "get":
        return cmd_config_get(args[1:], session)
    if args and args[0] == "set":
        return cmd_config_set(args[1:], session)
    if args and args[0] == "save":
        return cmd_config_save(args[1:], session)
    if args and args[0] == "reset":
        return cmd_config_reset(args[1:], session)
    if args and args[0] == "validate":
        return cmd_config_validate(args[1:], session)
    if args and args[0] == "diff":
        return cmd_config_diff(args[1:], session)

    # Show specific category
    if args:
        category = args[0].upper()
        for cat_name, keys in _CONFIG_CATEGORIES.items():
            if cat_name.upper().startswith(category):
                _print_category(cat_name, keys, session)
                return
        console.print(f"[red]Unknown category: {args[0]}[/red]")
        console.print(f"Available: {', '.join(_CONFIG_CATEGORIES)}")
        return

    # Show all categories
    for cat_name, keys in _CONFIG_CATEGORIES.items():
        _print_category(cat_name, keys, session)


def _print_category(name: str, keys: list[str], session: SessionState) -> None:
    """Print a config category as a Rich table."""
    table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold", width=35)
    table.add_column("Value")

    for key in keys:
        value = _get_config_value(session, key)
        display = _mask_secret(value)
        is_overridden = key in session.config_overrides
        if is_overridden:
            display = f"{display} [yellow](session override)[/yellow]"
        table.add_row(key, display)

    console.print(Panel(table, title=name, border_style="blue"))


def cmd_config_get(args: list[str], session: SessionState) -> None:
    """Show a single config value."""
    if not args:
        console.print("[red]Usage: config get <key>[/red]")
        return
    key = args[0]
    value = _get_config_value(session, key)
    if value is None and not hasattr(session.settings, key):
        console.print(f"[red]Unknown config key: {key}[/red]")
        return
    console.print(f"  {key} = {_mask_secret(value)}")


def cmd_config_set(args: list[str], session: SessionState) -> None:
    """Set a config value for this session only (not persisted)."""
    if len(args) < 2:
        console.print("[red]Usage: config set <key> <value>[/red]")
        return
    key = args[0]
    value = " ".join(args[1:])

    if not hasattr(session.settings, key):
        console.print(f"[red]Unknown config key: {key}[/red]")
        return

    session.config_overrides[key] = value
    session.invalidate_settings_cache()
    console.print(f"  [green]{key}[/green] = {value} [dim](session only)[/dim]")


def cmd_config_save(args: list[str], session: SessionState) -> None:
    """Persist session config overrides to .env file."""
    if not session.config_overrides:
        console.print("[dim]No session overrides to save.[/dim]")
        return

    console.print("[bold]Changes to save:[/bold]")
    for key, value in session.config_overrides.items():
        console.print(f"  {key} = {value}")
    console.print()
    console.print("[yellow]config save is not yet implemented (coming in Phase 3d).[/yellow]")


def cmd_config_reset(args: list[str], session: SessionState) -> None:
    """Reload config from .env, discard session overrides."""
    count = len(session.config_overrides)
    session.config_overrides.clear()
    session.invalidate_settings_cache()
    console.print(
        f"  [green]Reset {count} session override(s). Config reloaded from .env.[/green]"
    )


def cmd_config_validate(args: list[str], session: SessionState) -> None:
    """Validate current config."""
    try:
        from code_review_agent.config import Settings

        Settings()  # type: ignore[call-arg]
        console.print("[green]Config is valid.[/green]")
    except Exception as exc:
        console.print(f"[red]Config validation error: {exc}[/red]")


def cmd_config_diff(args: list[str], session: SessionState) -> None:
    """Show differences between session and .env config."""
    if not session.config_overrides:
        console.print("[dim]No session overrides. Config matches .env.[/dim]")
        return
    for key, value in session.config_overrides.items():
        original = getattr(session.settings, key, None)
        console.print(f"  {key}: {_mask_secret(original)} -> {value}")
