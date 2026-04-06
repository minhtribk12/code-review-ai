# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.14] - 2026-04-06

### Added

- **News intelligence pipeline**: multi-source fetching (HN Algolia + Reddit JSON + DuckDuckGo Web), composite scoring (token-overlap relevance + recency + engagement), trigram/token Jaccard deduplication with cross-source convergence detection, LLM synthesis on pre-scored data.
- **Full-screen article reader**: fetch content on demand, rich text rendering (headings, code blocks, blockquotes), scroll (j/k/d/u/g/G), progress bar, page indicator, reading time, save/resume position, next/prev article navigation.
- **Enhanced news navigator**: multi-select (Space), batch delete (d/D), batch mark-read (R/A), sort cycling (S: score/date/comments), fuzzy search (/), domain filter (F), mark unread (u).
- **News commands**: `news <topic>` multi-source brief, `news 30days <topic>` deep research, `news add/remove` custom feeds (persisted to config.yaml), `news refresh` (clear cache), `news cleanup` (delete old articles), `news stats` (weekly summary + cache size).
- **Source diversity**: interleave_sources() guarantees min 3 items from each source in results.
- **Depth profiles**: quick (15s), default (30s), deep (60s) per-source timeouts.
- **24h cache**: skip pipeline when fresh articles exist in SQLite.
- **Quality nudge**: source status (ok/failed) shown in navigator header.
- **Reddit JSON content**: fetch selftext + threaded comments via .json API (fixes garbled reader).
- **HN threaded comments**: fetch + render nested discussion with indentation via Algolia items API.
- **News tab in Textual TUI**: shows unread count, recent articles, and usage hints.
- **Content validation**: is_valid_content() rejects garbled binary, re-fetches on invalid cache.
- **HTML entity decoding**: html.unescape() on all Reddit/HN text fields.

### Fixed

- "Failed to save settings" on exit was a false alarm (0 overrides treated as failure).
- `scroll-up`/`scroll-down` key bindings now use `Keys.ScrollUp`/`Keys.ScrollDown`.
- `news 30days` without topic shows usage help instead of searching literal "30days".

## [0.1.13] - 2026-04-03

### Added

- **Terminal news reader**: integrated RSS/Atom reader with 40+ built-in domains across 10 categories (tech, AI, LLM, security, languages, DevOps, startups, open source, data, frontend, research).
- **Article model**: frozen Pydantic model with priority (trending/recent/saved/read), age display, and score formatting.
- **Article storage**: SQLite-backed store with upsert, domain filtering, read/saved tracking, content caching, per-domain stats, and 30-day auto-cleanup.
- **RSS adapter**: generic feedparser-based adapter handling RSS 2.0 and Atom feeds with tag extraction and date parsing.
- **Content fetcher**: fetch full article HTML on demand, convert to rich terminal text preserving headings, code blocks, lists, blockquotes, bold/italic, and link footnotes.
- **Domain registry**: 40+ domains with meta-domains (e.g., `news tech` fetches hackernews + lobsters + techcrunch).
- **REPL commands**: `news <domain>`, `news list`, `news stats`, `read-news`.

## [0.1.12] - 2026-04-03

### Added

- **Render cache**: dirty generation counter on config editor and findings navigator skips redundant rebuilds.
- **Mouse scroll**: scroll-up/scroll-down support in config editor and findings navigator.
- **Filter suggestion cache**: DB queries cached per field, avoiding repeated queries per keystroke.
- **Memoize decorators**: `memoize_with_ttl` and `memoize_with_lru` with thread-safe implementations.
- **Graceful shutdown**: phased cleanup (terminal reset, save state, flush usage) via atexit.
- **FPS tracker**: per-frame render duration tracking with adaptive refresh rate.
- **Learning from dismissed findings**: triage history fed back into agent prompts as suppression patterns.
- **Diff-aware context enrichment**: parse diffs to extract enclosing function/class scope, imports, and surrounding context for agent prompts.
- **Auto-fix**: parse suggestion code blocks and apply patches to source files with backup/undo.
- **Linter integration**: built-in parsers for ruff, eslint, mypy (JSON), plus generic line-based parser.
- **Streaming agent execution**: `_run_agents_streaming()` generator yields results as each agent completes.
- **Plugin system**: declarative plugins with `plugin.yaml` manifest contributing hooks, commands, and agents.
- **Permission system**: ask/auto/deny modes for PR comment posting with denial tracking.
- **Review comparison**: compare two review runs to show resolved/new/persistent findings.
- **Fuzzy search**: substring matching across all finding fields with weighted scoring.
- **Delta-style diff rendering**: syntax-aware code snippets with green/red diff coloring and line numbers.
- **Watch mode incremental review**: SHA-256 content hashing for change detection, re-review only changed hunks.
- **Quick git actions**: contextual git status/diff/blame/log from findings navigator.
- **Bootstrap state isolation**: frozen `BootstrapState` with startup profiling and timed checkpoints.

## [0.1.11] - 2026-03-23

### Fixed

- Test fixtures now use isolated `ConfigStore` backed by temp directories, preventing tests from polluting the real `~/.cra/config.yaml` with stale values (e.g., `max_deepening_rounds` being overwritten to `3`).

## [0.1.10] - 2026-03-21

### Fixed

- Resolve nvidia base URL in config display so users see the actual URL instead of "not set".

### Changed

- Recommend `pipx` over `pip` for installation in README.

## [0.1.9] - 2026-03-21

### Added

- Demo video in README.
- Community files: CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, issue/PR templates.
- GitHub token setup instructions in quick start.

### Changed

- Updated interactive guide to match current TUI features.
- Clarified cost estimation in usage section.
- Improved README: API key setup guide, TUI explanation, token tier docs.

## [0.1.8] - 2026-03-20

### Added

- YAML-based config persistence (`~/.cra/config.yaml`), replacing SQLite for settings storage.
- API key manager panel for interactive key setup.
- `config clean` command with confirmation panel.

### Fixed

- API key inconsistency across storage layers.

## [0.1.7] - 2026-03-20

### Fixed

- API key resolution inconsistency across storage layers.

## [0.1.6] - 2026-03-19

### Added

- `factory-reset` command to clear all config, health marks, and review history while preserving API keys.
- Config reset now preserves API keys.

### Fixed

- Text formatting and production display issues.

## [0.1.5] - 2026-03-19

### Added

- Field selector for editing all provider and model properties.

### Fixed

- Allow editing built-in providers via user overrides.
- Show error on settings rebuild failure during startup.
- Rebuild settings after key setup to pick up new keys.

## [0.1.4] - 2026-03-18

### Fixed

- Startup flow: always show provider panel first, block continue without API key.

## [0.1.2] - 2026-03-18

### Added

- Key setup panel during startup.
- Provider registry with connection testing and interactive browser.

### Changed

- Removed openrouter/auto default, require explicit provider selection.

## [0.1.1] - 2026-03-18

### Added

- Multi-agent code review CLI scaffold.
- Single-agent MVP with comprehensive tests.

## [0.1.0] - 2026-03-18

### Added

- Initial release: project scaffold and core architecture.
