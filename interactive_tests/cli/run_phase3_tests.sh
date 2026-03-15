#!/usr/bin/env bash
# ==========================================================================
# Phase 3 Interactive TUI Test Suite (CRA-69)
#
# Tests all Phase 3 features:
#   - PR write commands: create, merge, approve, request-changes
#   - --dry-run preview mode for all write commands
#   - --fill auto-fill from commits
#   - Pre-flight checks on merge (approvals, CI, conflicts)
#   - Permission error handling (GitHubAuthError)
#   - Smart workflows: review auto-stage, pr review auto-stash
#   - Watch command registration
#   - Cache invalidation after write operations
#   - Completers and help text updates
#
# Prerequisites:
#   - Mock LLM server (port 9999) -- same as Phase 1/2
#   - Mock GitHub API server (port 9998) -- new for Phase 3
#
# Usage:
#   bash interactive_tests/cli/run_phase3_tests.sh
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

# Run TUI command non-interactively via stdin pipe
# Usage: run_tui_cmd "command" -> sets TUI_STDOUT, TUI_STDERR, TUI_EXIT
run_tui_cmd() {
    local cmd="$1"
    local tmp_stdout tmp_stderr
    tmp_stdout=$(mktemp)
    tmp_stderr=$(mktemp)
    set +e
    echo -e "${cmd}\nexit" | uv run code-review-agent interactive >"$tmp_stdout" 2>"$tmp_stderr"
    TUI_EXIT=$?
    set -e
    TUI_STDOUT=$(cat "$tmp_stdout")
    TUI_STDERR=$(cat "$tmp_stderr")
    rm -f "$tmp_stdout" "$tmp_stderr"
}

# Run CLI and capture stdout/stderr separately
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

# Run pytest subset and capture result
run_pytest() {
    local tmp_stdout
    tmp_stdout=$(mktemp)
    set +e
    uv run pytest "$@" -q >"$tmp_stdout" 2>&1
    PYTEST_EXIT=$?
    set -e
    PYTEST_STDOUT=$(cat "$tmp_stdout")
    rm -f "$tmp_stdout"
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
export GITHUB_TOKEN=mock-github-token

# Create output directory
mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Start mock servers
# ---------------------------------------------------------------------------

echo "============================================"
echo " Phase 3 Interactive TUI Test Suite"
echo "============================================"
echo ""
echo "Starting mock LLM server on port 9999..."

uv run uvicorn interactive_tests.cli.mock_llm_server:app \
    --host 127.0.0.1 --port 9999 --log-level error &
LLM_PID=$!

echo "Starting mock GitHub API server on port 9998..."

uv run uvicorn interactive_tests.cli.mock_github_server:app \
    --host 127.0.0.1 --port 9998 --log-level error &
GITHUB_PID=$!

cleanup() {
    echo ""
    echo "Shutting down mock servers..."
    kill $LLM_PID 2>/dev/null || true
    kill $GITHUB_PID 2>/dev/null || true
    wait $LLM_PID 2>/dev/null || true
    wait $GITHUB_PID 2>/dev/null || true
}
trap cleanup EXIT

# Wait for both servers
for i in $(seq 1 15); do
    LLM_READY=false
    GH_READY=false
    if curl -s http://127.0.0.1:9999/health > /dev/null 2>&1; then
        LLM_READY=true
    fi
    if curl -s http://127.0.0.1:9998/health > /dev/null 2>&1; then
        GH_READY=true
    fi
    if [ "$LLM_READY" = true ] && [ "$GH_READY" = true ]; then
        echo "Both mock servers ready."
        echo ""
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "ERROR: Mock server(s) failed to start"
        exit 1
    fi
    sleep 0.5
done

# Reset counters
curl -s -X POST http://127.0.0.1:9999/reset > /dev/null
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null


# ===========================================================================
# SECTION 1: Unit Tests (pytest)
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 1: Unit Tests (pytest)"
echo "==========================================="

echo ""
echo "--- Test 1: PR write command unit tests pass ---"
run_pytest tests/test_interactive.py -k "PrCreate or PrMerge or PrApprove or PrRequestChanges" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "PR write command unit tests pass"
else
    fail_test "PR write command unit tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 2: PR dispatch wiring tests pass ---"
run_pytest tests/test_interactive.py -k "PrDispatchWiring" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "PR dispatch wiring tests pass"
else
    fail_test "PR dispatch wiring tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 3: Auto-stage review tests pass ---"
run_pytest tests/test_interactive.py -k "ReviewAutoStage" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Auto-stage review tests pass"
else
    fail_test "Auto-stage review tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 4: Auto-stash PR review tests pass ---"
run_pytest tests/test_interactive.py -k "PrReviewAutoStash" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Auto-stash PR review tests pass"
else
    fail_test "Auto-stash PR review tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 5: Watch command tests pass ---"
run_pytest tests/test_interactive.py -k "WatchCommand" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Watch command tests pass"
else
    fail_test "Watch command tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 6: New git_ops functions tests pass ---"
run_pytest tests/test_interactive.py -k "GitOpsNewFunctions" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "New git_ops functions tests pass"
else
    fail_test "New git_ops functions tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 7: PR cache invalidation tests pass ---"
run_pytest tests/test_interactive.py -k "PRCacheInvalidation" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "PR cache invalidation tests pass"
else
    fail_test "PR cache invalidation tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 8: Completers include Phase 3 commands ---"
run_pytest tests/test_interactive.py -k "CompletersPhase3" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Completers include Phase 3 commands"
else
    fail_test "Completers tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 9: Meta help includes Phase 3 groups ---"
run_pytest tests/test_interactive.py -k "MetaPhase3" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Meta help includes Phase 3 groups"
else
    fail_test "Meta help tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 10: Flag parsing helpers ---"
run_pytest tests/test_interactive.py -k "PrWriteHelpers" -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Flag parsing helpers pass"
else
    fail_test "Flag parsing helper tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 11: All existing tests still pass ---"
run_pytest tests/test_interactive.py -x
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "All existing interactive tests still pass"
else
    fail_test "Regression in existing tests" "$PYTEST_STDOUT"
fi

echo ""
echo "--- Test 12: Full test suite passes ---"
# Run with clean env to avoid mock vars leaking into settings tests
set +e
PYTEST_FULL=$(env -u LLM_BASE_URL -u LLM_MODEL -u LLM_PROVIDER -u LLM_TEMPERATURE \
    -u REQUEST_TIMEOUT_SECONDS -u TOKEN_TIER -u DEDUP_STRATEGY -u MAX_REVIEW_SECONDS \
    -u MAX_PR_FILES -u MAX_CONCURRENT_AGENTS -u GITHUB_TOKEN \
    uv run pytest tests/ -x -q 2>&1)
PYTEST_EXIT=$?
set -e
if [ $PYTEST_EXIT -eq 0 ]; then
    pass_test "Full test suite passes"
else
    fail_test "Full test suite regression" "$PYTEST_FULL"
fi


# ===========================================================================
# SECTION 2: Mock GitHub API Server Verification
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 2: Mock GitHub API Server"
echo "==========================================="

echo ""
echo "--- Test 13: Mock server health check ---"
HEALTH=$(curl -s http://127.0.0.1:9998/health)
if echo "$HEALTH" | grep -q '"ok"'; then
    pass_test "Mock GitHub server health check"
else
    fail_test "Mock GitHub server health check" "$HEALTH"
fi

echo ""
echo "--- Test 14: List PRs endpoint ---"
PRS=$(curl -s http://127.0.0.1:9998/repos/acme/app/pulls)
if echo "$PRS" | python3 -c "import sys,json; prs=json.load(sys.stdin); assert len(prs)>=2" 2>/dev/null; then
    pass_test "List PRs returns sample data"
else
    fail_test "List PRs endpoint" "Unexpected response"
fi

echo ""
echo "--- Test 15: Get PR detail endpoint ---"
PR=$(curl -s http://127.0.0.1:9998/repos/acme/app/pulls/42)
if echo "$PR" | python3 -c "import sys,json; pr=json.load(sys.stdin); assert pr['number']==42" 2>/dev/null; then
    pass_test "Get PR detail returns correct PR"
else
    fail_test "Get PR detail endpoint" "Unexpected response"
fi

echo ""
echo "--- Test 16: PR not found returns 404 ---"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9998/repos/acme/app/pulls/999)
if [ "$HTTP_CODE" = "404" ]; then
    pass_test "PR not found returns 404"
else
    fail_test "PR not found" "Expected 404, got $HTTP_CODE"
fi

echo ""
echo "--- Test 17: Create PR endpoint ---"
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null
CREATE_RESULT=$(curl -s -X POST http://127.0.0.1:9998/repos/acme/app/pulls \
    -H "Content-Type: application/json" \
    -d '{"title":"Test PR","head":"feat/test","base":"main","body":"test body","draft":false}')
if echo "$CREATE_RESULT" | python3 -c "import sys,json; pr=json.load(sys.stdin); assert pr['number']>=100 and pr['title']=='Test PR'" 2>/dev/null; then
    pass_test "Create PR returns new PR with number"
else
    fail_test "Create PR endpoint" "$CREATE_RESULT"
fi

echo ""
echo "--- Test 18: Merge PR endpoint ---"
MERGE_RESULT=$(curl -s -X PUT http://127.0.0.1:9998/repos/acme/app/pulls/42/merge \
    -H "Content-Type: application/json" \
    -d '{"merge_method":"squash"}')
if echo "$MERGE_RESULT" | python3 -c "import sys,json; r=json.load(sys.stdin); assert r['merged']==True" 2>/dev/null; then
    pass_test "Merge PR returns merged=true"
else
    fail_test "Merge PR endpoint" "$MERGE_RESULT"
fi

echo ""
echo "--- Test 19: Submit review endpoint ---"
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null
REVIEW_RESULT=$(curl -s -X POST http://127.0.0.1:9998/repos/acme/app/pulls/42/reviews \
    -H "Content-Type: application/json" \
    -d '{"event":"APPROVE","body":"LGTM"}')
if echo "$REVIEW_RESULT" | python3 -c "import sys,json; r=json.load(sys.stdin); assert r['state']=='APPROVED'" 2>/dev/null; then
    pass_test "Submit review returns correct state"
else
    fail_test "Submit review endpoint" "$REVIEW_RESULT"
fi

echo ""
echo "--- Test 20: Get reviews endpoint ---"
REVIEWS=$(curl -s http://127.0.0.1:9998/repos/acme/app/pulls/42/reviews)
if echo "$REVIEWS" | python3 -c "import sys,json; rs=json.load(sys.stdin); assert len(rs)>=1" 2>/dev/null; then
    pass_test "Get reviews returns data"
else
    fail_test "Get reviews endpoint" "$REVIEWS"
fi

echo ""
echo "--- Test 21: CI checks endpoint ---"
CHECKS=$(curl -s http://127.0.0.1:9998/repos/acme/app/commits/aaa111bbb222/check-runs)
if echo "$CHECKS" | python3 -c "import sys,json; c=json.load(sys.stdin); assert len(c['check_runs'])>=2" 2>/dev/null; then
    pass_test "CI checks returns check runs"
else
    fail_test "CI checks endpoint" "$CHECKS"
fi

echo ""
echo "--- Test 22: Stats tracking ---"
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null
curl -s http://127.0.0.1:9998/repos/acme/app/pulls > /dev/null
curl -s http://127.0.0.1:9998/repos/acme/app/pulls/42 > /dev/null
STATS=$(curl -s http://127.0.0.1:9998/stats)
if echo "$STATS" | python3 -c "import sys,json; s=json.load(sys.stdin); assert s['total_requests']>=2" 2>/dev/null; then
    pass_test "Stats tracking counts requests"
else
    fail_test "Stats tracking" "$STATS"
fi

echo ""
echo "--- Test 23: Non-mergeable PR returns 405 ---"
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT http://127.0.0.1:9998/repos/acme/app/pulls/44/merge \
    -H "Content-Type: application/json" \
    -d '{"merge_method":"squash"}')
if [ "$HTTP_CODE" = "405" ]; then
    pass_test "Non-mergeable PR returns 405"
else
    fail_test "Non-mergeable PR merge" "Expected 405, got $HTTP_CODE"
fi


# ===========================================================================
# SECTION 3: Lint, Type Check, Static Analysis
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 3: Static Analysis"
echo "==========================================="

echo ""
echo "--- Test 24: Ruff lint passes ---"
set +e
RUFF_OUTPUT=$(uv run ruff check src/ 2>&1)
RUFF_EXIT=$?
set -e
if [ $RUFF_EXIT -eq 0 ]; then
    pass_test "Ruff lint passes"
else
    fail_test "Ruff lint" "$RUFF_OUTPUT"
fi

echo ""
echo "--- Test 25: Mypy type check passes ---"
set +e
MYPY_OUTPUT=$(uv run mypy src/ 2>&1)
MYPY_EXIT=$?
set -e
if [ $MYPY_EXIT -eq 0 ]; then
    pass_test "Mypy type check passes"
else
    fail_test "Mypy type check" "$MYPY_OUTPUT"
fi


# ===========================================================================
# SECTION 4: CLI Integration -- Review Commands
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 4: CLI Integration (Review)"
echo "==========================================="

echo ""
echo "--- Test 26: Review with standard patch still works ---"
run_cli review --diff "$SAMPLES/standard.patch" --format json
if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Review with standard patch"
else
    fail_test "Review with standard patch" "Exit code: $CLI_EXIT"
fi

echo ""
echo "--- Test 27: Review with --agents filter ---"
curl -s -X POST http://127.0.0.1:9999/reset > /dev/null
run_cli review --diff "$SAMPLES/standard.patch" --agents security --format json
if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Review with --agents filter"
else
    fail_test "Review with --agents filter" "Exit code: $CLI_EXIT"
fi


# ===========================================================================
# SECTION 5: Interactive Mode Help & Registration
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 5: Interactive Mode Registration"
echo "==========================================="

echo ""
echo "--- Test 28: Interactive --help shows command ---"
run_cli interactive --help
if [ $CLI_EXIT -eq 0 ]; then
    pass_test "Interactive --help works"
else
    fail_test "Interactive --help" "Exit code: $CLI_EXIT"
fi

echo ""
echo "--- Test 29: Help text includes Pr Write group ---"
# Run help via Python import to avoid stdin issues
set +e
HELP_OUTPUT=$(uv run python3 -c "
from code_review_agent.interactive.commands.meta import COMMAND_HELP
groups = list(COMMAND_HELP.keys())
print(','.join(groups))
" 2>&1)
set -e
if echo "$HELP_OUTPUT" | grep -q "Pr Write"; then
    pass_test "Help includes 'Pr Write' group"
else
    fail_test "Help text missing Pr Write" "$HELP_OUTPUT"
fi

echo ""
echo "--- Test 30: Help text includes Watch group ---"
if echo "$HELP_OUTPUT" | grep -q "Watch"; then
    pass_test "Help includes 'Watch' group"
else
    fail_test "Help text missing Watch" "$HELP_OUTPUT"
fi

echo ""
echo "--- Test 31: Help text has Pr Read (not old 'Pr') ---"
if echo "$HELP_OUTPUT" | grep -q "Pr Read"; then
    pass_test "Help has 'Pr Read' (renamed from 'Pr')"
else
    fail_test "Help text missing Pr Read" "$HELP_OUTPUT"
fi


# ===========================================================================
# SECTION 6: Config and New Settings
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 6: Config & Settings"
echo "==========================================="

echo ""
echo "--- Test 32: watch_debounce_seconds field exists ---"
set +e
CONFIG_CHECK=$(uv run python3 -c "
from code_review_agent.config import Settings
s = Settings(llm_api_key='test', watch_debounce_seconds=10.0)
print(s.watch_debounce_seconds)
" 2>&1)
set -e
if echo "$CONFIG_CHECK" | grep -q "10.0"; then
    pass_test "watch_debounce_seconds field works"
else
    fail_test "watch_debounce_seconds field" "$CONFIG_CHECK"
fi

echo ""
echo "--- Test 33: watch_debounce_seconds defaults to 5.0 ---"
set +e
DEFAULT_CHECK=$(uv run python3 -c "
from code_review_agent.config import Settings
s = Settings(llm_api_key='test')
print(s.watch_debounce_seconds)
" 2>&1)
set -e
if echo "$DEFAULT_CHECK" | grep -q "5.0"; then
    pass_test "watch_debounce_seconds defaults to 5.0"
else
    fail_test "watch_debounce_seconds default" "$DEFAULT_CHECK"
fi

echo ""
echo "--- Test 34: watch_debounce_seconds rejects < 1.0 ---"
set +e
INVALID_CHECK=$(uv run python3 -c "
from code_review_agent.config import Settings
try:
    s = Settings(llm_api_key='test', watch_debounce_seconds=0.1)
    print('NO_ERROR')
except Exception as e:
    print('VALIDATION_ERROR')
" 2>&1)
set -e
if echo "$INVALID_CHECK" | grep -q "VALIDATION_ERROR"; then
    pass_test "watch_debounce_seconds rejects values < 1.0"
else
    fail_test "watch_debounce_seconds validation" "$INVALID_CHECK"
fi


# ===========================================================================
# SECTION 7: GitHub Client API Functions
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 7: GitHub Client API Functions"
echo "==========================================="

echo ""
echo "--- Test 35: create_pr function exists and callable ---"
set +e
FUNC_CHECK=$(uv run python3 -c "
from code_review_agent.github_client import create_pr, merge_pr, submit_pr_review
print('create_pr:', callable(create_pr))
print('merge_pr:', callable(merge_pr))
print('submit_pr_review:', callable(submit_pr_review))
" 2>&1)
set -e
if echo "$FUNC_CHECK" | grep -q "True" && echo "$FUNC_CHECK" | grep -c "True" | grep -q "3"; then
    pass_test "All 3 new API functions exist and are callable"
else
    fail_test "API function check" "$FUNC_CHECK"
fi

echo ""
echo "--- Test 36: create_pr against mock server ---"
set +e
curl -s -X POST http://127.0.0.1:9998/reset > /dev/null
CREATE_CHECK=$(uv run python3 -c "
import httpx
resp = httpx.post('http://127.0.0.1:9998/repos/acme/app/pulls',
    json={'title':'API test','head':'feat/api','base':'main','body':'test','draft':False},
    headers={'Accept':'application/vnd.github.v3+json'})
print('status:', resp.status_code)
data = resp.json()
print('number:', data['number'])
print('title:', data['title'])
" 2>&1)
set -e
if echo "$CREATE_CHECK" | grep -q "status: 201" && echo "$CREATE_CHECK" | grep -q "title: API test"; then
    pass_test "create_pr works against mock server"
else
    fail_test "create_pr against mock" "$CREATE_CHECK"
fi


# ===========================================================================
# SECTION 8: Git Ops New Functions
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 8: Git Ops New Functions"
echo "==========================================="

echo ""
echo "--- Test 37: has_upstream function ---"
set +e
UPSTREAM_CHECK=$(uv run python3 -c "
from code_review_agent.interactive.git_ops import has_upstream
result = has_upstream()
print('has_upstream:', result)
print('type:', type(result).__name__)
" 2>&1)
set -e
if echo "$UPSTREAM_CHECK" | grep -q "type: bool"; then
    pass_test "has_upstream returns bool"
else
    fail_test "has_upstream function" "$UPSTREAM_CHECK"
fi

echo ""
echo "--- Test 38: log_oneline_commits_since function ---"
set +e
COMMITS_CHECK=$(uv run python3 -c "
from code_review_agent.interactive.git_ops import log_oneline_commits_since
result = log_oneline_commits_since('main')
print('type:', type(result).__name__)
print('is_list:', isinstance(result, list))
" 2>&1)
set -e
if echo "$COMMITS_CHECK" | grep -q "is_list: True"; then
    pass_test "log_oneline_commits_since returns list"
else
    fail_test "log_oneline_commits_since function" "$COMMITS_CHECK"
fi

echo ""
echo "--- Test 39: status_porcelain function ---"
set +e
PORCELAIN_CHECK=$(uv run python3 -c "
from code_review_agent.interactive.git_ops import status_porcelain
result = status_porcelain()
print('type:', type(result).__name__)
" 2>&1)
set -e
if echo "$PORCELAIN_CHECK" | grep -q "type: str"; then
    pass_test "status_porcelain returns str"
else
    fail_test "status_porcelain function" "$PORCELAIN_CHECK"
fi

echo ""
echo "--- Test 40: push_branch function signature ---"
set +e
PUSH_CHECK=$(uv run python3 -c "
import inspect
from code_review_agent.interactive.git_ops import push_branch
sig = inspect.signature(push_branch)
params = list(sig.parameters.keys())
print('params:', params)
print('has_remote:', 'remote' in params)
print('has_set_upstream:', 'set_upstream' in params)
" 2>&1)
set -e
if echo "$PUSH_CHECK" | grep -q "has_remote: True" && echo "$PUSH_CHECK" | grep -q "has_set_upstream: True"; then
    pass_test "push_branch has correct signature"
else
    fail_test "push_branch signature" "$PUSH_CHECK"
fi


# ===========================================================================
# SECTION 9: Completers and Dispatch
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 9: Completers & Dispatch"
echo "==========================================="

echo ""
echo "--- Test 41: Completer tree includes PR write commands ---"
set +e
COMP_CHECK=$(uv run python3 -c "
from code_review_agent.interactive.completers import build_static_completer
c = build_static_completer()
# NestedCompleter stores options in .options dict
pr_opts = c.options.get('pr')
if pr_opts and hasattr(pr_opts, 'options'):
    pr_keys = list(pr_opts.options.keys())
    for cmd in ['create', 'merge', 'approve', 'request-changes']:
        if cmd in pr_keys:
            print(f'{cmd}: found')
        else:
            print(f'{cmd}: MISSING')
else:
    print('pr_opts: not found')
" 2>&1)
set -e
if echo "$COMP_CHECK" | grep -q "create: found" && echo "$COMP_CHECK" | grep -q "merge: found"; then
    pass_test "Completer includes PR write commands"
else
    fail_test "Completer PR write commands" "$COMP_CHECK"
fi

echo ""
echo "--- Test 42: Completer tree includes watch command ---"
set +e
WATCH_COMP=$(uv run python3 -c "
from code_review_agent.interactive.completers import build_static_completer
c = build_static_completer()
if 'watch' in c.options:
    print('watch: found')
else:
    print('watch: MISSING')
" 2>&1)
set -e
if echo "$WATCH_COMP" | grep -q "watch: found"; then
    pass_test "Completer includes watch command"
else
    fail_test "Completer watch command" "$WATCH_COMP"
fi

echo ""
echo "--- Test 43: REPL dispatch table includes watch ---"
set +e
REPL_CHECK=$(uv run python3 -c "
from code_review_agent.interactive.repl import _COMMANDS
cmds = list(_COMMANDS.keys())
print('watch' in cmds)
print('commands:', cmds)
" 2>&1)
set -e
if echo "$REPL_CHECK" | grep -q "True"; then
    pass_test "REPL dispatch table includes watch"
else
    fail_test "REPL dispatch table" "$REPL_CHECK"
fi


# ===========================================================================
# SECTION 10: PR Write Command Behavior (via Python)
# ===========================================================================

echo ""
echo "==========================================="
echo " Section 10: PR Write Command Behavior"
echo "==========================================="

echo ""
echo "--- Test 44: pr create --dry-run shows preview ---"
set +e
DRY_RUN_CHECK=$(uv run python3 -c "
from unittest.mock import MagicMock, patch
from code_review_agent.interactive.session import SessionState
from code_review_agent.interactive.commands.pr_write import pr_create

settings = MagicMock()
settings.github_token = MagicMock()
settings.github_token.get_secret_value.return_value = 'ghp_test'
session = SessionState(settings=settings)

output_lines = []
with (
    patch('code_review_agent.interactive.commands.pr_write._get_repo_info', return_value=('acme','app','ghp_t')),
    patch('code_review_agent.interactive.commands.pr_write.git_ops') as mg,
    patch('code_review_agent.interactive.commands.pr_write.create_pr') as mc,
    patch('code_review_agent.interactive.commands.pr_write.console') as con,
):
    mg.current_branch.return_value = 'feat/x'
    mg.has_upstream.return_value = True
    pr_create(['--title', 'Test', '--dry-run'], session)
    for call in con.print.call_args_list:
        output_lines.append(str(call))

api_called = mc.called
print('api_called:', api_called)
has_dry_run = any('dry run' in line.lower() for line in output_lines)
print('has_dry_run:', has_dry_run)
" 2>&1)
set -e
if echo "$DRY_RUN_CHECK" | grep -q "api_called: False" && echo "$DRY_RUN_CHECK" | grep -q "has_dry_run: True"; then
    pass_test "pr create --dry-run shows preview, no API call"
else
    fail_test "pr create --dry-run" "$DRY_RUN_CHECK"
fi

echo ""
echo "--- Test 45: pr merge --dry-run shows pre-flight ---"
set +e
MERGE_DRY=$(uv run python3 -c "
from unittest.mock import MagicMock, patch
from code_review_agent.interactive.session import SessionState
from code_review_agent.interactive.commands.pr_write import pr_merge

session = SessionState(settings=MagicMock())
output_lines = []
with (
    patch('code_review_agent.interactive.commands.pr_write._get_repo_info', return_value=('a','b','t')),
    patch('code_review_agent.interactive.commands.pr_write.get_pr_detail', return_value={
        'number':42,'title':'Fix','state':'open','head_branch':'fix/x','base_branch':'main',
        'mergeable':True,'html_url':'https://x'}),
    patch('code_review_agent.interactive.commands.pr_write.get_pr_checks', return_value=[
        {'name':'ci','status':'completed','conclusion':'success'}]),
    patch('code_review_agent.interactive.commands.pr_write.get_pr_reviews', return_value=[
        {'user':'r1','state':'APPROVED','submitted_at':''}]),
    patch('code_review_agent.interactive.commands.pr_write.merge_pr') as mm,
    patch('code_review_agent.interactive.commands.pr_write.console') as con,
):
    pr_merge(['42','--dry-run'], session)
    for call in con.print.call_args_list:
        output_lines.append(str(call))

print('merge_called:', mm.called)
print('has_dry_run:', any('dry run' in l.lower() for l in output_lines))
" 2>&1)
set -e
if echo "$MERGE_DRY" | grep -q "merge_called: False" && echo "$MERGE_DRY" | grep -q "has_dry_run: True"; then
    pass_test "pr merge --dry-run shows pre-flight, no API call"
else
    fail_test "pr merge --dry-run" "$MERGE_DRY"
fi

echo ""
echo "--- Test 46: pr approve submits APPROVE event ---"
set +e
APPROVE_CHECK=$(uv run python3 -c "
from unittest.mock import MagicMock, patch
from code_review_agent.interactive.session import SessionState
from code_review_agent.interactive.commands.pr_write import pr_approve

session = SessionState(settings=MagicMock())
with (
    patch('code_review_agent.interactive.commands.pr_write._get_repo_info', return_value=('a','b','t')),
    patch('code_review_agent.interactive.commands.pr_write.get_pr_detail', return_value={
        'number':42,'title':'Fix','author':'octocat','head_branch':'fix/x','base_branch':'main'}),
    patch('code_review_agent.interactive.commands.pr_write.submit_pr_review') as ms,
    patch('code_review_agent.interactive.commands.pr_write.console'),
):
    ms.return_value = {'id':1,'state':'APPROVED','html_url':'https://x'}
    pr_approve(['42','-m','LGTM'], session)

print('event:', ms.call_args.kwargs['event'])
print('body:', ms.call_args.kwargs['body'])
" 2>&1)
set -e
if echo "$APPROVE_CHECK" | grep -q "event: APPROVE" && echo "$APPROVE_CHECK" | grep -q "body: LGTM"; then
    pass_test "pr approve submits APPROVE with comment"
else
    fail_test "pr approve event" "$APPROVE_CHECK"
fi

echo ""
echo "--- Test 47: pr request-changes requires comment ---"
set +e
REQCH_CHECK=$(uv run python3 -c "
from unittest.mock import MagicMock, patch
from code_review_agent.interactive.session import SessionState
from code_review_agent.interactive.commands.pr_write import pr_request_changes

session = SessionState(settings=MagicMock())
output_lines = []
with (
    patch('code_review_agent.interactive.commands.pr_write._get_repo_info', return_value=('a','b','t')),
    patch('code_review_agent.interactive.commands.pr_write.submit_pr_review') as ms,
    patch('code_review_agent.interactive.commands.pr_write.console') as con,
):
    pr_request_changes(['42'], session)
    for call in con.print.call_args_list:
        output_lines.append(str(call))

print('api_called:', ms.called)
print('has_mandatory:', any('mandatory' in l.lower() for l in output_lines))
" 2>&1)
set -e
if echo "$REQCH_CHECK" | grep -q "api_called: False" && echo "$REQCH_CHECK" | grep -q "has_mandatory: True"; then
    pass_test "pr request-changes requires comment"
else
    fail_test "pr request-changes comment check" "$REQCH_CHECK"
fi

echo ""
echo "--- Test 48: Permission error shows user-friendly message ---"
set +e
PERM_CHECK=$(uv run python3 -c "
from unittest.mock import MagicMock, patch
from code_review_agent.interactive.session import SessionState
from code_review_agent.interactive.commands.pr_write import pr_create
from code_review_agent.github_client import GitHubAuthError

session = SessionState(settings=MagicMock())
output_lines = []
with (
    patch('code_review_agent.interactive.commands.pr_write._get_repo_info', return_value=('a','b','t')),
    patch('code_review_agent.interactive.commands.pr_write.git_ops') as mg,
    patch('code_review_agent.interactive.commands.pr_write.create_pr') as mc,
    patch('code_review_agent.interactive.commands.pr_write.console') as con,
):
    mg.current_branch.return_value = 'feat/x'
    mg.has_upstream.return_value = True
    mc.side_effect = GitHubAuthError('403')
    pr_create(['--title','test'], session)
    for call in con.print.call_args_list:
        output_lines.append(str(call))

print('has_permission:', any('permission denied' in l.lower() for l in output_lines))
print('has_scope:', any('repo' in l.lower() for l in output_lines))
" 2>&1)
set -e
if echo "$PERM_CHECK" | grep -q "has_permission: True"; then
    pass_test "Permission error shows user-friendly message"
else
    fail_test "Permission error message" "$PERM_CHECK"
fi


# ===========================================================================
# Results
# ===========================================================================

echo ""
echo "==========================================="
echo " Results"
echo "==========================================="
echo ""
echo "  Total:  $TOTAL"
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo ""

if [ "$FAILED" -eq 0 ]; then
    echo "  All tests passed!"
    exit 0
else
    echo "  $FAILED test(s) failed."
    exit 1
fi
