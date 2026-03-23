# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
