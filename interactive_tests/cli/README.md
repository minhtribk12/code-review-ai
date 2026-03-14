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
  run_all_tests.sh         # Full test suite: 16 scenarios with PASS/FAIL
  output/                  # Generated reports (auto-created, gitignored)
  README.md                # This file
```

---

## How to Run

```bash
# From project root
bash interactive_tests/cli/run_all_tests.sh
```

The script will:
1. Start the mock LLM server on port 9999
2. Run 16 test scenarios covering every CLI feature
3. Print PASS/FAIL for each assertion
4. Show final score and shut down the server

---

## Test Scenarios

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
| 5 | Standard diff | Full pipeline: diff parsing, 4 agents in parallel, synthesis, Rich terminal report |
| 6 | `--output` flag | Saves markdown report to file, confirms with message |
| 7 | `--verbose` flag | Debug logging enabled, no crash |

### Diff format handling (Tests 8-12)

| Test | Sample file | Verifies |
|------|-------------|----------|
| 8 | `empty.patch` | No crash on empty input |
| 9 | `new_file.patch` | Detects `added` status from `new file mode` header |
| 10 | `deleted_file.patch` | Detects `deleted` status from `deleted file mode` header |
| 11 | `renamed_file.patch` | Detects `renamed` status from `rename from/to` header |
| 12 | `multi_file.patch` | Parses 3 files with mixed statuses, saves markdown |

---

## Expected Output

```
============================================
 Interactive CLI Test Suite
============================================

Starting mock LLM server on port 9999...
Mock server ready.

--- Test 1: --help ---
  PASS: --help shows app name
  PASS: --help lists review command

--- Test 2: --version ---
  PASS: --version shows 0.1.0

--- Test 3: review --help ---
  PASS: review --help shows --pr option
  PASS: review --help shows --diff option
  PASS: review --help shows --output option

--- Test 4: No arguments (expect error) ---
  PASS: No args gives helpful error message

--- Test 5: Review standard diff (standard.patch) ---
  PASS: Exit code 0 on valid diff
  PASS: Output contains report title
  PASS: Output contains risk level
  PASS: Output contains summary
  PASS: Output mentions security agent

...

============================================
 Results: 30/30 passed, 0 failed
============================================

All tests passed!
```

---

## How the Mock Server Works

`mock_llm_server.py` implements a single endpoint:

```
POST /v1/chat/completions
```

This is the standard OpenAI chat completions endpoint. The server:

1. Reads the system prompt from the request
2. Detects which agent is calling by keyword matching:
   - `"security"` -> SQL injection + info leakage findings
   - `"performance"` -> unbounded cache finding
   - `"style"` -> no findings (clean code)
   - `"test coverage"` -> missing tests finding
   - `"synthesiz"` or `"senior engineering"` -> overall summary + risk level
3. Returns a pre-built JSON response matching the expected Pydantic model
4. Adds 300ms delay to simulate real API latency
5. Reports realistic token usage counts

### Run the mock server standalone

**Terminal 1 -- start the mock server:**

```bash
uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999 --reload
```

**Terminal 2 -- run the CLI against it:**

```bash
# Set env vars to point to the mock server
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://localhost:9999/v1
export LLM_MODEL=mock/test-model
export LLM_PROVIDER=openrouter

# Review a sample diff
uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch

# Review with markdown output
uv run code-review-agent review \
  --diff interactive_tests/cli/samples/standard.patch \
  --output /tmp/review.md

# Review with debug logging
uv run code-review-agent --verbose review \
  --diff interactive_tests/cli/samples/new_file.patch

# Try different sample diffs
uv run code-review-agent review --diff interactive_tests/cli/samples/multi_file.patch
uv run code-review-agent review --diff interactive_tests/cli/samples/deleted_file.patch
```

You can also set these env vars in a one-liner:

```bash
LLM_API_KEY=mock-key LLM_BASE_URL=http://localhost:9999/v1 LLM_MODEL=mock/test-model LLM_PROVIDER=openrouter \
  uv run code-review-agent review --diff interactive_tests/cli/samples/standard.patch
```

**Terminal 2 -- test the API directly with curl:**

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
  }' | python -m json.tool
```

---

## Adding New Scenarios

1. Create a `.patch` file in `samples/`
2. Add a test block in `run_all_tests.sh`:

```bash
echo ""
echo "--- Test N: Description ---"

OUTPUT=$(uv run code-review-agent review --diff "$SAMPLES/your_file.patch" 2>&1)
EXIT=$?

if [ $EXIT -eq 0 ]; then
    pass_test "Your assertion"
else
    fail_test "Your assertion" "Exit code: $EXIT"
fi
```

3. To add mock responses for new agent types, edit `_AGENT_RESPONSES` in
   `mock_llm_server.py`.
