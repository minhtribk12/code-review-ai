"""Shared config category definitions for config display and editor.

Single source of truth for config key grouping. Used by both
``config_cmd.py`` (show/get/set) and ``config_edit.py`` (interactive editor).
"""

from __future__ import annotations

# Ordered list of (category_name, [keys]) for display and editing.
CONFIG_CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "LLM",
        [
            "llm_provider",
            "llm_model",
            "llm_base_url",
            "llm_temperature",
            "llm_top_p",
            "llm_max_tokens",
            "llm_api_key",
            "request_timeout_seconds",
            "test_connection_on_start",
        ],
    ),
    (
        "Token Budget",
        [
            "token_tier",
            "max_prompt_tokens",
            "max_tokens_per_review",
            "llm_input_price_per_m",
            "llm_output_price_per_m",
            "rate_limit_rpm",
        ],
    ),
    (
        "Review",
        [
            "dedup_strategy",
            "max_review_seconds",
            "max_concurrent_agents",
            "default_agents",
        ],
    ),
    (
        "Iterative Review",
        [
            "max_deepening_rounds",
            "is_validation_enabled",
            "max_validation_rounds",
        ],
    ),
    (
        "GitHub",
        [
            "github_token",
            "max_pr_files",
            "github_rate_limit_warn_threshold",
            "pr_stale_days",
        ],
    ),
    (
        "Custom Agents",
        [
            "custom_agents_dir",
        ],
    ),
    (
        "History & Usage",
        [
            "history_db_path",
            "auto_save_history",
            "usage_window",
        ],
    ),
    (
        "Interactive",
        [
            "interactive_history_file",
            "interactive_prompt",
            "interactive_vi_mode",
            "interactive_autocomplete_cache_ttl",
            "watch_debounce_seconds",
        ],
    ),
    (
        "Logging",
        [
            "log_level",
        ],
    ),
]

# Dict form for quick category lookup by name.
CONFIG_CATEGORIES_DICT: dict[str, list[str]] = dict(CONFIG_CATEGORIES)
