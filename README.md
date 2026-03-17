# Code Review Agent

Multi-agent code review CLI powered by LLMs. Runs specialized agents in parallel
to review GitHub pull requests or local diffs, deduplicates findings, and
synthesizes results into a structured report with severity, file location, and
actionable suggestions.

Built with Python 3.12+, Typer, Pydantic, and any OpenAI-compatible API.

## Features

**Review pipeline:**
- 4 built-in agents (security, performance, style, test coverage) + custom YAML agents
- Parallel execution via ThreadPoolExecutor with configurable concurrency
- Iterative deepening -- multiple review rounds with convergence detection
- Validation loop -- skeptical validator agent filters false positives
- Cross-agent deduplication (exact, location-based, or similarity-based)
- Token budget enforcement with automatic diff truncation

**Input/output:**
- GitHub PR review (`--pr owner/repo#123`) or local diff (`--diff file.patch`)
- Rich terminal, JSON, and Markdown output formats
- Interactive findings navigator with triage and PR comment posting

**Operations:**
- SQLite review history with trends and export
- Prompt injection defense (random delimiters, instruction anchoring)
- Cost estimation with per-model pricing
- Graceful degradation -- partial results when agents fail
- Retry with exponential backoff for transient API errors

**Extensibility:**
- Custom agents defined in YAML (no Python required)
- File pattern matching -- agents run only on relevant file types
- Provider-agnostic -- OpenRouter, NVIDIA, OpenAI, or any compatible API

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Install

```bash
git clone https://github.com/minhtribk12/code-review-agent.git
cd code-review-agent
make install
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` and set your API key:

```env
LLM_API_KEY=your-api-key-here
LLM_PROVIDER=openrouter          # openrouter, nvidia, or openai
```

See [docs/configuration.md](docs/configuration.md) for all settings.

### Run

```bash
# Review a local diff
uv run cra review --diff path/to/file.patch

# Review a GitHub PR
uv run cra review --pr owner/repo#123

# JSON output for CI pipelines
uv run cra review --diff file.patch --format json --quiet

# Interactive mode
uv run cra interactive
```

## CLI Usage

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

| Tier | Default Agents | Budget | Use Case |
|------|---------------|--------|----------|
| `free` | security | 5k tokens | Free-tier APIs, small context |
| `standard` | all 4 built-in | 16k tokens | 32k context models |
| `premium` | all 4 built-in | 48k tokens | 128k context models |

Budget is auto-detected from the model's context window when possible.
Override with `--agents` or `MAX_PROMPT_TOKENS`.

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

See [docs/custom-agents.md](docs/custom-agents.md) for the full guide.

## Interactive TUI

```bash
cra interactive
```

```
  code-review-agent v0.1.0
  Type help for commands, Tab for autocomplete, Ctrl+D to exit.

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

Key bindings: Up/Down navigate, `f` filter, `s` sort, `m` mark false positive,
`p` stage for PR posting, `P` submit staged to PR, `q` quit.

### Other Commands

```bash
config                          # show all settings
config edit                     # full-screen config editor
config set llm_temperature 0.3  # session override
history                         # past reviews
history trends --days 30        # aggregated stats
usage                           # session token/cost stats
watch --interval 10             # continuous monitoring
agents                          # list all agents (built-in + custom)
```

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

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design, pipeline flow, component responsibilities, design decisions |
| [docs/configuration.md](docs/configuration.md) | All settings, provider URL resolution, secrets handling |
| [docs/data-models.md](docs/data-models.md) | Pydantic models, StrEnums, LLM contracts |
| [docs/custom-agents.md](docs/custom-agents.md) | YAML agent schema, examples, discovery, file patterns |
| [interactive_tests/cli/README.md](interactive_tests/cli/README.md) | Mock servers and interactive test suite |

## Development

```bash
make install    # Install dependencies
make fmt        # Auto-format code
make lint       # Run ruff linter
make typecheck  # Run mypy (strict mode)
make test       # Run pytest with coverage
make check      # All of the above
```

### Test Suite

630+ unit tests covering models, config, LLM client, agents, agent loader,
CLI, report, orchestrator, deduplication, GitHub client, and the interactive TUI.

### Interactive Tests

Run against mock servers (no API keys needed):

```bash
bash interactive_tests/cli/run_all_tests.sh     # Phase 1: 16 scenarios
bash interactive_tests/cli/run_phase2_tests.sh   # Phase 2: 22 scenarios
bash interactive_tests/cli/run_phase3_tests.sh   # Phase 3: 48 scenarios
```

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
    repl.py              # REPL loop, dispatch, toolbar
    session.py           # Session state, PR cache
  agent_loader.py        # Custom YAML agent discovery + loading
  config.py              # Settings with pydantic-settings
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

tests/                   # 630+ unit tests
interactive_tests/cli/   # Mock servers + scenario tests
docs/                    # Architecture, configuration, models, custom agents
```

## License

Apache License 2.0 -- see [LICENSE](LICENSE) for details.
