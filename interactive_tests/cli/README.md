# Interactive CLI & TUI Tests

End-to-end tests for the `code-review-agent` CLI and interactive TUI. Uses
**two mock servers** (FastAPI) so you can test the full pipeline without real
API keys, GitHub accounts, or spending money.

| Server | Port | Purpose |
|--------|------|---------|
| `mock_llm_server.py` | 9999 | OpenAI-compatible chat completions (review agents) |
| `mock_github_server.py` | 9998 | GitHub REST API (PR read/write/workflow) |

---

## Structure

```
interactive_tests/cli/
  mock_llm_server.py       # FastAPI mock OpenAI-compatible chat completions API
  mock_github_server.py     # FastAPI mock GitHub REST API (PRs, reviews, checks)
  samples/
    standard.patch         # SQL injection fix + cache module (main demo)
    empty.patch            # Empty diff (edge case)
    new_file.patch         # New file with hardcoded secrets + nested loops
    deleted_file.patch     # File deletion
    renamed_file.patch     # File rename
    multi_file.patch       # 3 files: API routes + model + tests
    large_diff.patch       # 7 files: auth, cache, models, routes, email, tests, config
    injection.patch        # Prompt injection patterns embedded in code comments
  run_all_tests.sh         # Phase 1 test suite: 16 scenarios
  run_phase2_tests.sh      # Phase 2 test suite: 22 scenarios
  run_phase3_tests.sh      # Phase 3 test suite: 48 scenarios (PR write, TUI, watch)
  output/                  # Generated reports (auto-created, gitignored)
  README.md                # This file
```

---

## Quick Start (Automated)

Run all tests with a single command. Scripts start mock servers, run tests,
print results, and shut down servers automatically.

```bash
# Phase 1 tests (basic CLI, diff parsing, input validation)
bash interactive_tests/cli/run_all_tests.sh

# Phase 2 tests (JSON output, --agents, --quiet, token tiers, dedup, etc.)
bash interactive_tests/cli/run_phase2_tests.sh

# Phase 3 tests (PR write, TUI commands, watch, auto-stage/stash, mock GitHub API)
bash interactive_tests/cli/run_phase3_tests.sh
```

---

## Manual Testing

### Option A: Two Terminals (LLM mock only -- review commands)

For interactive exploration of the review pipeline.

#### Terminal 1 -- Start the mock LLM server

```bash
uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999 --reload
```

The server runs on `http://localhost:9999` with:
- `POST /v1/chat/completions` -- mock OpenAI chat endpoint
- `GET /health` -- health check
- `GET /stats` -- request counter (for verifying LLM call counts)
- `POST /reset` -- reset request counter

#### Terminal 2 -- Set environment and run CLI

```bash
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://localhost:9999/v1
export LLM_MODEL=mock/test-model
export LLM_PROVIDER=openrouter
```

##### Basic review (Rich terminal output)

```bash
uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch
```

##### JSON output (pipe to jq)

```bash
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --format json | jq .
```

##### Select specific agents

```bash
# Security only (1 LLM call, no synthesis)
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --agents security

# Security + performance (2 LLM calls + synthesis)
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --agents security,performance

# All four agents
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --agents security,performance,style,test_coverage
```

##### Token tier defaults

```bash
# Free tier: security-only
TOKEN_TIER=free uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].agent_name'

# Premium tier: all 4 agents
TOKEN_TIER=premium uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].agent_name'
```

### Option B: Three Terminals (LLM + GitHub mocks -- full TUI)

For testing the interactive TUI with PR write commands, review auto-stage,
and watch mode.

#### Terminal 1 -- Mock LLM server

```bash
uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999 --reload
```

#### Terminal 2 -- Mock GitHub API server

```bash
uv run uvicorn interactive_tests.cli.mock_github_server:app --port 9998 --reload
```

The GitHub mock runs on `http://localhost:9998` with:
- `GET  /repos/{owner}/{repo}/pulls` -- list PRs (3 sample PRs)
- `POST /repos/{owner}/{repo}/pulls` -- create PR
- `GET  /repos/{owner}/{repo}/pulls/{n}` -- PR detail
- `GET  /repos/{owner}/{repo}/pulls/{n}/files` -- PR files (with patches)
- `PUT  /repos/{owner}/{repo}/pulls/{n}/merge` -- merge PR
- `GET  /repos/{owner}/{repo}/pulls/{n}/reviews` -- list reviews
- `POST /repos/{owner}/{repo}/pulls/{n}/reviews` -- submit review
- `GET  /repos/{owner}/{repo}/commits/{sha}/check-runs` -- CI checks
- `GET  /user` -- authenticated user
- `GET  /health` -- health check
- `GET  /stats` -- counters (prs_created, prs_merged, reviews_submitted)
- `POST /reset` -- reset counters and restore sample data

**Sample data pre-loaded:**

| PR # | Title | State | CI | Reviews | Mergeable |
|------|-------|-------|----|---------|-----------|
| 42 | Fix SQL injection in login | open | all passing | 1 approval | yes |
| 43 | Add caching layer | open (draft) | pending | none | yes |
| 44 | Refactor auth middleware | open | failing | changes requested | no (conflicts) |

#### Terminal 3 -- TUI session

```bash
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://localhost:9999/v1
export LLM_MODEL=mock/test-model
export LLM_PROVIDER=openrouter
export GITHUB_TOKEN=mock-github-token

uv run code-review-agent interactive
```

Then try these TUI commands:

##### PR Read Commands

```
pr list
pr list --state all
pr show 42
pr diff 42
pr checks 42
pr checkout 42
pr review 42
pr review 42 --agents security
```

##### PR Write Commands

```
# Preview (dry-run, no API call)
pr create --title "My PR" --dry-run
pr create --fill --dry-run
pr create --fill --base main --draft --dry-run

# Actually create (calls mock API)
pr create --title "Test PR" --body "Testing"
pr create --fill

# Merge with pre-flight checks
pr merge 42 --dry-run
pr merge 42 --strategy squash
pr merge 42 --strategy rebase

# Approve / request changes
pr approve 42 --dry-run
pr approve 42 -m "LGTM"
pr request-changes 44 -m "Fix the failing CI"
pr request-changes 44 --dry-run
```

##### PR Workflow Commands

```
pr mine
pr assigned
pr stale
pr ready
pr conflicts
pr summary
pr summary --full
pr unresolved
```

##### Smart Review Workflows

```
# Auto-stages when no diff: review with no args stages, reviews, unstages
review

# Auto-stash on pr review: stashes dirty tree, reviews, pops stash
pr review 42
```

##### Watch Mode

```
# Polls every 5s (default), runs review on changes, Ctrl+C to stop
watch
watch --interval 10
watch --agents security
```

##### Verify Mock Server State

```bash
# In another terminal:
curl -s http://localhost:9998/stats | jq .
curl -s -X POST http://localhost:9998/reset
```

##### Test Error Scenarios

```
# PR not found
pr show 999

# No token set (unset GITHUB_TOKEN first)
pr create --title "test"

# Missing required comment
pr request-changes 42

# Same branch as base
# (switch to main first, then: pr create --title "test")
```

---

## Phase 1 Test Scenarios (run_all_tests.sh)

### Help and version (Tests 1-3)

| Test | Command | Verifies |
|------|---------|----------|
| 1 | `--help` | Shows app name and lists `review` command |
| 2 | `--version` | Prints version `0.1.0` |
| 3 | `review --help` | Lists `--pr`, `--diff`, `--output` options |

### Input validation (Tests 4, 13-16)

| Test | Scenario | Expected |
|------|----------|----------|
| 4 | No arguments | Error: "provide either --pr or --diff" |
| 13 | Nonexistent file path | Error about invalid/missing file |
| 14 | Both `--pr` and `--diff` | Error: "only one of --pr or --diff" |
| 15 | Invalid PR format | Error: "Invalid PR reference" |
| 16 | Missing `LLM_API_KEY` | Friendly error with setup instructions |

### Core review pipeline (Tests 5-7)

| Test | Scenario | Verifies |
|------|----------|----------|
| 5 | Standard diff | Full pipeline: agents, synthesis, Rich report |
| 6 | `--output` flag | Saves markdown report to file |
| 7 | `--verbose` flag | Debug logging enabled, no crash |

### Diff format handling (Tests 8-12)

| Test | Sample file | Verifies |
|------|-------------|----------|
| 8 | `empty.patch` | No crash on empty input |
| 9 | `new_file.patch` | Detects `added` status |
| 10 | `deleted_file.patch` | Detects `deleted` status |
| 11 | `renamed_file.patch` | Detects `renamed` status |
| 12 | `multi_file.patch` | Parses 3 files, saves markdown |

## Phase 2 Test Scenarios (run_phase2_tests.sh)

### JSON output (Tests 1-2)

| Test | Scenario | Verifies |
|------|----------|----------|
| 1 | `--format json` | Valid JSON on stdout with all fields |
| 2 | `--format json --output` | Saves valid JSON to file |

### Agent selection (Tests 4-7)

| Test | Scenario | Verifies |
|------|----------|----------|
| 4 | `--agents security` | Single agent, 1 LLM call, no synthesis |
| 5 | `--agents security,performance` | Two agents, 3 LLM calls |
| 6 | `--agents invalid_agent` | Helpful error message |
| 7 | All four agents | 5 LLM calls, all results present |

### Token budget (Tests 9, 13-14)

| Test | Scenario | Verifies |
|------|----------|----------|
| 9 | `large_diff.patch` | 7-file diff processed |
| 13 | `TOKEN_TIER=free` | Security-only |
| 14 | `TOKEN_TIER=premium` | All 4 agents |

### Dedup / Injection / Risk (Tests 10-12)

| Test | Scenario | Verifies |
|------|----------|----------|
| 10 | `injection.patch` | No crash |
| 11 | Dedup strategies | disabled + exact both work |
| 12 | Risk validation | Valid severity value |

## Phase 3 Test Scenarios (run_phase3_tests.sh)

### Section 1: Unit Tests (Tests 1-12)

| Test | Class | Verifies |
|------|-------|----------|
| 1 | `TestPrCreate/Merge/Approve/RequestChanges` | All PR write command handlers |
| 2 | `TestPrDispatchWiring` | Write commands reachable via `pr` router |
| 3 | `TestReviewAutoStage` | `review` auto-stages when no unstaged diff |
| 4 | `TestPrReviewAutoStash` | `pr review` stashes/pops dirty working tree |
| 5 | `TestWatchCommand` | Watch command is registered and dispatches |
| 6 | `TestGitOpsNewFunctions` | `has_upstream`, `log_oneline_commits_since`, `status_porcelain`, `push_branch` |
| 7 | `TestPRCacheInvalidation` | Cache clears after write operations |
| 8 | `TestCompletersPhase3` | Completers include PR write + watch |
| 9 | `TestMetaPhase3` | Help has Pr Write, Watch, Pr Read groups |
| 10 | `TestPrWriteHelpers` | `_parse_flag`, `_has_flag` parsing |
| 11 | All interactive tests | No regressions in existing tests |
| 12 | Full test suite | All 391+ tests pass |

### Section 2: Mock GitHub API (Tests 13-23)

| Test | Endpoint | Verifies |
|------|----------|----------|
| 13 | `GET /health` | Server is running |
| 14 | `GET /pulls` | Returns sample PRs |
| 15 | `GET /pulls/42` | Returns correct PR detail |
| 16 | `GET /pulls/999` | Returns 404 for missing PR |
| 17 | `POST /pulls` | Creates PR, assigns number |
| 18 | `PUT /pulls/42/merge` | Returns merged=true |
| 19 | `POST /pulls/42/reviews` | Returns correct review state |
| 20 | `GET /pulls/42/reviews` | Returns review list |
| 21 | `GET /commits/{sha}/check-runs` | Returns CI check data |
| 22 | `GET /stats` | Tracks request counts |
| 23 | `PUT /pulls/44/merge` | Returns 405 (not mergeable) |

### Section 3: Static Analysis (Tests 24-25)

| Test | Tool | Verifies |
|------|------|----------|
| 24 | `ruff check src/` | Lint passes |
| 25 | `mypy src/` | Type check passes |

### Section 4: CLI Integration (Tests 26-27)

| Test | Scenario | Verifies |
|------|----------|----------|
| 26 | Standard patch review | Review pipeline still works |
| 27 | `--agents security` filter | Agent filter still works |

### Section 5: TUI Registration (Tests 28-31)

| Test | Scenario | Verifies |
|------|----------|----------|
| 28 | `interactive --help` | Command is registered |
| 29 | Help groups | Includes "Pr Write" |
| 30 | Help groups | Includes "Watch" |
| 31 | Help groups | "Pr" renamed to "Pr Read" |

### Section 6: Config (Tests 32-34)

| Test | Scenario | Verifies |
|------|----------|----------|
| 32 | `watch_debounce_seconds=10.0` | Field accepts custom value |
| 33 | Default value | Defaults to 5.0 |
| 34 | `watch_debounce_seconds=0.1` | Rejects values < 1.0 |

### Section 7: GitHub Client Functions (Tests 35-36)

| Test | Scenario | Verifies |
|------|----------|----------|
| 35 | Import check | `create_pr`, `merge_pr`, `submit_pr_review` callable |
| 36 | Mock server call | `create_pr` works against mock GitHub server |

### Section 8: Git Ops Functions (Tests 37-40)

| Test | Function | Verifies |
|------|----------|----------|
| 37 | `has_upstream()` | Returns bool |
| 38 | `log_oneline_commits_since()` | Returns list |
| 39 | `status_porcelain()` | Returns str |
| 40 | `push_branch()` | Has correct signature (remote, set_upstream) |

### Section 9: Completers & Dispatch (Tests 41-43)

| Test | Scenario | Verifies |
|------|----------|----------|
| 41 | Completer tree | Has create, merge, approve, request-changes |
| 42 | Completer tree | Has watch command |
| 43 | REPL dispatch table | Includes watch handler |

### Section 10: PR Write Behavior (Tests 44-48)

| Test | Scenario | Verifies |
|------|----------|----------|
| 44 | `pr create --dry-run` | Shows preview, no API call |
| 45 | `pr merge --dry-run` | Shows pre-flight, no merge |
| 46 | `pr approve -m "LGTM"` | Submits APPROVE event with comment |
| 47 | `pr request-changes` (no -m) | Requires comment (mandatory) |
| 48 | `GitHubAuthError` on create | Shows "Permission denied" with scope hint |

---

## How the Mock Servers Work

### Mock LLM Server (`mock_llm_server.py`)

```
POST /v1/chat/completions
```

1. Reads the system prompt
2. Detects agent by keyword: security, performance, style, test_coverage, synthesis
3. Returns pre-built JSON matching Pydantic models
4. Adds random latency (0.2-2.0s)
5. Tracks request count via `/stats`

### Mock GitHub API Server (`mock_github_server.py`)

Implements the GitHub REST API v3 endpoints used by the TUI:

1. Pre-loaded with 3 sample PRs (42, 43, 44) with different states
2. PR 42: open, all CI passing, 1 approval, mergeable
3. PR 43: open draft, CI pending, no reviews, mergeable
4. PR 44: open, CI failing, changes requested, not mergeable (conflicts)
5. Tracks counters: `prs_created`, `prs_merged`, `reviews_submitted`
6. `POST /reset` restores sample data to initial state
7. Returns rate limit headers on all responses

---

## Adding New Test Scenarios

### For CLI review tests

1. Create a `.patch` file in `samples/`
2. Add a test block in `run_all_tests.sh` or `run_phase2_tests.sh`

### For TUI / PR write tests

1. Add mock data to `mock_github_server.py` (PRs, reviews, checks)
2. Add test blocks in `run_phase3_tests.sh`
3. For Python-level tests, add to `tests/test_interactive.py`

### For new GitHub API endpoints

1. Add the endpoint to `mock_github_server.py`
2. Add sample response data to the `_SAMPLE_*` dicts
3. Verify with `curl http://localhost:9998/your/endpoint`
