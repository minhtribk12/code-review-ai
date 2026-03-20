# Code Review AI

[![PyPI version](https://img.shields.io/pypi/v/code-review-ai)](https://pypi.org/project/code-review-ai/)
[![Downloads](https://img.shields.io/pypi/dm/code-review-ai)](https://pypi.org/project/code-review-ai/)
[![CI](https://github.com/minhtribk12/code-review-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/minhtribk12/code-review-ai/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/code-review-ai)](https://pypi.org/project/code-review-ai/)
[![License](https://img.shields.io/github/license/minhtribk12/code-review-ai)](LICENSE)

**AI-powered code review that runs locally, works with any OpenAI-compatible LLM, and costs nothing with free-tier APIs.**

Multiple specialized agents (security, performance, style, test coverage) review your code in parallel, deduplicate findings across agents, and synthesize everything into one structured report -- all from a rich interactive TUI or a single CLI command.

```bash
pip install code-review-ai && cra interactive
```

https://github.com/user-attachments/assets/ab246961-d592-4974-bc7f-070fd88034c9

### Why Code Review AI?

- **Free to run** -- works out of the box with NVIDIA and OpenRouter free-tier models
- **Multi-agent** -- 4 built-in agents catch different issue types simultaneously
- **Full TUI** -- git, PR management, provider switching, and config editing in one terminal
- **Any LLM** -- NVIDIA, OpenRouter, Ollama, vLLM, or any OpenAI-compatible endpoint
- **Extensible** -- define custom review agents in YAML, no Python needed
- **Privacy-first** -- runs locally, your code never leaves your machine (unless you choose a cloud API)

## Features

**Review pipeline:**
- 4 built-in agents (security, performance, style, test coverage) + custom YAML agents
- Parallel execution with configurable concurrency
- Iterative deepening -- multiple review rounds with convergence detection
- Validation loop -- skeptical validator filters false positives
- Cross-agent deduplication (exact, location-based, or similarity-based)
- Token budget enforcement with automatic diff truncation

**Interactive TUI:**
- Full-screen provider browser -- add, edit, switch LLM providers and models
- API key manager -- edit, sync, delete keys across secrets.env and .env
- Git commands, PR workflows, findings navigator with triage and PR posting
- Tab autocomplete, keyboard shortcuts (Ctrl+A agents, Ctrl+P provider, Ctrl+O repo)

**Input/output:**
- GitHub PR review (`--pr owner/repo#123`) or local diff (`--diff file.patch`)
- Rich terminal, JSON, and Markdown output formats
- SQLite review history with trends and export

**Operations:**
- Prompt injection defense (random delimiters, instruction anchoring)
- Cost estimation with per-model pricing
- Graceful degradation -- partial results when agents fail
- Retry with exponential backoff for transient API errors

**Extensibility:**
- Custom agents defined in YAML (no Python required)
- File pattern matching -- agents run only on relevant file types
- Provider-agnostic -- any OpenAI-compatible server, including local ones

## Quick Start

### Install

```bash
# Install from PyPI (recommended)
pipx install code-review-ai

# Or with pip
pip install code-review-ai
```

**From source** (for development):

```bash
git clone https://github.com/minhtribk12/code-review-ai.git
cd code-review-ai
make install  # requires uv
```

### Get Your Keys

You need an LLM API key and a GitHub token to get started:

**NVIDIA (recommended)** -- powers the default model, [Nemotron 3 Super 120B](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b), a 120B MoE model with only 12B active parameters and a 1M token context window:

1. Go to [build.nvidia.com](https://build.nvidia.com)
2. Sign in with your NVIDIA account (or create one free)
3. Click any model, then **"Get API Key"** in the top right
4. Copy the key (starts with `nvapi-`)

**OpenRouter** -- access to 100+ models from multiple providers, many free:

1. Go to [openrouter.ai/keys](https://openrouter.ai/keys)
2. Sign in with Google/GitHub
3. Click **"Create Key"**
4. Copy the key (starts with `sk-or-`)

**GitHub Token** -- required for PR commands (`pr list`, `pr review`, `pr create`, etc.):

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **"Generate new token"** (classic) with `repo` scope
3. Copy the token and set it in your shell:

```bash
export GITHUB_TOKEN=ghp_your-token-here
```

Or add `GITHUB_TOKEN=ghp_your-token-here` to your `.env` file (see below).

### Configure

**Option A: Interactive setup (easiest)** -- on first launch, a provider setup panel appears automatically. Just paste your API key:

```
 LLM Provider Setup

 > nvidia (no key)       https://integrate.api.nvidia.com/v1
   openrouter (no key)   https://openrouter.ai/api/v1

  Up/Down navigate, Enter input key, c continue, q quit
```

Keys are saved to `~/.cra/secrets.env` and persist across restarts.

**Option B: Manual `.env` file** -- create a `.env` file in your project directory:

```bash
vim .env
# or: nano .env, code .env, etc.
```

```env
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-your-key-here
```

The `.env` file is loaded automatically on startup. See [docs/configuration.md](docs/configuration.md) for all settings.


### Run

```bash
# Interactive TUI (recommended)
cra interactive

# One-shot CLI review
cra review --diff path/to/file.patch
cra review --pr owner/repo#123
cra review --diff file.patch --format json --quiet
```

## Interactive TUI (Recommended)

The TUI (Terminal User Interface) is a full-screen interactive mode that runs entirely in your terminal. It provides git commands, code review, PR management, provider switching, and configuration editing -- all without leaving the terminal.

```bash
cra interactive
```

```
  code-review-ai v0.1.0
  Tab autocomplete | Ctrl+A agents | Ctrl+P provider | Ctrl+O repo | Ctrl+L graph | Ctrl+D exit

cra> _
------------------------------------------------------------------------
 Branch: main | Repo: acme/app:local | Reviews: 0 | Tokens: 0 | Tier: free
```

### Git Commands

```bash
# Read
status                          # git status (branch + changed files)
diff                            # unstaged diff
diff staged                     # staged diff
diff HEAD~3                     # diff against N commits back
log                             # compact log (last 20)
show abc123                     # full commit detail with diff

# Write
branch                          # list local branches
branch switch feat/login        # switch branch
branch create feat/new          # create + switch
add src/main.py                 # stage specific file
commit -m "fix: resolve bug"    # commit staged changes
stash                           # stash / stash pop / stash list
cd ~/projects/other-repo        # change directory (Tab completes paths)
```

### Code Review

```bash
review                          # auto-detects unstaged/staged diff
review staged                   # review staged changes only
review HEAD~1                   # review last commit
review --agents security        # single agent
review --format json            # JSON output
```

### PR Commands

```bash
# Read
pr list                         # list open PRs
pr show 42                      # PR details
pr diff 42                      # PR diff with syntax highlighting
pr checks 42                    # CI/CD check status
pr review 42                    # run code review on PR

# Write
pr create --fill                # auto-fill from commits
pr merge 42 --strategy squash   # merge with pre-flight checks
pr approve 42                   # approve PR

# Workflow
pr mine                         # your open PRs
pr assigned                     # PRs where you're reviewer
pr stale --days 14              # stale PRs
pr ready                        # PRs ready to merge
pr conflicts                    # PRs with merge conflicts
pr summary --full               # dashboard overview
```

### Findings Navigator

After a review, navigate, triage, and post findings to PRs:

```bash
findings                        # navigate last review
findings 42                     # navigate saved review #42
```

Key bindings: Up/Down navigate, `f` filter, `s`/`S` sort forward/backward, `m` mark false positive,
`p` stage for PR posting, `P` submit staged to PR, `q` quit. Triage state
(false positive, ignored) is persisted to SQLite across sessions.

### Other Commands

```bash
config                          # show all settings
config edit                     # full-screen config editor (paste supported)
config set llm_temperature 0.3  # session override
config reset                    # reload from .env (preserves API keys)
config factory-reset            # full reset (clears history, keeps keys)
config clean                    # remove all tool data from ~/.cra/ (confirmation panel)
# Provider management
provider                        # full-screen provider browser (alias: pv)
provider add                    # add custom provider (wizard)
provider list                   # table view of all providers
provider models nvidia          # list models for a provider
provider remove my-custom       # remove a user-defined provider
history                         # past reviews
history trends --days 30        # aggregated stats
usage                           # session token/cost stats
watch --interval 10             # continuous monitoring
agents                          # list all agents (built-in + custom)
```

### Provider Browser

Run `provider` or `pv` to open the full-screen provider/model browser:

```
 Provider Browser  (Up/Down navigate, Enter expand, a add provider, m add model, d delete, i edit, q quit)

 > v nvidia  [built-in]  https://integrate.api.nvidia.com/v1  (5 models)
       nvidia/nemotron-3-super-120b-a12b  (Nemotron 3 Super 120B free, 1,000,000 ctx)
       nvidia/nemotron-3-nano-30b-a3b  (Nemotron 3 Nano 30B free, 1,000,000 ctx)
   > openrouter  [built-in]  https://openrouter.ai/api/v1  (6 models)
   > ollama  [custom]  http://localhost:11434/v1  (1 models)
```

Key bindings: `Enter` expand/collapse, `a` add provider, `m` add model to selected provider,
`d` delete (custom only), `i` edit any field (works on built-in too), `q` quit.

See the [Interactive Guide](docs/interactive-guide.md) for the full command reference.

## CLI Usage

For one-shot reviews and CI/CD integration, use the CLI directly:

### Review Commands

```bash
# Local diffs
cra review --diff changes.patch
cra review --diff changes.patch --agents security,performance
cra review --diff changes.patch --format json --output report.json

# GitHub PRs (requires GITHUB_TOKEN)
cra review --pr owner/repo#123
cra review --pr https://github.com/owner/repo/pull/123

# Open findings navigator after review
cra review --diff changes.patch --findings
```

### Token Tiers

Token tiers let you match the tool's behavior to your API plan. **The tool itself is completely free** -- tiers only control how much context is sent per review so you can stay within your API provider's token limits. If you use a free-tier API (like NVIDIA or OpenRouter free models), you pay nothing at all.

| Tier | Default Agents | Budget | When to Use |
|------|---------------|--------|-------------|
| `free` | security | 5k tokens | Free-tier APIs (NVIDIA, OpenRouter free models) |
| `standard` | all 4 built-in | 16k tokens | Pay-as-you-go APIs with 32k context models |
| `premium` | all 4 built-in | 48k tokens | Pay-as-you-go APIs with 128k+ context models |

The tier is auto-detected from the model's context window when possible. You can override with `config set token_tier standard` or `--agents` / `MAX_PROMPT_TOKENS` on the CLI.

### Custom Agents

Define domain-specific agents in YAML without writing Python:

```yaml
# ~/.cra/agents/django_security.yaml
name: django_security
description: "Django-specific security review"
system_prompt: |
  You are a Django security expert. Focus on:
  - CSRF token usage in views
  - SQL injection via raw() and extra()
  - Insecure deserialization with pickle
priority: 10
file_patterns:
  - "*.py"
```

```bash
# Use custom agents alongside built-in ones
cra review --diff changes.patch --agents security,django_security
```

See [docs/custom-agents.md](docs/custom-agents.md) for the full guide and the [CLI Guide](docs/cli-guide.md) for all flags and CI/CD integration.

## Architecture

```
CLI (Typer) / Interactive REPL
  |
  v
Orchestrator
  |-- Token budget enforcement (truncate oversized diffs)
  |-- Prompt injection scan
  |-- Agent dispatch (parallel, ThreadPoolExecutor)
  |     |-- [Security Agent]      \
  |     |-- [Performance Agent]    |-- built-in
  |     |-- [Style Agent]          |
  |     |-- [Test Coverage Agent] /
  |     |-- [Custom YAML Agents]  --- file_patterns filtering
  |-- Cross-agent deduplication
  |-- Iterative deepening loop (convergence-based)
  |-- Synthesis (LLM merges findings into summary + risk level)
  |-- Validation loop (skeptical validator filters false positives)
  |
  v
ReviewReport -> Rich terminal / JSON / Markdown
            -> SQLite history storage
            -> Findings navigator (interactive triage + PR posting)
```

See [docs/architecture.md](docs/architecture.md) for full design details.

For the full command reference with all flags, smart behaviors, and
workflows, see the detailed guides:
- **[Interactive Guide](docs/interactive-guide.md)** -- all TUI commands, findings navigator, PR workflows
- **[CLI Guide](docs/cli-guide.md)** -- one-shot CLI commands, flags, CI/CD integration, exit codes

## Documentation

| Document | Description |
|----------|-------------|
| [docs/interactive-guide.md](docs/interactive-guide.md) | TUI commands, findings navigator, PR workflows |
| [docs/cli-guide.md](docs/cli-guide.md) | One-shot CLI commands, flags, CI/CD integration |
| [docs/architecture.md](docs/architecture.md) | System design, pipeline flow, component responsibilities |
| [docs/configuration.md](docs/configuration.md) | All settings, provider URL resolution, secrets handling |
| [docs/data-models.md](docs/data-models.md) | Pydantic models, StrEnums, LLM contracts |
| [docs/custom-agents.md](docs/custom-agents.md) | YAML agent schema, examples, discovery, file patterns |

## Development

```bash
make install    # Install dependencies
make fmt        # Auto-format code
make lint       # Run ruff linter
make typecheck  # Run mypy (strict mode)
make test       # Run pytest with coverage
make check      # lint + typecheck + test
```

### Test Suite

856 unit tests covering models, config, LLM client, agents, agent loader,
CLI, report, orchestrator, deduplication, GitHub client, and the interactive TUI.

## Project Structure

```
src/code_review_agent/
  agents/
    base.py              # BaseAgent ABC with priority + validation
    security.py          # OWASP-focused security review
    performance.py       # Complexity, memory, I/O analysis
    style.py             # Naming, readability, dead code
    test_coverage.py     # Missing tests, edge cases
  interactive/
    commands/            # REPL commands (git, pr, review, config, etc.)
    tabs/                # Textual TUI tabs
    completers.py        # Tab completion
    provider_browser.py  # Full-screen provider/model browser
    provider_cmd.py      # Provider management commands
    repl.py              # REPL loop, dispatch, toolbar
    session.py           # Session state, PR cache
    startup_keys.py      # First-launch provider key setup panel
  agent_loader.py        # Custom YAML agent discovery + loading
  config.py              # Settings with pydantic-settings
  providers.py           # Provider registry (bundled + user ~/.cra/providers.yaml)
  provider_registry.yaml # Bundled provider/model knowledge base
  connection_test.py     # LLM connection verification
  dedup.py               # Cross-agent finding deduplication
  github_client.py       # GitHub API (PR read + write + rate limiting)
  llm_client.py          # OpenAI-compatible client with retry + JSON parsing
  main.py                # Typer CLI entry point
  models.py              # Pydantic models + StrEnums
  orchestrator.py        # Agent dispatch, deepening, validation, synthesis
  prompt_security.py     # Prompt injection defense
  report.py              # Rich terminal + Markdown rendering
  storage.py             # SQLite review history
  token_budget.py        # Tiers, budgets, cost estimation

tests/                   # 856 unit tests
docs/                    # Architecture, configuration, models, custom agents
```

## License

Apache License 2.0 -- see [LICENSE](LICENSE) for details.
