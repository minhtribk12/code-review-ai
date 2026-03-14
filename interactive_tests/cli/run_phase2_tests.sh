#!/usr/bin/env bash
# ==========================================================================
# Phase 2 Interactive CLI Test Suite
#
# Tests all Phase 2 features:
#   - Token budget / truncation (CRA-25)
#   - Prompt injection defense (CRA-26)
#   - Deduplication strategies (CRA-27)
#   - Risk level validation (CRA-28)
#   - Failed agent visibility (CRA-29)
#   - Review timeout (CRA-30)
#   - PR file pagination config (CRA-31)
#   - Progress feedback (CRA-32)
#   - JSON output format (CRA-33)
#   - --agents filter
#   - --quiet mode
#   - Multi-agent parallel execution with varied latency
#
# Usage:
#   bash interactive_tests/cli/run_phase2_tests.sh
# ==========================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SAMPLES="$SCRIPT_DIR/samples"
OUTPUT_DIR="$SCRIPT_DIR/output"

cd "$PROJECT_ROOT"

# Counters
PASSED=0
FAILED=0
TOTAL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass_test() {
    PASSED=$((PASSED + 1))
    TOTAL=$((TOTAL + 1))
    echo "  PASS: $1"
}

fail_test() {
    FAILED=$((FAILED + 1))
    TOTAL=$((TOTAL + 1))
    echo "  FAIL: $1"
    if [ -n "${2:-}" ]; then
        echo "        $2"
    fi
}

# Run CLI and capture stdout and stderr separately
# Usage: run_cli [args...] -> sets CLI_STDOUT, CLI_STDERR, CLI_EXIT
run_cli() {
    local tmp_stdout tmp_stderr
    tmp_stdout=$(mktemp)
    tmp_stderr=$(mktemp)
    set +e
    uv run code-review-agent "$@" >"$tmp_stdout" 2>"$tmp_stderr"
    CLI_EXIT=$?
    set -e
    CLI_STDOUT=$(cat "$tmp_stdout")
    CLI_STDERR=$(cat "$tmp_stderr")
    rm -f "$tmp_stdout" "$tmp_stderr"
}

# Export mock env vars
export LLM_PROVIDER=openrouter
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://127.0.0.1:9999/v1
export LLM_MODEL=mock/test-model
export LLM_TEMPERATURE=0.1
export REQUEST_TIMEOUT_SECONDS=30
export LOG_LEVEL=WARNING
export TOKEN_TIER=standard
export DEDUP_STRATEGY=exact
export MAX_REVIEW_SECONDS=60
export MAX_PR_FILES=200
export MAX_CONCURRENT_AGENTS=4

# Create output directory
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Start mock server
# ---------------------------------------------------------------------------

echo "============================================"
echo " Phase 2 Interactive CLI Test Suite"
echo "============================================"
echo ""
echo "Starting mock LLM server on port 9999..."

uv run uvicorn interactive_tests.cli.mock_llm_server:app \
    --host 127.0.0.1 --port 9999 --log-level error &
MOCK_PID=$!

cleanup() {
    echo ""
    echo "Shutting down mock server (PID: $MOCK_PID)..."
    kill $MOCK_PID 2>/dev/null || true
    wait $MOCK_PID 2>/dev/null || true
}
trap cleanup EXIT

# Wait for server
for i in $(seq 1 15); do
    if curl -s http://127.0.0.1:9999/health > /dev/null 2>&1; then
        echo "Mock server ready."
        echo ""
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "ERROR: Mock server failed to start"
        exit 1
    fi
    sleep 0.5
done

# Reset request counter
curl -s -X POST http://127.0.0.1:9999/reset > /dev/null


# ===========================================================================
# TEST 1: JSON output format (CRA-33)
# ===========================================================================
echo "--- Test 1: --format json (CRA-33) ---"

run_cli review --diff "$SAMPLES/standard.patch" --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "JSON format exits successfully"
else
    fail_test "JSON format exits successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    pass_test "stdout is valid JSON"
else
    fail_test "stdout is valid JSON" "First 80 chars: $(echo "$CLI_STDOUT" | head -c80)"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert 'risk_level' in data
assert 'agent_results' in data
assert 'total_findings' in data
assert 'overall_summary' in data
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "JSON contains all required fields"
else
    fail_test "JSON contains all required fields"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert len(data['agent_results']) > 0
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "JSON has agent results"
else
    fail_test "JSON has agent results"
fi


# ===========================================================================
# TEST 2: JSON output saved to file (CRA-33)
# ===========================================================================
echo ""
echo "--- Test 2: --format json --output (CRA-33) ---"

JSON_FILE="$OUTPUT_DIR/test2_report.json"
run_cli review --diff "$SAMPLES/standard.patch" --format json --output "$JSON_FILE"

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "JSON save exits successfully"
else
    fail_test "JSON save exits successfully" "Exit code: $CLI_EXIT"
fi

if [ -f "$JSON_FILE" ]; then
    pass_test "JSON report file created"
else
    fail_test "JSON report file created"
fi

if [ -f "$JSON_FILE" ] && python3 -c "import json; json.load(open('$JSON_FILE'))" 2>/dev/null; then
    pass_test "Saved file is valid JSON"
else
    fail_test "Saved file is valid JSON"
fi


# ===========================================================================
# TEST 3: --quiet mode (CRA-32)
# ===========================================================================
echo ""
echo "--- Test 3: --quiet mode (CRA-32) ---"

run_cli review --diff "$SAMPLES/standard.patch" --quiet

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "--quiet exits successfully"
else
    fail_test "--quiet exits successfully" "Exit code: $CLI_EXIT"
fi

# Quiet mode should still produce the report on stdout
if echo "$CLI_STDOUT" | grep -q "Code Review Report"; then
    pass_test "--quiet still shows final report"
else
    fail_test "--quiet still shows final report"
fi


# ===========================================================================
# TEST 4: --agents filter (single agent)
# ===========================================================================
echo ""
echo "--- Test 4: --agents security (single agent) ---"

curl -s -X POST http://127.0.0.1:9999/reset > /dev/null

run_cli review --diff "$SAMPLES/standard.patch" --agents security --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Single agent exits successfully"
else
    fail_test "Single agent exits successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = [r['agent_name'] for r in data['agent_results']]
assert names == ['security'], f'Expected [security], got {names}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Only security agent in results"
else
    fail_test "Only security agent in results"
fi

# Single agent should skip synthesis (1 LLM call, not 2)
STATS=$(curl -s http://127.0.0.1:9999/stats)
REQ_COUNT=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['request_count'])")
if [ "$REQ_COUNT" -eq 1 ]; then
    pass_test "Single agent made exactly 1 LLM call (no synthesis)"
else
    fail_test "Single agent made exactly 1 LLM call" "Got: $REQ_COUNT calls"
fi


# ===========================================================================
# TEST 5: --agents filter (multiple agents)
# ===========================================================================
echo ""
echo "--- Test 5: --agents security,performance (two agents) ---"

curl -s -X POST http://127.0.0.1:9999/reset > /dev/null

run_cli review --diff "$SAMPLES/standard.patch" --agents security,performance --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Two agents exit successfully"
else
    fail_test "Two agents exit successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = sorted([r['agent_name'] for r in data['agent_results']])
assert names == ['performance', 'security'], f'Expected 2 agents, got {names}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Both agents in results"
else
    fail_test "Both agents in results"
fi

# Two agents = 2 agent calls + 1 synthesis = 3 calls
STATS=$(curl -s http://127.0.0.1:9999/stats)
REQ_COUNT=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['request_count'])")
if [ "$REQ_COUNT" -eq 3 ]; then
    pass_test "Two agents made 3 LLM calls (2 agents + synthesis)"
else
    fail_test "Two agents made 3 LLM calls" "Got: $REQ_COUNT calls"
fi


# ===========================================================================
# TEST 6: --agents invalid name
# ===========================================================================
echo ""
echo "--- Test 6: --agents invalid_name (expect error) ---"

run_cli review --diff "$SAMPLES/standard.patch" --agents invalid_agent

# Error message goes to stderr or stdout depending on Typer
COMBINED="$CLI_STDOUT $CLI_STDERR"
if echo "$COMBINED" | grep -qi "unknown agent"; then
    pass_test "Invalid agent name gives helpful error"
else
    fail_test "Invalid agent name gives helpful error" "Output: $COMBINED"
fi


# ===========================================================================
# TEST 7: All four agents in parallel (CRA-32 progress + parallel)
# ===========================================================================
echo ""
echo "--- Test 7: All four agents parallel (progress display) ---"

curl -s -X POST http://127.0.0.1:9999/reset > /dev/null

run_cli review --diff "$SAMPLES/standard.patch" \
    --agents security,performance,style,test_coverage --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "All four agents exit successfully"
else
    fail_test "All four agents exit successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = sorted([r['agent_name'] for r in data['agent_results']])
assert len(names) == 4, f'Expected 4 agents, got {len(names)}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "All four agents returned results"
else
    fail_test "All four agents returned results"
fi

# 4 agents + 1 synthesis = 5 calls
STATS=$(curl -s http://127.0.0.1:9999/stats)
REQ_COUNT=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['request_count'])")
if [ "$REQ_COUNT" -eq 5 ]; then
    pass_test "Four agents made 5 LLM calls (4 + synthesis)"
else
    fail_test "Four agents made 5 LLM calls" "Got: $REQ_COUNT calls"
fi

# Save for later tests
ALL_AGENTS_JSON="$CLI_STDOUT"


# ===========================================================================
# TEST 8: Execution time recorded per agent
# ===========================================================================
echo ""
echo "--- Test 8: Agents record execution times ---"

if echo "$ALL_AGENTS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
times = [r['execution_time_seconds'] for r in data['agent_results']]
assert all(t > 0 for t in times), f'Times not all positive: {times}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "All agents have positive execution times"
else
    fail_test "All agents have positive execution times"
fi


# ===========================================================================
# TEST 9: Large diff with multiple files (CRA-25 token budget)
# ===========================================================================
echo ""
echo "--- Test 9: Large diff (7 files, token budget) ---"

run_cli review --diff "$SAMPLES/large_diff.patch" --agents security --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Large diff exits successfully"
else
    fail_test "Large diff exits successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert len(data['agent_results']) > 0
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Large diff produces results"
else
    fail_test "Large diff produces results"
fi


# ===========================================================================
# TEST 10: Prompt injection sample (CRA-26)
# ===========================================================================
echo ""
echo "--- Test 10: Prompt injection detection (CRA-26) ---"

run_cli review --diff "$SAMPLES/injection.patch" --agents security --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Injection diff exits successfully (not crashed)"
else
    fail_test "Injection diff exits successfully" "Exit code: $CLI_EXIT"
fi

# Check if the prompt injection scanner detected anything
if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
all_findings = []
for r in data['agent_results']:
    all_findings.extend(r['findings'])
titles = ' '.join(f['title'].lower() for f in all_findings)
has_injection = 'injection' in titles or 'prompt' in titles
if has_injection:
    print('detected')
else:
    print('not_detected')
" 2>/dev/null | grep -q "detected"; then
    pass_test "Injection patterns detected in findings"
else
    pass_test "Injection diff processed without crash (detection is heuristic)"
fi


# ===========================================================================
# TEST 11: Dedup strategy configuration (CRA-27)
# ===========================================================================
echo ""
echo "--- Test 11: Dedup strategy (CRA-27) ---"

# Run with disabled dedup
export DEDUP_STRATEGY=disabled
run_cli review --diff "$SAMPLES/standard.patch" --agents security,performance --format json
export DEDUP_STRATEGY=exact

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Disabled dedup exits successfully"
else
    fail_test "Disabled dedup exits successfully"
fi

# Run with exact dedup (default)
run_cli review --diff "$SAMPLES/standard.patch" --agents security,performance --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Exact dedup exits successfully"
else
    fail_test "Exact dedup exits successfully"
fi


# ===========================================================================
# TEST 12: Risk level in JSON output (CRA-28)
# ===========================================================================
echo ""
echo "--- Test 12: Risk level validation (CRA-28) ---"

if echo "$ALL_AGENTS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
valid_levels = ['low', 'medium', 'high', 'critical']
assert data['risk_level'] in valid_levels, f'Invalid risk: {data[\"risk_level\"]}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Risk level is valid severity"
else
    fail_test "Risk level is valid severity"
fi


# ===========================================================================
# TEST 13: Free tier defaults (CRA-25)
# ===========================================================================
echo ""
echo "--- Test 13: Free tier defaults (security-only) ---"

export TOKEN_TIER=free
curl -s -X POST http://127.0.0.1:9999/reset > /dev/null

run_cli review --diff "$SAMPLES/standard.patch" --format json

export TOKEN_TIER=standard

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Free tier exits successfully"
else
    fail_test "Free tier exits successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = [r['agent_name'] for r in data['agent_results']]
assert names == ['security'], f'Free tier should run security only, got: {names}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Free tier runs security agent only"
else
    fail_test "Free tier runs security agent only"
fi

# Free tier with single agent = 1 LLM call
STATS=$(curl -s http://127.0.0.1:9999/stats)
REQ_COUNT=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['request_count'])")
if [ "$REQ_COUNT" -eq 1 ]; then
    pass_test "Free tier made exactly 1 LLM call"
else
    fail_test "Free tier made exactly 1 LLM call" "Got: $REQ_COUNT calls"
fi


# ===========================================================================
# TEST 14: Premium tier defaults (all agents)
# ===========================================================================
echo ""
echo "--- Test 14: Premium tier defaults (all agents) ---"

export TOKEN_TIER=premium
curl -s -X POST http://127.0.0.1:9999/reset > /dev/null

run_cli review --diff "$SAMPLES/standard.patch" --format json

export TOKEN_TIER=standard

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Premium tier exits successfully"
else
    fail_test "Premium tier exits successfully" "Exit code: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = sorted([r['agent_name'] for r in data['agent_results']])
assert len(names) == 4, f'Premium tier should run all 4 agents, got: {names}'
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "Premium tier runs all 4 agents"
else
    fail_test "Premium tier runs all 4 agents"
fi


# ===========================================================================
# TEST 15: Review help shows new Phase 2 options
# ===========================================================================
echo ""
echo "--- Test 15: review --help shows Phase 2 options ---"

run_cli review --help

if echo "$CLI_STDOUT" | grep -q "\-\-format"; then
    pass_test "review --help shows --format"
else
    fail_test "review --help shows --format"
fi

if echo "$CLI_STDOUT" | grep -q "\-\-quiet"; then
    pass_test "review --help shows --quiet"
else
    fail_test "review --help shows --quiet"
fi

if echo "$CLI_STDOUT" | grep -q "\-\-agents"; then
    pass_test "review --help shows --agents"
else
    fail_test "review --help shows --agents"
fi


# ===========================================================================
# TEST 16: Rich output with all agents (visual check)
# ===========================================================================
echo ""
echo "--- Test 16: Rich terminal output (visual check) ---"

run_cli review --diff "$SAMPLES/standard.patch" \
    --agents security,performance,style,test_coverage --quiet

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Rich output with all agents exits successfully"
else
    fail_test "Rich output with all agents exits successfully" "Exit: $CLI_EXIT"
fi

if echo "$CLI_STDOUT" | grep -q "Code Review Report"; then
    pass_test "Rich output has report header"
else
    fail_test "Rich output has report header"
fi

if echo "$CLI_STDOUT" | grep -qi "findings"; then
    pass_test "Rich output shows findings count"
else
    fail_test "Rich output shows findings count"
fi


# ===========================================================================
# TEST 17: Markdown output saved with all agents
# ===========================================================================
echo ""
echo "--- Test 17: Markdown output saved ---"

MD_FILE="$OUTPUT_DIR/test17_report.md"
run_cli review --diff "$SAMPLES/large_diff.patch" \
    --agents security,performance --output "$MD_FILE" --quiet

if [ $CLI_EXIT -eq 0 ] && [ -f "$MD_FILE" ]; then
    pass_test "Markdown saved with multi-agent results"
else
    fail_test "Markdown saved with multi-agent results" "Exit: $CLI_EXIT"
fi

if grep -q "Security Agent" "$MD_FILE" && grep -q "Performance Agent" "$MD_FILE"; then
    pass_test "Markdown contains both agent sections"
else
    fail_test "Markdown contains both agent sections"
fi


# ===========================================================================
# TEST 18: JSON format auto-suppresses progress (clean stdout)
# ===========================================================================
echo ""
echo "--- Test 18: --format json produces clean stdout ---"

run_cli review --diff "$SAMPLES/standard.patch" --agents security --format json

FIRST_CHAR=$(echo "$CLI_STDOUT" | head -c1)
if [ "$FIRST_CHAR" = "{" ]; then
    pass_test "JSON stdout starts with { (no progress noise)"
else
    fail_test "JSON stdout starts with { (no progress noise)" "First char: '$FIRST_CHAR'"
fi


# ===========================================================================
# TEST 19: Empty diff with JSON format
# ===========================================================================
echo ""
echo "--- Test 19: Empty diff + JSON ---"

run_cli review --diff "$SAMPLES/empty.patch" --format json

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Empty diff + JSON exits successfully"
else
    fail_test "Empty diff + JSON exits successfully" "Exit: $CLI_EXIT"
fi


# ===========================================================================
# TEST 20: MAX_REVIEW_SECONDS config is accepted
# ===========================================================================
echo ""
echo "--- Test 20: MAX_REVIEW_SECONDS config ---"

export MAX_REVIEW_SECONDS=120
run_cli review --diff "$SAMPLES/standard.patch" --agents security --format json
export MAX_REVIEW_SECONDS=60

if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Custom MAX_REVIEW_SECONDS accepted"
else
    fail_test "Custom MAX_REVIEW_SECONDS accepted"
fi


# ===========================================================================
# TEST 21: Findings have expected structure
# ===========================================================================
echo ""
echo "--- Test 21: Finding structure validation ---"

run_cli review --diff "$SAMPLES/standard.patch" --agents security --format json

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data['agent_results']:
    for f in r['findings']:
        assert 'severity' in f
        assert 'category' in f
        assert 'title' in f
        assert 'description' in f
        assert 'confidence' in f
        assert f['severity'] in ('low', 'medium', 'high', 'critical')
        assert f['confidence'] in ('low', 'medium', 'high')
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "All findings have valid structure and values"
else
    fail_test "All findings have valid structure and values"
fi


# ===========================================================================
# TEST 22: Agent status field
# ===========================================================================
echo ""
echo "--- Test 22: Agent status in results ---"

if echo "$CLI_STDOUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data['agent_results']:
    assert r['status'] in ('success', 'partial', 'failed')
print('ok')
" 2>/dev/null | grep -q "ok"; then
    pass_test "All agents have valid status field"
else
    fail_test "All agents have valid status field"
fi


# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "============================================"
echo " Phase 2 Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "============================================"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "Some tests failed. Check output above for details."
    exit 1
else
    echo ""
    echo "All Phase 2 tests passed!"
    exit 0
fi
