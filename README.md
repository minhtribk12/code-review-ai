# Code Review Agent

Multi-agent code review CLI powered by LLMs. Runs specialized agents (security,
performance, style, test coverage) in parallel to review GitHub pull requests or
local diffs, then synthesizes findings into a structured report.

Built with Python, Typer, Pydantic, and the OpenAI-compatible API.

## Features

- **4 specialized agents** -- security, performance, style, test coverage
- **Parallel execution** -- all agents run concurrently via ThreadPoolExecutor
- **Structured output** -- findings with severity, category, file location, and suggestions
- **Multiple input modes** -- review GitHub PRs (`--pr`) or local diffs (`--diff`)
- **Multiple output formats** -- Rich terminal display and Markdown file export
- **Graceful degradation** -- partial results when individual agents fail
- **Retry with backoff** -- tenacity-powered retry for transient API errors
- **Provider-agnostic** -- works with OpenRouter, NVIDIA, OpenAI, or any OpenAI-compatible API

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

Edit `.env` and add your API key:

```env
LLM_API_KEY=your-api-key-here
LLM_PROVIDER=openrouter          # openrouter, nvidia, or openai
```

### Run

```bash
# Review a local diff
uv run code-review-agent review --diff path/to/file.patch

# Review a GitHub PR
uv run code-review-agent review --pr owner/repo#123

# Save markdown report
uv run code-review-agent review --diff file.patch --output report.md

# Debug mode
uv run code-review-agent --verbose review --diff file.patch
```

## CLI Usage

### Review a Local Diff

```bash
# Review unstaged changes in the current repo
uv run code-review-agent review --diff changes.patch

# Review with specific agents only
uv run code-review-agent review --diff changes.patch --agents security,performance

# JSON output (pipe to jq, scripts, CI)
uv run code-review-agent review --diff changes.patch --format json

# Save report to file
uv run code-review-agent review --diff changes.patch --output report.md
uv run code-review-agent review --diff changes.patch --format json --output report.json

# Quiet mode (suppress progress, show report only)
uv run code-review-agent review --diff changes.patch --quiet
```

### Review a GitHub PR

```bash
# By owner/repo#number
uv run code-review-agent review --pr owner/repo#123

# By full URL
uv run code-review-agent review --pr https://github.com/owner/repo/pull/123
```

Requires `GITHUB_TOKEN` in `.env` for private repos.

### Token Tiers

The `TOKEN_TIER` setting controls which agents run by default:

| Tier | Agents | Use case |
|------|--------|----------|
| `free` | security only | Free-tier APIs with small context windows |
| `standard` | all 4 agents | 32k context models |
| `premium` | all 4 agents | 128k context models |

Override per-run with `--agents`:

```bash
# Force all agents regardless of tier
uv run code-review-agent review --diff file.patch --agents security,performance,style,test_coverage
```

## Interactive TUI

The TUI provides a REPL-style interface with tab completion, persistent history,
and a status bar showing branch, review count, token usage, and tier.

### Launch

```bash
uv run code-review-agent interactive
```

```
  code-review-agent v0.1.0
  Type help for commands, Tab for autocomplete, Ctrl+D to exit.

cra> _
────────────────────────────────────────────────────────────────────────────────
 Branch: main | Repo: acme/app:local | Reviews: 0 | Tokens: 0 | Tier: free
```

### Git Commands

```bash
# Read
status                          # git status (branch + changed files)
diff                            # unstaged diff
diff staged                     # staged diff
diff HEAD~3                     # diff against N commits back
diff main..feat/x               # diff between branches
log                             # compact log (last 20)
log -n 5                        # last 5 commits
show abc123                     # full commit detail with diff

# Write
branch                          # list local branches
branch -r                       # list remote branches
branch switch feat/login        # switch branch (blocks if dirty)
branch create feat/new          # create + switch
branch create feat/new main     # create from specific ref
branch delete feat/old          # delete (blocks if unmerged)
branch delete feat/old --force  # force delete
branch rename old-name new-name # rename
add src/main.py                 # stage specific file
add .                           # stage all (shows file count)
unstage src/main.py             # unstage file
commit -m "fix: resolve bug"    # commit (warns if nothing staged)
stash                           # stash changes
stash pop                       # restore stash
stash list                      # list stashes
```

### Code Review

```bash
# Review current working tree changes
review                          # auto-detects unstaged/staged diff
review staged                   # review staged changes only
review HEAD~1                   # review last commit
review main..feat/x             # review branch diff
review src/auth.py              # review single file

# Options
review --agents security        # single agent
review --agents security,performance  # multiple agents
review --format json            # JSON output
```

When `review` is run with no args and there are no unstaged changes, it
auto-stages all changes, reviews, then unstages -- so you always get a review.

### Repo Management

Switch between local and remote repositories. PR commands target the active repo.

```bash
repo list                       # list local (git remotes) + remote (GitHub) repos
repo list --limit 50            # fetch more remote repos
repo select                     # interactive full-screen repo picker
repo select acme/api            # direct selection by name
repo current                    # show current active repo and source
repo clear                      # clear selection, fall back to local git remote
```

`repo select` without arguments opens a full-screen selector:

```
 Select Repository
  Up/Down to navigate, Enter to select, Esc to cancel

 > (*) acme/app:local  (Python | public | Main web application)  (current)
   ( ) acme/api:remote  (Go | private | REST API service)
   ( ) acme/docs:remote  (MDX | public | Documentation site)
```

`repo list` shows both:
- **Local repos** (`:local`) -- parsed from your git remotes (origin, upstream, etc.)
- **Remote repos** (`:remote`) -- fetched from GitHub API (your repos + collaborator access)

Duplicates are merged (local takes priority). The active repo and its source
are shown in the status bar:

```
────────────────────────────────────────────────────────────────────────────────
 Branch: main | Repo: acme/api:remote | Reviews: 0 | Tokens: 0 | Tier: free
```

### PR Commands

```bash
# Read
pr list                         # list open PRs
pr list --state closed          # list closed PRs
pr list --state all             # list all PRs
pr show 42                      # PR details (title, author, stats, labels)
pr diff 42                      # PR diff with syntax highlighting
pr checks 42                    # CI/CD check status
pr checkout 42                  # fetch and switch to PR branch
pr review 42                    # run code review on PR diff
pr review 42 --agents security  # review with specific agents

# Write
pr create --title "Add auth" --body "Adds login flow"
pr create --fill                # auto-fill title/body from commits
pr create --fill --draft        # create as draft PR
pr create --fill --base dev     # target a different base branch
pr create --fill --dry-run      # preview without creating

pr merge 42                     # merge with pre-flight checks
pr merge 42 --strategy rebase   # merge/squash/rebase
pr merge 42 --dry-run           # preview checks without merging

pr approve 42                   # approve PR
pr approve 42 -m "LGTM"        # approve with comment
pr approve 42 --dry-run         # preview without submitting

pr request-changes 42 -m "Fix the SQL injection on line 15"
pr request-changes 42 --dry-run

# Workflow helpers
pr mine                         # your open PRs
pr assigned                     # PRs where you're a reviewer
pr stale                        # PRs with no activity (default: 7 days)
pr stale --days 14              # custom threshold
pr ready                        # PRs ready to merge (approved + CI passing)
pr conflicts                    # PRs with merge conflicts
pr summary                      # dashboard overview
pr summary --full               # detailed counts
pr unresolved                   # PRs with unresolved review feedback
```

**Smart behaviors:**
- `pr review` auto-stashes dirty working tree, reviews, then pops stash
- `pr create --fill` generates title from first commit, body from all commits
- `pr merge` runs pre-flight checks (approvals, CI, conflicts) before merging
- `--dry-run` on all write commands shows preview without side effects

### Watch Mode

Continuously monitors the working tree and auto-reviews on changes:

```bash
watch                           # poll every 5s (default)
watch --interval 10             # custom interval
watch --agents security         # review with specific agents
# Press Ctrl+C to stop
```

### Configuration

```bash
config                          # show all settings (grouped, secrets masked)
config llm                      # show LLM settings only
config github                   # show GitHub settings only
config get llm_model            # get single value
config set llm_temperature 0.3  # set for this session only
config edit                     # full-screen interactive editor
config diff                     # show session overrides vs .env
config reset                    # discard session overrides
config validate                 # check config for errors
```

**`config edit`** opens a full-screen editor:
- Arrow keys to navigate between fields
- Enter/Space to edit (toggle bools, cycle enums, edit text)
- Left/Right arrows to cycle enum options
- Esc to cancel edit or exit editor
- Validation on Enter -- invalid input shows error, keeps old value

### Other Commands

```bash
usage                           # session stats (reviews, tokens, cost)
help                            # all commands
help pr                         # help for a specific group
agents                          # list available review agents
version                         # show version
clear                           # clear screen
!ls -la                         # run shell command
exit                            # exit (warns about unsaved config)
```

## Architecture

```
CLI (Typer)
  -> Orchestrator
       -> [Security Agent]     \
       -> [Performance Agent]   |-- parallel execution (ThreadPoolExecutor)
       -> [Style Agent]         |
       -> [Test Coverage Agent]/
  -> Synthesizer (merges agent results into summary + risk level)
  -> Reporter (Rich terminal / Markdown)
```

1. **Input** -- CLI parses a local `.patch` file or fetches a GitHub PR diff
2. **Dispatch** -- Orchestrator sends the diff to all 4 agents in parallel
3. **Review** -- Each agent analyzes the diff through its specialized lens
4. **Synthesis** -- A final LLM call merges all findings into an overall assessment
5. **Report** -- Findings are rendered as a Rich terminal report or saved as Markdown

See [docs/architecture.md](docs/architecture.md) for full design details and
decision rationale.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design, pipeline flow, component responsibilities, design decisions |
| [docs/data-models.md](docs/data-models.md) | Pydantic models, StrEnums, LLM contracts |
| [docs/configuration.md](docs/configuration.md) | All settings, provider URL resolution, secrets handling |
| [interactive_tests/cli/README.md](interactive_tests/cli/README.md) | Mock LLM server and interactive test suite |

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

450+ tests covering models, config, LLM client, agents, CLI, report,
orchestrator, GitHub client, and the interactive TUI.

### Interactive Tests

Run against mock servers (no API keys needed):

```bash
# Phase 1: basic CLI, diff parsing, input validation (16 scenarios)
bash interactive_tests/cli/run_all_tests.sh

# Phase 2: JSON output, agents, tiers, dedup, injection (22 scenarios)
bash interactive_tests/cli/run_phase2_tests.sh

# Phase 3: PR write, TUI commands, watch, config editor (48 scenarios)
bash interactive_tests/cli/run_phase3_tests.sh
```

Phase 3 tests use two mock servers: a mock LLM server (port 9999) and a mock
GitHub API server (port 9998) with pre-loaded PRs, reviews, and CI checks.
See [interactive_tests/cli/README.md](interactive_tests/cli/README.md) for
manual TUI testing instructions.

## Project Structure

```
src/code_review_agent/
  agents/
    base.py              # BaseAgent ABC with validation + error handling
    security.py          # OWASP-focused security review
    performance.py       # Complexity, memory, I/O analysis
    style.py             # Naming, readability, dead code
    test_coverage.py     # Missing tests, edge cases
  interactive/
    commands/
      config_cmd.py      # config show/get/set/reset/validate/diff
      config_edit.py     # full-screen interactive config editor
      git_read.py        # status, diff, log, show
      git_write.py       # branch, add, unstage, commit, stash
      meta.py            # help, agents, version, clear, shell
      pr_read.py         # pr list/show/diff/checks/checkout/review
      pr_write.py        # pr create/merge/approve/request-changes
      pr_workflow.py     # pr mine/assigned/stale/ready/conflicts/summary
      repo_cmd.py        # repo list/select/current/clear
      review_cmd.py      # review command with auto-stage
      usage_cmd.py       # session usage summary
      watch_cmd.py       # continuous file monitoring
    completers.py        # tab completion (static + dynamic branches)
    git_ops.py           # git subprocess wrappers
    repl.py              # REPL loop, dispatch, toolbar
    session.py           # session state, PR cache
  config.py              # Settings with pydantic-settings
  llm_client.py          # OpenAI-compatible client with retry + JSON parsing
  models.py              # Pydantic models + StrEnums
  orchestrator.py        # Parallel agent execution + synthesis
  main.py                # Typer CLI + diff parser
  report.py              # Rich terminal + Markdown rendering
  github_client.py       # GitHub API (PR read + write + rate limiting)

tests/                   # 450+ unit tests
interactive_tests/cli/
  mock_llm_server.py     # FastAPI mock OpenAI chat completions
  mock_github_server.py  # FastAPI mock GitHub REST API
  samples/               # .patch files for testing
  run_all_tests.sh       # Phase 1 (16 scenarios)
  run_phase2_tests.sh    # Phase 2 (22 scenarios)
  run_phase3_tests.sh    # Phase 3 (48 scenarios)
```

## License

Apache License 2.0 -- see [LICENSE](LICENSE) for details.
