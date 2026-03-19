"""Interactive REPL for the code review agent."""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.styles import Style
from rich.console import Console

from code_review_agent.error_guidance import classify_exception
from code_review_agent.errors import UserError, print_error
from code_review_agent.interactive.commands.config_cmd import cmd_config
from code_review_agent.interactive.commands.findings_cmd import cmd_findings
from code_review_agent.interactive.commands.git_read import (
    cmd_diff,
    cmd_log,
    cmd_show,
    cmd_status,
)
from code_review_agent.interactive.commands.git_write import (
    cmd_add,
    cmd_branch,
    cmd_cd,
    cmd_commit,
    cmd_stash,
    cmd_unstage,
)
from code_review_agent.interactive.commands.history_cmd import cmd_history
from code_review_agent.interactive.commands.meta import (
    cmd_agents,
    cmd_clear,
    cmd_help,
    cmd_shell,
    cmd_version,
)
from code_review_agent.interactive.commands.pr_read import cmd_pr
from code_review_agent.interactive.commands.provider_cmd import cmd_provider
from code_review_agent.interactive.commands.repo_cmd import cmd_repo
from code_review_agent.interactive.commands.review_cmd import cmd_review
from code_review_agent.interactive.commands.usage_cmd import cmd_usage
from code_review_agent.interactive.commands.watch_cmd import cmd_watch
from code_review_agent.interactive.completers import build_static_completer
from code_review_agent.interactive.session import SessionState

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)
console = Console()

# Command handler type: (args, session) -> None
CommandHandler = Callable[[list[str], SessionState], None]

# Map command names to their handlers.
_COMMANDS: dict[str, CommandHandler] = {
    "status": cmd_status,
    "diff": cmd_diff,
    "log": cmd_log,
    "show": cmd_show,
    "branch": cmd_branch,
    "add": cmd_add,
    "unstage": cmd_unstage,
    "commit": cmd_commit,
    "stash": cmd_stash,
    "cd": cmd_cd,
    "review": cmd_review,
    "findings": cmd_findings,
    "pr": cmd_pr,
    "repo": cmd_repo,
    "watch": cmd_watch,
    "history": cmd_history,
    "config": cmd_config,
    "provider": cmd_provider,
    "pv": cmd_provider,
    "usage": cmd_usage,
    "help": cmd_help,
    "agents": cmd_agents,
    "version": cmd_version,
    "clear": cmd_clear,
}

_VERSION = __import__("code_review_agent").__version__


def _get_toolbar(session: SessionState) -> HTML:
    """Build the bottom toolbar separated by a horizontal line."""
    branch = ""
    try:
        from code_review_agent.interactive import git_ops

        branch = git_ops.current_branch()
    except Exception:
        branch = "?"
    tokens = _format_token_count(session.total_tokens_used)

    # Build repo label: "owner/repo:local" or "owner/repo:remote"
    if session.active_repo:
        source = session.active_repo_source or "local"
        repo_label = f"{session.active_repo}:{source}"
    else:
        # Derive from local git remote without storing
        try:
            from code_review_agent.interactive.git_ops import (
                parse_github_owner_repo,
                remote_url,
            )

            url = remote_url()
            if url:
                parsed = parse_github_owner_repo(url)
                repo_label = f"{parsed[0]}/{parsed[1]}:local" if parsed else ""
            else:
                repo_label = ""
        except Exception:
            repo_label = ""

    # DB-backed usage stats for the configured window
    usage_label = ""
    try:
        from code_review_agent.progress import USAGE_WINDOW_HOURS, USAGE_WINDOW_LABELS
        from code_review_agent.storage import ReviewStorage

        settings = session.effective_settings
        window = settings.usage_window
        if window != "session":
            storage = ReviewStorage(settings.history_db_path)
            hours = USAGE_WINDOW_HOURS.get(window)
            stats = storage.get_usage_stats(hours=hours)
            window_label = USAGE_WINDOW_LABELS.get(window, window)
            tok = stats["total_tokens"]
            tok_str = f"{tok / 1000:.1f}k" if tok >= 1000 else str(tok)
            cost = stats["estimated_cost_usd"]
            cost_str = f"${cost:.4f}" if 0 < cost < 0.01 else f"${cost:.2f}" if cost else "$0"
            usage_label = f" | <b>{window_label}:</b> {tok_str} tokens, {cost_str}"
    except Exception:  # noqa: S110
        pass

    repo_part = f" | <b>Repo:</b> {repo_label}" if repo_label else ""
    term_width = shutil.get_terminal_size((80, 24)).columns
    separator = "\u2500" * term_width

    # Live review status line (only shown when a review exists)
    review_line = ""
    try:
        bg = session.background_review
        if bg is not None:
            raw = bg.format_status_line()
            escaped = (
                raw.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            suffix = " -- press Enter" if bg.is_done else ""
            review_line = f" {escaped}{suffix}\n"
    except Exception:  # noqa: S110
        pass

    return HTML(
        f'<style fg="ansigray">{separator}</style>\n'
        f"{review_line}"
        f" <b>Branch:</b> {branch}"
        f"{repo_part}"
        f" | <b>Reviews:</b> {session.reviews_completed}"
        f" | <b>Tokens:</b> {tokens}"
        f" | <b>Tier:</b> {session.display_tier}"
        f"{usage_label}"
        f"{'  !' if session.has_cost_warning else ''}"
    )


_REPL_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse",
    }
)

# Completions rendered as multi-column above/below the prompt depending on space
from prompt_toolkit.shortcuts import CompleteStyle as _CompleteStyle  # noqa: E402

_COMPLETE_STYLE = _CompleteStyle.COLUMN


def _format_token_count(count: int) -> str:
    """Format token count with human-readable suffix (k, m, b)."""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}b"
    if count >= 999_950:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _print_welcome() -> None:
    """Print a friendly welcome banner with getting-started guidance."""
    term_width = shutil.get_terminal_size((80, 24)).columns
    rule = "\u2500" * min(term_width, 72)

    console.print()
    console.print(f"  [bold]Code Review AI[/bold] v{_VERSION}")
    console.print(
        "  Multi-agent code review powered by LLM.\n"
        "  Analyzes [bold]security[/bold], [bold]performance[/bold],"
        " [bold]style[/bold], and [bold]test coverage[/bold]."
    )
    console.print()
    console.print(f"  [dim]{rule}[/dim]")
    console.print("  [bold]Quick start:[/bold]")
    console.print()
    console.print(
        "    [bold cyan]review[/bold cyan]                    Review working tree changes"
    )
    console.print(
        "    [bold cyan]review --diff <file>[/bold cyan]      Review a local diff or patch file"
    )
    console.print(
        "    [bold cyan]repo select <owner/repo>[/bold cyan]  Set the active GitHub repository"
    )
    console.print("    [bold cyan]pr list[/bold cyan]                   List open pull requests")
    console.print(
        "    [bold cyan]pr review <number>[/bold cyan]        "
        "Review a PR (fetches diff from GitHub)"
    )
    console.print()
    console.print("  [bold]Tools:[/bold]")
    console.print()
    console.print(
        "    [bold cyan]findings[/bold cyan]                  Browse, triage, and post findings"
    )
    console.print(
        "    [bold cyan]config edit[/bold cyan]               Open the interactive config editor"
    )
    console.print(
        "    [bold cyan]help[/bold cyan]                      Show all available commands"
    )
    console.print()
    console.print(f"  [dim]{rule}[/dim]")
    console.print(
        "  [dim]Tab[/dim] autocomplete  "
        "[dim]Ctrl+A[/dim] agents  "
        "[dim]Ctrl+P[/dim] provider  "
        "[dim]Ctrl+O[/dim] repo  "
        "[dim]Ctrl+L[/dim] graph  "
        "[dim]Ctrl+D[/dim] exit"
    )
    console.print()


def _run_startup_connection_test(session: SessionState) -> None:
    """Run an LLM connection test on startup if enabled."""
    settings = session.effective_settings
    if not settings.test_connection_on_start:
        return
    # Skip if no real API key is available for the active provider
    try:
        key = settings.resolved_api_key.get_secret_value()
        if key == "__placeholder__":
            return
    except (ValueError, AttributeError):
        return
    run_connection_test(session)


_LLM_CONFIG_KEYS = ("llm_provider", "llm_model", "llm_base_url")


def run_connection_test(
    session: SessionState,
    *,
    previous_llm_config: dict[str, str] | None = None,
) -> bool:
    """Test the LLM connection, handle failures, and persist changes.

    On failure:
    - Marks model/provider as "(not working)" in the DB
    - Reverts LLM config to ``previous_llm_config`` (if provided)
    - Re-tests with reverted config
    All config changes are auto-saved to DB immediately.
    """
    from code_review_agent.connection_test import FailureKind, test_llm_connection

    settings = session.effective_settings
    provider = settings.llm_provider
    model = settings.llm_model

    console.print("  [dim]Testing LLM connection...[/dim]", end="")
    is_ok, message, failure_kind = test_llm_connection(settings)

    if is_ok:
        console.print(f"\r  [green]OK[/green] LLM connection: {message}        ")
        _set_health_mark(session, "model", model, is_healthy=True)
        _set_health_mark(session, "provider", provider, is_healthy=True)
        return True

    console.print(f"\r  [red]!![/red] LLM connection failed: {message}        ")

    if failure_kind == FailureKind.MODEL:
        _set_health_mark(session, "model", model, is_healthy=False)
        console.print(f"  [red]Model '{model}' marked as (not working).[/red]")
    elif failure_kind == FailureKind.PROVIDER:
        _set_health_mark(session, "provider", provider, is_healthy=False)
        console.print(f"  [red]Provider '{provider}' marked as (not working).[/red]")
    else:
        console.print(
            "  [dim]Check your provider, model, API key, and base URL with "
            "'config edit' or 'provider list'[/dim]"
        )

    # Revert to previous LLM config if available
    if previous_llm_config:
        _revert_llm_config(session, previous_llm_config)
    else:
        # Offer removal
        if failure_kind == FailureKind.MODEL:
            _offer_model_removal(session, provider, model)
        elif failure_kind == FailureKind.PROVIDER:
            _offer_provider_removal(session, provider)

    return False


def _revert_llm_config(
    session: SessionState,
    previous: dict[str, str],
) -> None:
    """Revert LLM config keys to previous values and re-test."""
    prev_provider = previous.get("llm_provider", "")
    prev_model = previous.get("llm_model", "")
    console.print(
        f"  [yellow]Reverting to previous config: "
        f"provider={prev_provider}, model={prev_model}[/yellow]"
    )
    for key in _LLM_CONFIG_KEYS:
        if key in previous:
            session.config_overrides[key] = previous[key]
        else:
            session.config_overrides.pop(key, None)
    session.invalidate_settings_cache()
    _auto_save_config(session)

    # Re-test with reverted config
    from code_review_agent.connection_test import test_llm_connection

    settings = session.effective_settings
    console.print("  [dim]Re-testing previous config...[/dim]", end="")
    is_ok, message, _ = test_llm_connection(settings)
    if is_ok:
        console.print(f"\r  [green]OK[/green] Previous config: {message}        ")
    else:
        console.print(f"\r  [red]!![/red] Previous config also failed: {message}        ")


def _set_health_mark(session: SessionState, kind: str, name: str, *, is_healthy: bool) -> None:
    """Set or clear a health mark in the DB."""
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.effective_settings.history_db_path)
        if is_healthy:
            storage.delete_config(f"health:{kind}:{name}")
        else:
            storage.save_config(f"health:{kind}:{name}", "not_working")
    except Exception:
        logger.debug("failed to update health mark", kind=kind, name=name, exc_info=True)


def get_health_status(session: SessionState) -> dict[str, set[str]]:
    """Load all health marks from DB. Returns {kind: {name, ...}}."""
    result: dict[str, set[str]] = {"model": set(), "provider": set()}
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.effective_settings.history_db_path)
        overrides = storage.load_all_config_overrides()
        for key, value in overrides.items():
            if key.startswith("health:") and value == "not_working":
                parts = key.split(":", 2)
                if len(parts) == 3:
                    kind, name = parts[1], parts[2]
                    if kind in result:
                        result[kind].add(name)
    except Exception:
        logger.debug("failed to load health status", exc_info=True)
    return result


def _auto_save_config(session: SessionState) -> None:
    """Persist all config overrides to DB immediately."""
    from code_review_agent.interactive.commands.config_cmd import save_config_to_db

    save_config_to_db(session)


def _offer_model_removal(session: SessionState, provider: str, model: str) -> None:
    """Ask user whether to remove the broken model from the provider."""
    from prompt_toolkit import prompt as pt_prompt

    try:
        answer = (
            pt_prompt(f"  Remove '{model}' from provider '{provider}'? (y/N): ").strip().lower()
        )
    except (KeyboardInterrupt, EOFError):
        return

    if answer not in ("y", "yes"):
        return

    from code_review_agent.interactive.commands.provider_cmd import (
        _load_user_registry,
        _save_user_registry,
    )
    from code_review_agent.providers import (
        get_provider,
        reload_registry,
    )

    # Remove from user registry if present
    user_providers = _load_user_registry()
    if provider in user_providers and isinstance(user_providers[provider], dict):
        models = user_providers[provider].get("models", [])
        updated = [m for m in models if m.get("id") != model]
        if len(updated) < len(models):
            user_providers[provider]["models"] = updated
            _save_user_registry(user_providers)
            reload_registry()
            console.print(f"  [green]Model '{model}' removed.[/green]")

    # Switch to provider's default model
    try:
        provider_info = get_provider(provider)
        new_model = provider_info.default_model
        if new_model != model:
            session.config_overrides["llm_model"] = new_model
            session.invalidate_settings_cache()
            _auto_save_config(session)
            console.print(f"  [green]Switched to model: {new_model} (saved)[/green]")
    except KeyError:
        pass

    _set_health_mark(session, "model", model, is_healthy=True)


def _offer_provider_removal(session: SessionState, provider: str) -> None:
    """Ask user whether to remove the broken provider."""
    from prompt_toolkit import prompt as pt_prompt

    from code_review_agent.config import BUILTIN_PROVIDERS

    is_builtin = provider in BUILTIN_PROVIDERS
    action = "Switch away from" if is_builtin else "Remove"
    try:
        answer = pt_prompt(f"  {action} provider '{provider}'? (y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    if answer not in ("y", "yes"):
        return

    if not is_builtin:
        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
            _save_user_registry,
        )
        from code_review_agent.providers import reload_registry

        user_providers = _load_user_registry()
        if provider in user_providers:
            del user_providers[provider]
            _save_user_registry(user_providers)
            reload_registry()
            console.print(f"  [green]Provider '{provider}' removed.[/green]")

    # Switch to first available healthy provider
    from code_review_agent.providers import PROVIDER_REGISTRY

    health = get_health_status(session)
    for p in sorted(PROVIDER_REGISTRY.keys()):
        if p != provider and p not in health.get("provider", set()):
            from code_review_agent.providers import get_provider

            provider_info = get_provider(p)
            session.config_overrides["llm_provider"] = p
            session.config_overrides["llm_base_url"] = provider_info.base_url
            session.config_overrides["llm_model"] = provider_info.default_model
            session.invalidate_settings_cache()
            _auto_save_config(session)
            console.print(
                f"  [green]Switched to provider: {p}, "
                f"model: {provider_info.default_model} (saved)[/green]"
            )
            break

    _set_health_mark(session, "provider", provider, is_healthy=True)


_AGENT_SELECT_SENTINEL = "\x00__agent_select__"
_PROVIDER_SELECT_SENTINEL = "\x00__provider_select__"
_REPO_SELECT_SENTINEL = "\x00__repo_select__"
_GRAPH_SENTINEL = "\x00__graph__"
_REVIEW_DONE_SENTINEL = "\x00__review_done__"

_HOTKEY_SENTINELS = frozenset(
    {
        _AGENT_SELECT_SENTINEL,
        _PROVIDER_SELECT_SENTINEL,
        _REPO_SELECT_SENTINEL,
        _GRAPH_SENTINEL,
        _REVIEW_DONE_SENTINEL,
    }
)


def _build_repl_keybindings() -> KeyBindings:
    """Build key bindings for the REPL prompt.

    Ctrl+A: agents, Ctrl+P: provider, Ctrl+O: repo, Ctrl+L: git graph.
    All exit the prompt with a sentinel so the main loop can launch the
    action outside the event loop.
    """
    kb = KeyBindings()

    @kb.add("c-a")
    def on_ctrl_a(event: KeyPressEvent) -> None:
        event.app.exit(result=_AGENT_SELECT_SENTINEL)

    @kb.add("c-p")
    def on_ctrl_p(event: KeyPressEvent) -> None:
        event.app.exit(result=_PROVIDER_SELECT_SENTINEL)

    @kb.add("c-o")
    def on_ctrl_o(event: KeyPressEvent) -> None:
        event.app.exit(result=_REPO_SELECT_SENTINEL)

    @kb.add("c-l")
    def on_ctrl_l(event: KeyPressEvent) -> None:
        event.app.exit(result=_GRAPH_SENTINEL)

    return kb


_REPO_KEY = "active_repo"
_REPO_SOURCE_KEY = "active_repo_source"


def _save_active_repo(session: SessionState) -> None:
    """Persist the active repo to the database."""
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.effective_settings.history_db_path)
        if session.active_repo:
            storage.save_config(_REPO_KEY, session.active_repo)
            storage.save_config(_REPO_SOURCE_KEY, session.active_repo_source or "local")
        else:
            storage.delete_config(_REPO_KEY)
            storage.delete_config(_REPO_SOURCE_KEY)
    except Exception:
        logger.debug("failed to save active repo", exc_info=True)


def _load_active_repo(session: SessionState) -> None:
    """Restore the active repo from the database."""
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.effective_settings.history_db_path)
        repo = storage.load_config(_REPO_KEY)
        if repo:
            session.active_repo = repo
            session.active_repo_source = storage.load_config(_REPO_SOURCE_KEY) or "local"
            logger.debug("restored active repo", repo=repo)
    except Exception:
        logger.debug("failed to load active repo", exc_info=True)


def _confirm_exit(session: SessionState) -> bool:
    """Prompt for exit confirmation, optionally saving config.

    Returns True if the user confirms exit, False to stay.
    """
    has_overrides = bool(session.config_overrides)
    has_repo = session.active_repo is not None
    has_state = has_overrides or has_repo

    if not has_state:
        # Nothing to save -- simple confirmation
        console.print()
        console.print("[bold]Exit? (y/n)[/bold]")
        try:
            answer = input("> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return True
        return answer in ("y", "yes", "")

    # Has session state worth saving -- offer save option
    console.print()
    console.print("[bold]Unsaved session state:[/bold]")
    if has_repo:
        console.print(f"  [dim]repo[/dim] = {session.active_repo}")
    for key, value in session.config_overrides.items():
        console.print(f"  [dim]{key}[/dim] = {value}")
    console.print()
    console.print("  [bold green][1][/bold green] Save and exit  [dim](default)[/dim]")
    console.print("  [bold yellow][2][/bold yellow] Exit without saving")
    console.print("  [bold][3][/bold] Cancel")
    try:
        answer = input("> ").strip()
    except (KeyboardInterrupt, EOFError):
        return True

    if answer in ("1", ""):
        try:
            from code_review_agent.interactive.commands.config_cmd import (
                save_config_to_db,
            )

            saved = save_config_to_db(session)
            _save_active_repo(session)
            if saved:
                console.print(f"  [green]{saved} setting(s) saved.[/green]")
            else:
                console.print("  [red]Failed to save settings.[/red]")
        except Exception:
            console.print("  [red]Failed to save settings.[/red]")
        return True
    return answer == "2"


def _process_completed_review(
    session: SessionState,
    prompt_session: PromptSession[str] | None = None,
) -> None:
    """Check for a finished background review and process its result.

    Called at the top of every REPL loop iteration. When a review is
    done, prints the report immediately and prompts the user to run
    queued commands.
    """
    bg = session.background_review
    if bg is None or not bg.is_done:
        return

    from code_review_agent.interactive.commands.review_cmd import _auto_save_report
    from code_review_agent.models import OutputFormat
    from code_review_agent.report import render_report_json, render_report_rich

    report, error = bg.collect_result()
    output_format = bg.output_format
    session.background_review = None

    if error is not None:
        print_error(
            UserError(
                detail=f"Review failed: {error}",
                reason="The background review encountered an error.",
                solution=(
                    "Check your LLM provider connection with "
                    "'config validate'. Retry with 'review'."
                ),
            ),
            console=console,
        )
    elif report is not None:
        session.reviews_completed += 1
        session.last_review_report = report
        session.usage_history.record_review(report)
        if report.token_usage is not None:
            session.total_tokens_used += report.token_usage.total_tokens

        session.last_review_id = _auto_save_report(report, session)

        if output_format == OutputFormat.JSON:
            console.print(render_report_json(report))
        else:
            render_report_rich(report)
    else:
        console.print("[bold]Review was cancelled.[/bold]")

    # Ask user about queued commands
    if session.command_queue:
        queued = list(session.command_queue)
        console.print()
        console.print(f"[bold]{len(queued)} queued command(s):[/bold]")
        for cmd in queued:
            console.print(f"  [bold cyan]>[/bold cyan] {cmd}")
        console.print()
        console.print("  [bold green][1][/bold green] Run queued commands  [dim](default)[/dim]")
        console.print("  [bold yellow][2][/bold yellow] Discard queue")
        try:
            answer = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            answer = "2"

        if answer in ("1", ""):
            for cmd in queued:
                console.print(f"[dim]Running: {cmd}[/dim]")
                try:
                    _dispatch(cmd, session)
                except Exception as exc:
                    err = classify_exception(
                        exc,
                        context=f"Queued command '{cmd}'",
                    )
                    print_error(err, console=console)
        else:
            console.print("[dim]Queue discarded.[/dim]")
        session.command_queue.clear()


def _handle_review_interrupt(session: SessionState) -> None:
    """Handle Ctrl+C when a background review is running."""
    from code_review_agent.cancel_prompt import CancelChoice, prompt_cancel_choice

    bg = session.background_review
    if bg is None or bg.is_done:
        return

    choice = prompt_cancel_choice(console)

    if choice == CancelChoice.ABORT:
        bg.orchestrator.abort()
        session.background_review = None
        session.command_queue.clear()
        console.print("[bold]Review aborted.[/bold]")
    elif choice == CancelChoice.FINISH_PARTIAL:
        bg.orchestrator.cancel()
        bg.mark_finishing()
        console.print("[bold]Finishing with partial results...[/bold]")


def run_repl(settings: Settings) -> None:
    """Launch the interactive REPL loop."""
    # Suppress background thread log output so it doesn't corrupt the TUI
    from code_review_agent.main import _StderrProxy

    _StderrProxy.suppress_background = True

    try:
        _run_repl_loop(settings)
    finally:
        _StderrProxy.suppress_background = False


def _run_repl_loop(settings: Settings) -> None:
    """Inner REPL loop, separated so suppress_background is always reset."""
    session = SessionState(settings=settings)

    # Load persisted config overrides from database (DB is the source of truth)
    # Skip: health marks, API keys, and values matching the base settings default
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(settings.history_db_path)
        persisted = storage.load_all_config_overrides()
        if persisted:
            for k, v in persisted.items():
                if k.startswith("health:") or k.endswith("_api_key"):
                    continue
                # Skip if the value matches the current base setting
                base_val = getattr(settings, k, None)
                if base_val is not None and str(base_val) == v:
                    # Redundant — clean it from DB
                    storage.delete_config(k)
                    continue
                session.config_overrides[k] = v
            session.invalidate_settings_cache()
            logger.debug("loaded persisted config overrides", count=len(persisted))
    except Exception:
        logger.debug("failed to load persisted config", exc_info=True)

    _load_active_repo(session)

    completer = build_static_completer()

    prompt_str = settings.interactive_prompt
    history_path = os.path.expanduser(settings.interactive_history_file)

    repl_kb = _build_repl_keybindings()

    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        complete_while_typing=True,
        complete_style=_COMPLETE_STYLE,
        bottom_toolbar=lambda: _get_toolbar(session),
        style=_REPL_STYLE,
        refresh_interval=0.25,
        vi_mode=settings.interactive_vi_mode,
        key_bindings=repl_kb,
        reserve_space_for_menu=6,
    )

    # Store prompt_session on session so background reviews can interrupt it
    session._prompt_session = prompt_session  # type: ignore[attr-defined]

    # Always show the provider setup panel first
    from code_review_agent.interactive.startup_keys import run_startup_key_setup

    run_startup_key_setup(session)

    # Rebuild settings from env after key setup (picks up newly added keys)
    try:
        from code_review_agent.config import Settings

        session.settings = Settings()
        session.invalidate_settings_cache()
    except Exception as exc:
        logger.debug("failed to rebuild settings after key setup", exc_info=True)
        print_error(classify_exception(exc, context="Loading configuration"), console=console)

    _print_welcome()
    _run_startup_connection_test(session)

    while True:
        try:
            _process_completed_review(session, prompt_session)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            err = classify_exception(
                exc,
                context="Processing review result",
            )
            print_error(err, console=console)
            logger.debug("_process_completed_review failed", exc_info=True)

        try:
            text = prompt_session.prompt(prompt_str)
        except KeyboardInterrupt:
            bg = session.background_review
            if bg is not None and bg.is_running:
                _handle_review_interrupt(session)
            continue
        except EOFError:
            bg = session.background_review
            if bg is not None and bg.is_running:
                console.print("[dim]Review in progress. Use Ctrl+C for cancel options.[/dim]")
                continue
            if _confirm_exit(session):
                console.print("[dim]Goodbye.[/dim]")
                break
            continue

        if text in _HOTKEY_SENTINELS:
            if text == _AGENT_SELECT_SENTINEL:
                from code_review_agent.interactive.commands.agent_selector import (
                    run_agent_selector,
                )

                run_agent_selector(session)
            elif text == _PROVIDER_SELECT_SENTINEL:
                from code_review_agent.interactive.commands.provider_selector import (
                    run_provider_selector,
                )

                run_provider_selector(session)
            elif text == _REPO_SELECT_SENTINEL:
                from code_review_agent.interactive.commands.repo_cmd import (
                    _repo_select,
                )

                _repo_select([], session)
            elif text == _GRAPH_SENTINEL:
                from code_review_agent.interactive.commands._helpers import (
                    warn_if_remote_repo,
                )

                warn_if_remote_repo(session)
                from code_review_agent.interactive.commands.graph_nav import (
                    run_graph_app,
                )

                run_graph_app()
            # _REVIEW_DONE_SENTINEL: just loop back to _process_completed_review
            continue

        text = text.strip()
        if not text:
            continue

        try:
            _dispatch(text, session)
        except EOFError:
            if _confirm_exit(session):
                console.print("[dim]Goodbye.[/dim]")
                break


# Commands safe to run during a background review (read-only, no conflict)
_IMMEDIATE_COMMANDS: set[str] = {
    "help",
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "cd",
    "config",
    "provider",
    "pv",
    "agents",
    "version",
    "clear",
    "usage",
}


def _dispatch(text: str, session: SessionState) -> None:
    """Parse and dispatch a single command."""
    # Shell escape -- always immediate
    if text.startswith("!"):
        shell_cmd = text[1:].strip()
        cmd_shell(shell_cmd.split() if shell_cmd else [], session)
        return

    # Exit aliases
    if text in ("exit", "quit", "q"):
        bg = session.background_review
        if bg is not None and bg.is_running:
            console.print("[dim]Review in progress. Use Ctrl+C for cancel options.[/dim]")
            return
        raise EOFError

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        print_error(
            UserError(
                detail=f"Parse error: {exc}",
                reason="Invalid command syntax (unmatched quotes or special characters).",
                solution="Check for unmatched quotes. Use 'help' to see command syntax.",
            ),
            console=console,
        )
        return

    if not tokens:
        return

    command = tokens[0].lower()
    args = tokens[1:]

    # Queue non-immediate commands during a background review
    bg = session.background_review
    if bg is not None and bg.is_running and command not in _IMMEDIATE_COMMANDS:
        session.command_queue.append(text)
        console.print(f"[dim]Queued: {text} (will run after review)[/dim]")
        return

    handler = _COMMANDS.get(command)
    if handler is None:
        print_error(
            UserError(
                detail=f"Unknown command: {command}",
                solution="Type 'help' for available commands.",
            ),
            console=console,
        )
        return

    try:
        handler(args, session)
    except Exception as exc:
        print_error(classify_exception(exc, context=command), console=console)
        logger.debug("command failed", command=command, error=str(exc), exc_info=True)
