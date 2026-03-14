#!/usr/bin/env bash
# ==========================================================================
# Interactive CLI Test Suite
#
# Starts a mock LLM server and runs the CLI through every common scenario.
# Each test prints PASS/FAIL and the final summary shows the score.
#
# Usage:
#   bash interactive_tests/cli/run_all_tests.sh
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

# Export mock env vars (so CLI doesn't need .env file)
export LLM_PROVIDER=openrouter
export LLM_API_KEY=mock-key-not-real
export LLM_BASE_URL=http://127.0.0.1:9999/v1
export LLM_MODEL=mock/test-model
export LLM_TEMPERATURE=0.1
export REQUEST_TIMEOUT_SECONDS=30
export LOG_LEVEL=WARNING

# Create output directory
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Start mock server
# ---------------------------------------------------------------------------

echo "============================================"
echo " Interactive CLI Test Suite"
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

# ===========================================================================
# TEST 1: --help
# ===========================================================================
echo "--- Test 1: --help ---"

HELP_OUTPUT=$(uv run code-review-agent --help 2>&1)
if echo "$HELP_OUTPUT" | grep -q "code-review-agent"; then
    pass_test "--help shows app name"
else
    fail_test "--help shows app name" "Output: $HELP_OUTPUT"
fi

if echo "$HELP_OUTPUT" | grep -q "review"; then
    pass_test "--help lists review command"
else
    fail_test "--help lists review command"
fi

# ===========================================================================
# TEST 2: --version
# ===========================================================================
echo ""
echo "--- Test 2: --version ---"

VERSION_OUTPUT=$(uv run code-review-agent --version 2>&1)
if echo "$VERSION_OUTPUT" | grep -q "0.1.0"; then
    pass_test "--version shows 0.1.0"
else
    fail_test "--version shows 0.1.0" "Output: $VERSION_OUTPUT"
fi

# ===========================================================================
# TEST 3: review --help
# ===========================================================================
echo ""
echo "--- Test 3: review --help ---"

REVIEW_HELP=$(uv run code-review-agent review --help 2>&1)
if echo "$REVIEW_HELP" | grep -q "\-\-pr"; then
    pass_test "review --help shows --pr option"
else
    fail_test "review --help shows --pr option"
fi

if echo "$REVIEW_HELP" | grep -q "\-\-diff"; then
    pass_test "review --help shows --diff option"
else
    fail_test "review --help shows --diff option"
fi

if echo "$REVIEW_HELP" | grep -q "\-\-output"; then
    pass_test "review --help shows --output option"
else
    fail_test "review --help shows --output option"
fi

# ===========================================================================
# TEST 4: No arguments -- should fail with helpful message
# ===========================================================================
echo ""
echo "--- Test 4: No arguments (expect error) ---"

NO_ARGS_OUTPUT=$(uv run code-review-agent review 2>&1) || true
if echo "$NO_ARGS_OUTPUT" | grep -qi "provide either"; then
    pass_test "No args gives helpful error message"
else
    fail_test "No args gives helpful error message" "Output: $NO_ARGS_OUTPUT"
fi

# ===========================================================================
# TEST 5: Review standard diff (main scenario)
# ===========================================================================
echo ""
echo "--- Test 5: Review standard diff (sample.patch) ---"

STD_OUTPUT=$(uv run code-review-agent review --diff "$SAMPLES/standard.patch" 2>&1)
STD_EXIT=$?

if [ $STD_EXIT -eq 0 ]; then
    pass_test "Exit code 0 on valid diff"
else
    fail_test "Exit code 0 on valid diff" "Got exit code $STD_EXIT"
fi

if echo "$STD_OUTPUT" | grep -q "Code Review Report"; then
    pass_test "Output contains report title"
else
    fail_test "Output contains report title"
fi

if echo "$STD_OUTPUT" | grep -qi "risk level"; then
    pass_test "Output contains risk level"
else
    fail_test "Output contains risk level"
fi

if echo "$STD_OUTPUT" | grep -qi "summary"; then
    pass_test "Output contains summary"
else
    fail_test "Output contains summary"
fi

if echo "$STD_OUTPUT" | grep -qi "security"; then
    pass_test "Output mentions security agent"
else
    fail_test "Output mentions security agent"
fi

# ===========================================================================
# TEST 6: Review with --output (save markdown)
# ===========================================================================
echo ""
echo "--- Test 6: Review with --output (save markdown) ---"

REPORT_FILE="$OUTPUT_DIR/test6_report.md"
SAVE_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/standard.patch" \
    --output "$REPORT_FILE" 2>&1)
SAVE_EXIT=$?

if [ $SAVE_EXIT -eq 0 ]; then
    pass_test "--output exits successfully"
else
    fail_test "--output exits successfully" "Exit code: $SAVE_EXIT"
fi

if [ -f "$REPORT_FILE" ]; then
    pass_test "Report file created"
else
    fail_test "Report file created" "File not found: $REPORT_FILE"
fi

if [ -f "$REPORT_FILE" ] && grep -q "# Code Review Report" "$REPORT_FILE"; then
    pass_test "Markdown report has correct header"
else
    fail_test "Markdown report has correct header"
fi

if echo "$SAVE_OUTPUT" | grep -q "Report saved"; then
    pass_test "CLI confirms report saved"
else
    fail_test "CLI confirms report saved"
fi

# ===========================================================================
# TEST 7: Review with --verbose
# ===========================================================================
echo ""
echo "--- Test 7: Review with --verbose (debug logging) ---"

export LOG_LEVEL=DEBUG
VERBOSE_OUTPUT=$(uv run code-review-agent --verbose review \
    --diff "$SAMPLES/standard.patch" 2>&1)
VERBOSE_EXIT=$?
export LOG_LEVEL=WARNING

if [ $VERBOSE_EXIT -eq 0 ]; then
    pass_test "--verbose exits successfully"
else
    fail_test "--verbose exits successfully" "Exit code: $VERBOSE_EXIT"
fi

# ===========================================================================
# TEST 8: Empty diff file
# ===========================================================================
echo ""
echo "--- Test 8: Empty diff file ---"

EMPTY_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/empty.patch" 2>&1)
EMPTY_EXIT=$?

if [ $EMPTY_EXIT -eq 0 ]; then
    pass_test "Empty diff exits successfully (no crash)"
else
    fail_test "Empty diff exits successfully" "Exit code: $EMPTY_EXIT"
fi

# ===========================================================================
# TEST 9: New file diff (status: added)
# ===========================================================================
echo ""
echo "--- Test 9: New file diff ---"

NEW_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/new_file.patch" 2>&1)
NEW_EXIT=$?

if [ $NEW_EXIT -eq 0 ]; then
    pass_test "New file diff exits successfully"
else
    fail_test "New file diff exits successfully" "Exit code: $NEW_EXIT"
fi

if echo "$NEW_OUTPUT" | grep -q "Code Review Report"; then
    pass_test "New file diff produces report"
else
    fail_test "New file diff produces report"
fi

# ===========================================================================
# TEST 10: Deleted file diff (status: deleted)
# ===========================================================================
echo ""
echo "--- Test 10: Deleted file diff ---"

DEL_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/deleted_file.patch" 2>&1)
DEL_EXIT=$?

if [ $DEL_EXIT -eq 0 ]; then
    pass_test "Deleted file diff exits successfully"
else
    fail_test "Deleted file diff exits successfully" "Exit code: $DEL_EXIT"
fi

# ===========================================================================
# TEST 11: Renamed file diff (status: renamed)
# ===========================================================================
echo ""
echo "--- Test 11: Renamed file diff ---"

REN_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/renamed_file.patch" 2>&1)
REN_EXIT=$?

if [ $REN_EXIT -eq 0 ]; then
    pass_test "Renamed file diff exits successfully"
else
    fail_test "Renamed file diff exits successfully" "Exit code: $REN_EXIT"
fi

# ===========================================================================
# TEST 12: Multi-file diff (3 files, mixed statuses)
# ===========================================================================
echo ""
echo "--- Test 12: Multi-file diff (3 files) ---"

MULTI_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/multi_file.patch" \
    --output "$OUTPUT_DIR/test12_multi.md" 2>&1)
MULTI_EXIT=$?

if [ $MULTI_EXIT -eq 0 ]; then
    pass_test "Multi-file diff exits successfully"
else
    fail_test "Multi-file diff exits successfully" "Exit code: $MULTI_EXIT"
fi

if echo "$MULTI_OUTPUT" | grep -q "Code Review Report"; then
    pass_test "Multi-file diff produces report"
else
    fail_test "Multi-file diff produces report"
fi

# ===========================================================================
# TEST 13: Invalid --diff path (nonexistent file)
# ===========================================================================
echo ""
echo "--- Test 13: Invalid --diff path ---"

BAD_PATH_OUTPUT=$(uv run code-review-agent review \
    --diff /nonexistent/file.patch 2>&1) || true

if echo "$BAD_PATH_OUTPUT" | grep -qi "invalid\|not exist\|error\|no such"; then
    pass_test "Nonexistent file gives error"
else
    fail_test "Nonexistent file gives error" "Output: $BAD_PATH_OUTPUT"
fi

# ===========================================================================
# TEST 14: Both --pr and --diff (should fail)
# ===========================================================================
echo ""
echo "--- Test 14: Both --pr and --diff (expect error) ---"

BOTH_OUTPUT=$(uv run code-review-agent review \
    --pr "owner/repo#1" \
    --diff "$SAMPLES/standard.patch" 2>&1) || true

if echo "$BOTH_OUTPUT" | grep -qi "only one"; then
    pass_test "Both args gives 'only one' error"
else
    fail_test "Both args gives 'only one' error" "Output: $BOTH_OUTPUT"
fi

# ===========================================================================
# TEST 15: Invalid --pr format
# ===========================================================================
echo ""
echo "--- Test 15: Invalid --pr format ---"

BAD_PR_OUTPUT=$(uv run code-review-agent review --pr "not-a-valid-ref" 2>&1) || true

if echo "$BAD_PR_OUTPUT" | grep -qi "invalid\|error"; then
    pass_test "Invalid PR ref gives error"
else
    fail_test "Invalid PR ref gives error" "Output: $BAD_PR_OUTPUT"
fi

# ===========================================================================
# TEST 16: Missing API key (simulated)
# ===========================================================================
echo ""
echo "--- Test 16: Missing API key ---"

SAVED_KEY=$LLM_API_KEY
unset LLM_API_KEY

NOKEY_OUTPUT=$(uv run code-review-agent review \
    --diff "$SAMPLES/standard.patch" 2>&1) || true

export LLM_API_KEY=$SAVED_KEY

if echo "$NOKEY_OUTPUT" | grep -qi "LLM_API_KEY\|api.key\|required"; then
    pass_test "Missing API key gives helpful error"
else
    fail_test "Missing API key gives helpful error" "Output: $NOKEY_OUTPUT"
fi

# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "============================================"
echo " Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "============================================"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "Some tests failed. Check output above for details."
    exit 1
else
    echo ""
    echo "All tests passed!"
    exit 0
fi
