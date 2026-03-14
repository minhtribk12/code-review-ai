# Interactive CLI Tests

End-to-end interactive tests for the `code-review-agent` CLI. Uses a **mock
LLM server** (FastAPI) that responds with realistic code review findings, so
you can see the full pipeline working without a real API key or spending money.

---

## Structure

```
interactive_tests/cli/
  mock_llm_server.py       # FastAPI mock OpenAI-compatible chat completions API
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
  run_phase2_tests.sh      # Phase 2 test suite: 22 scenarios (all Phase 2 features)
  output/                  # Generated reports (auto-created, gitignored)
  README.md                # This file
```

---

## Quick Start (Automated)

Run all tests with a single command. The script starts the mock server, runs
tests, prints results, and shuts down the server automatically.

```bash
# Phase 1 tests (basic CLI, diff parsing, input validation)
bash interactive_tests/cli/run_all_tests.sh

# Phase 2 tests (JSON output, --agents, --quiet, token tiers, dedup, etc.)
bash interactive_tests/cli/run_phase2_tests.sh
```

---

## Manual Testing (Two Terminals)

For interactive exploration and debugging, run the mock server and CLI in
separate terminals.

### Terminal 1 -- Start the mock server

```bash
uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999 --reload
```

The server runs on `http://localhost:9999` with:
- `POST /v1/chat/completions` -- mock OpenAI chat endpoint
- `GET /health` -- health check
- `GET /stats` -- request counter (for verifying LLM call counts)
- `POST /reset` -- reset request counter

### Terminal 2 -- Set environment and run CLI

```bash
# Set env vars to point to the mock server
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://localhost:9999/v1
export LLM_MODEL=mock/test-model
export LLM_PROVIDER=openrouter
```

#### Basic review (Rich terminal output)

```bash
uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch
```

#### JSON output (pipe to jq)

```bash
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --format json | jq .

# Just the risk level
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --format json | jq '.risk_level'
```

#### Save reports

```bash
# Markdown report
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --output /tmp/review.md

# JSON report
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --format json --output /tmp/review.json
```

#### Select specific agents

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

#### Token tier defaults

```bash
# Free tier: runs security agent only (1 LLM call)
TOKEN_TIER=free uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].agent_name'

# Standard tier: all 4 agents
TOKEN_TIER=standard uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].agent_name'

# Premium tier: all 4 agents
TOKEN_TIER=premium uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].agent_name'
```

#### Quiet mode (suppress progress display)

```bash
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --quiet
```

#### Verbose / debug logging

```bash
uv run code-review-agent --verbose review \
  --diff interactive_tests/cli/samples/standard.patch
```

#### Test prompt injection defense

```bash
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/injection.patch --format json | jq '.agent_results[].findings[].title'
```

#### Test large multi-file diff

```bash
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/large_diff.patch \
  --agents security,performance --format json | jq '.agent_results | length'
```

#### Dedup strategy

```bash
# Run with disabled dedup
DEDUP_STRATEGY=disabled uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].findings | length'

# Run with exact dedup (default)
DEDUP_STRATEGY=exact uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch --format json | jq '.agent_results[].findings | length'
```

#### Verify LLM call counts

```bash
# Reset counter
curl -s -X POST http://localhost:9999/reset

# Run single agent
uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch --agents security --format json > /dev/null

# Check: should be 1 (no synthesis for single agent)
curl -s http://localhost:9999/stats | jq .request_count

# Reset and run two agents
curl -s -X POST http://localhost:9999/reset
uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch --agents security,performance --format json > /dev/null

# Check: should be 3 (2 agents + 1 synthesis)
curl -s http://localhost:9999/stats | jq .request_count
```

#### Test the mock API directly with curl

```bash
curl -s http://localhost:9999/health

curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "messages": [
      {"role": "system", "content": "You are a security reviewer."},
      {"role": "user", "content": "Review this code."}
    ]
  }' | python3 -m json.tool
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

### JSON output (Tests 1-2) -- CRA-33

| Test | Scenario | Verifies |
|------|----------|----------|
| 1 | `--format json` | Valid JSON on stdout with all fields |
| 2 | `--format json --output` | Saves valid JSON to file |

### Progress and quiet (Tests 3, 18) -- CRA-32

| Test | Scenario | Verifies |
|------|----------|----------|
| 3 | `--quiet` | Suppresses progress, still shows report |
| 18 | `--format json` | Auto-suppresses progress, clean stdout |

### Agent selection (Tests 4-7) -- multi-agent

| Test | Scenario | Verifies |
|------|----------|----------|
| 4 | `--agents security` | Single agent, 1 LLM call, no synthesis |
| 5 | `--agents security,performance` | Two agents, 3 LLM calls |
| 6 | `--agents invalid_agent` | Helpful error message |
| 7 | All four agents | 5 LLM calls, all results present |

### Execution timing (Test 8)

| Test | Scenario | Verifies |
|------|----------|----------|
| 8 | All four agents | Positive execution times recorded |

### Token budget (Tests 9, 13-14) -- CRA-25

| Test | Scenario | Verifies |
|------|----------|----------|
| 9 | `large_diff.patch` | 7-file diff processed successfully |
| 13 | `TOKEN_TIER=free` | Security-only, 1 LLM call |
| 14 | `TOKEN_TIER=premium` | All 4 agents |

### Prompt injection (Test 10) -- CRA-26

| Test | Scenario | Verifies |
|------|----------|----------|
| 10 | `injection.patch` | Processes without crash |

### Dedup strategy (Test 11) -- CRA-27

| Test | Scenario | Verifies |
|------|----------|----------|
| 11 | `DEDUP_STRATEGY=disabled/exact` | Both strategies work |

### Risk validation (Test 12) -- CRA-28

| Test | Scenario | Verifies |
|------|----------|----------|
| 12 | All agents | Risk level is valid severity value |

### CLI options (Test 15)

| Test | Scenario | Verifies |
|------|----------|----------|
| 15 | `review --help` | Shows `--format`, `--quiet`, `--agents` |

### Output formats (Tests 16-17)

| Test | Scenario | Verifies |
|------|----------|----------|
| 16 | Rich terminal | Report header and findings count |
| 17 | Markdown file | Both agent sections present |

### Config (Tests 19-20)

| Test | Scenario | Verifies |
|------|----------|----------|
| 19 | Empty diff + JSON | No crash |
| 20 | `MAX_REVIEW_SECONDS=120` | Custom config accepted |

### Data validation (Tests 21-22)

| Test | Scenario | Verifies |
|------|----------|----------|
| 21 | Finding structure | All required fields, valid enum values |
| 22 | Agent status | Valid status values (success/partial/failed) |

---

## How the Mock Server Works

`mock_llm_server.py` implements a single endpoint:

```
POST /v1/chat/completions
```

The server:

1. Reads the system prompt from the request
2. Detects which agent is calling by keyword matching:
   - `"security"` -> SQL injection + info leakage findings
   - `"performance"` -> unbounded cache finding
   - `"style"` -> no findings (clean code)
   - `"test coverage"` -> missing tests finding
   - `"synthesiz"` or `"senior engineering"` -> overall summary + risk level
3. Returns a pre-built JSON response matching the expected Pydantic model
4. Adds random per-agent latency (0.2-2.0s) to simulate realistic parallel progress
5. Reports realistic token usage counts
6. Tracks request count via `/stats` endpoint for test verification

---

## Adding New Scenarios

1. Create a `.patch` file in `samples/`
2. Add a test block in the appropriate test script:

```bash
echo ""
echo "--- Test N: Description ---"

run_cli review --diff "$SAMPLES/your_file.patch" --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Your assertion"
else
    fail_test "Your assertion" "Exit code: $CLI_EXIT"
fi
```

3. To add mock responses for new agent types, edit `_AGENT_RESPONSES` in
   `mock_llm_server.py`.
