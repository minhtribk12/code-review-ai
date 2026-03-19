# Interactive TUI Guide

The interactive TUI (Terminal User Interface) is the recommended way to use Code Review AI. It provides a full-featured terminal interface with git operations, PR management, code review, findings triage, API key management, provider/model browsing, agent management, configuration editing, review history, and continuous file monitoring -- all without leaving the terminal.

## Launch

```bash
cra interactive
```

```
  Code Review AI v0.1.8
  Multi-agent code review powered by LLM.
  Analyzes security, performance, style, and test coverage.

  ────────────────────────────────────────────────────────────────────────
  Quick start:

    review                    Review working tree changes
    review --diff <file>      Review a local diff or patch file
    repo select <owner/repo>  Set the active GitHub repository
    pr list                   List open pull requests
    pr review <number>        Review a PR (fetches diff from GitHub)

  Tools:

    findings                  Browse, triage, and post findings
    config edit               Open the interactive config editor
    help                      Show all available commands

  ────────────────────────────────────────────────────────────────────────
  Tab autocomplete  Ctrl+A agents  Ctrl+P provider  Ctrl+O repo  Ctrl+L graph  Ctrl+D exit

cra> _
────────────────────────────────────────────────────────────────────────
 Branch: main | Repo: acme/app:local | Reviews: 0 | Tokens: 0 | Tier: free
```

**Features:**
- **Tab completion** for all commands and sub-commands
- **Persistent command history** (saved to `~/.cra_history`)
- **Status toolbar** showing branch, repo, review count, tokens, tier, and cost
- **Keyboard shortcuts** for quick access to agents, providers, repos, and git graph
- **Background reviews** -- prompt stays active while review runs
- **Vi keybinding mode** (set `INTERACTIVE_VI_MODE=true`)
- **Shell escape** with `!` prefix

### First Launch

On the first launch (or when no provider has an API key configured), a **provider setup panel** appears:

```
 LLM Provider Setup

  No LLM provider is configured.
  Select a provider and press Enter to input your API key.

 > nvidia (no key)       https://integrate.api.nvidia.com/v1
   openrouter (no key)   https://openrouter.ai/api/v1

  Up/Down navigate, Enter input key, c continue, q quit
```

**How it works:**
- Navigate with Up/Down arrows
- Press Enter to input your API key (paste supported, display is masked)
- Local LLM servers (localhost, 127.x, 192.168.x, etc.) show `(local server)` and don't need keys
- After entering at least one key, press `c` to continue to the REPL
- Keys are saved to `~/.cra/secrets.env` and persist across restarts

Once at least one provider is configured, the panel shows:
```
  At least one provider is ready.
  Press c to continue, or configure more providers.

 > nvidia (key set)      https://integrate.api.nvidia.com/v1
   openrouter (no key)   https://openrouter.ai/api/v1
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+A` | Open agent selector (multi-select, saved to config.yaml) |
| `Ctrl+P` | Open provider selector (auto-updates model and base URL) |
| `Ctrl+O` | Open repo selector (interactive picker) |
| `Ctrl+L` | Open git graph navigator |
| `Tab` | Autocomplete commands and arguments |
| `Ctrl+D` | Exit the REPL |
| `!<cmd>` | Run a shell command |

### Structured Error Messages

All errors display a consistent three-part structure:

```
+--- Error ------------------------------------------------+
| LLM API authentication failed                            |
|                                                          |
|   Reason: The API key is invalid, expired, or not set.   |
|                                                          |
|   Fix:    Check your API key with 'config get            |
|           llm_api_key'. Set it with 'config set          |
|           llm_api_key <key>' or in your .env file.       |
+----------------------------------------------------------+
```

- **Detail** (red) -- what happened
- **Reason** (dim) -- why it happened
- **Fix** (highlighted) -- actionable steps to resolve it

### Connection Testing

On startup (and after changing LLM provider/model), a quick connection
test verifies the LLM is reachable:

```
  OK LLM connection: Connected to nvidia/nemotron-3-super-120b-a12b
```

If the test fails, the provider or model is marked as `(not working)` and
you are offered the option to remove or switch. Connection health is
persisted in `~/.cra/config.yaml` and shown in the provider browser.

---

## Git Read Commands

Read-only git operations for inspecting the working tree.

### `status`

Show current branch, staged changes, and modified files.

```
cra> status
```

### `diff`

Show diff content. Multiple modes supported.

```
cra> diff                    # unstaged changes
cra> diff staged             # staged changes only
cra> diff HEAD~3             # diff against N commits back
cra> diff HEAD^              # diff against parent commit
cra> diff main..feat/login   # diff between branches
cra> diff src/auth.py        # diff for a single file
```

### `log`

Show compact commit log.

```
cra> log                     # last 20 commits
cra> log -n 5               # last 5 commits
cra> log feat/login          # log for specific branch
```

### `show`

Show full commit details including diff.

```
cra> show abc1234            # show commit by hash
cra> show HEAD               # show latest commit
```

---

## Git Write Commands

Commands that modify the working tree, index, or repository.

### `branch`

Branch management.

```
cra> branch                          # list local branches
cra> branch -r                       # list remote branches
cra> branch switch feat/login        # switch to branch (blocks if dirty)
cra> branch create feat/new          # create and switch to new branch
cra> branch create feat/new main     # create from specific ref
cra> branch delete feat/old          # delete branch (blocks if unmerged)
cra> branch delete feat/old --force  # force delete
cra> branch rename old-name new-name # rename branch
```

### `add`

Stage files for commit.

```
cra> add src/main.py         # stage specific file
cra> add .                   # stage all changes (shows file count)
```

### `unstage`

Remove files from the staging area.

```
cra> unstage src/main.py     # unstage specific file
```

### `commit`

Create a git commit from staged changes.

```
cra> commit -m "fix: resolve login bug"
```

Warns if nothing is staged. Refuses to commit with an empty message.

### `stash`

Stash management.

```
cra> stash                   # stash working changes
cra> stash pop               # restore most recent stash
cra> stash list              # list all stashes
```

### `cd`

Change the working directory. Tab completes directory paths.

```
cra> cd ~/projects/other-repo   # change to another directory
cra> cd                         # go to home directory
cra> cd -                       # go to previous directory
```

When switching directories, the local active repo is cleared automatically
(since the git repository has changed). Use `repo select` or `Ctrl+O` to
set the new repo.

---

## Code Review

### `review`

Run code review on a diff from the current git context.

```
cra> review                          # auto-detects diff (see below)
cra> review staged                   # review staged changes only
cra> review HEAD~1                   # review last commit
cra> review HEAD~3                   # review last 3 commits
cra> review main..feat/login         # review branch diff
cra> review src/auth.py              # review single file diff
```

**Flags:**

```
cra> review --agents security               # single agent
cra> review --agents security,performance   # multiple agents
cra> review --format json                   # JSON output
cra> review staged --agents security --format json
```

**Auto-stage behavior:** When `review` is run with no arguments:
1. If unstaged changes exist, reviews them
2. If only staged changes exist, reviews those
3. If neither, auto-stages all changed files, reviews the staged diff, then
   unstages -- so you always get a review without manual staging

**Background review:** In interactive mode, reviews run in the background.
The prompt stays active so you can run read-only commands (`status`, `diff`,
`log`, `config`, etc.) while the review runs. Write commands are queued
and executed after the review completes.

**Progress display:** The status toolbar shows live review progress:

```
  security         >> running..   3.2s
  performance      OK done        2.1s
  style               waiting
  test_coverage    >> running.    1.8s
```

**After review:**
- Results are auto-saved to SQLite history
- Token usage is tracked in the session and toolbar
- `findings` command opens the navigator for the latest review
- Queued commands are offered for execution

---

## Findings Navigator

### `findings`

Opens a full-screen interactive navigator for triaging review findings.

```
cra> findings                # navigate last review's findings
cra> findings 42             # navigate findings from saved review #42
```

### Layout

```
+--------------------------------------------------------------+
| Findings Navigator  (Up/Down, Enter detail, f filter, q quit)|
| 3/3 findings | sort: severity                                |
+--------------------------------------------------------------+
|    Sev   Agent        File:Line                    Title      |
|    ---------------------------------------------------------- |
| >  CRIT  security     src/auth.py:12       SQL injection      |
|    MED   performance  src/cache.py:4       Unbounded cache    |
|    LOW   security     src/auth.py:25       Stack trace leak   |
+--------------------------------------------------------------+
|    ====================================================      |
|    SQL injection in login                                     |
|    Agent: security | Severity: CRITICAL | Confidence: high    |
|    File: src/auth.py:12                                       |
|    Description: f-string interpolation in SQL query...        |
|    Suggestion: Use parameterized queries.                     |
+--------------------------------------------------------------+
```

### Key Bindings

| Key | Action |
|-----|--------|
| Up/Down | Navigate findings list |
| Enter/Space | Toggle detail panel |
| `f` | Open filter modal (severity, agent checkboxes) |
| `s` | Sort forward (reverse current, then next column) |
| `S` | Sort backward (reverse current, then previous column) |
| `m` | Mark/unmark finding as false positive |
| `i` | Ignore/unignore finding |
| `p` | Stage/unstage finding for PR posting |
| `P` (Shift+P) | Submit all staged findings as PR review comments |
| `D` | Delete posted PR review comments |
| `?` | Show help overlay |
| `q` / Escape | Quit back to REPL |

### Filter Modal

Press `f` to open:
- Checkboxes for severity levels (critical, high, medium, low)
- Checkboxes for each agent in the review
- Tab to confirm, Escape to cancel
- Findings list updates immediately

### PR Posting

Findings can be posted as inline code review comments on the associated PR:

1. Press `p` on individual findings to stage them (shows `[PR]` indicator)
2. Press `P` to submit all staged findings as a batch
3. Findings with `file_path` + `line_number` become inline comments on the diff
4. Findings without location are included in the review body

**Requirements:**
- The review must be from a PR (not a local diff)
- `GITHUB_TOKEN` must be configured
- The token must have write access to the repository

### Triage Actions

| Action | Key | Indicator | Effect |
|--------|-----|-----------|--------|
| False positive | `m` | `[FP]` | Dims the row, marks as false positive |
| Ignore | `i` | `[IGN]` | Dims the row, marks as ignored |
| Stage for PR | `p` | `[PR]` | Queues for batch posting |

Triage state is persisted to the SQLite database (`~/.cra/reviews.db`),
so actions survive across sessions. Second press toggles the action off.

---

## PR Commands

All PR commands operate on the active repository. Set it with `repo select`
or let it auto-detect from git remotes.

### Read

```
cra> pr list                         # list open PRs
cra> pr list --state closed          # list closed PRs
cra> pr list --state all             # all PRs
cra> pr show 42                      # PR details (title, author, labels, stats)
cra> pr diff 42                      # PR diff with syntax highlighting
cra> pr checks 42                    # CI/CD check status (pass/fail/pending)
cra> pr checkout 42                  # fetch and switch to PR branch locally
```

### Review a PR

```
cra> pr review 42                    # run full code review on PR
cra> pr review 42 --agents security  # review with specific agents
```

**Auto-stash behavior:** If the working tree is dirty, `pr review`
automatically stashes changes before fetching the PR diff, then pops
the stash after the review completes.

### Write

```
cra> pr create --title "Add auth" --body "Adds login flow"
cra> pr create --fill                # auto-fill title/body from commits
cra> pr create --fill --draft        # create as draft PR
cra> pr create --fill --base dev     # target a different base branch
cra> pr create --fill --dry-run      # preview without creating

cra> pr merge 42                     # merge with pre-flight checks
cra> pr merge 42 --strategy squash   # squash merge
cra> pr merge 42 --strategy rebase   # rebase merge
cra> pr merge 42 --dry-run           # preview checks only

cra> pr approve 42                   # approve PR
cra> pr approve 42 -m "LGTM"        # approve with comment
cra> pr approve 42 --dry-run         # preview without submitting

cra> pr request-changes 42 -m "Fix the SQL injection on line 15"
cra> pr request-changes 42 --dry-run
```

**`pr create --fill`** generates:
- Title from the first commit message on the branch
- Body from all commit messages, formatted as a list

**`pr merge`** runs pre-flight checks before merging:
- Approval status
- CI check status
- Merge conflict detection

### Workflow Helpers

Dashboard-style queries for PR triage:

```
cra> pr mine                         # your open PRs
cra> pr assigned                     # PRs where you are a reviewer
cra> pr assigned --limit 20          # increase result limit
cra> pr stale                        # PRs with no activity (default: 7 days)
cra> pr stale --days 14              # custom threshold
cra> pr ready                        # PRs ready to merge (approved + CI green)
cra> pr conflicts                    # PRs with merge conflicts
cra> pr summary                      # dashboard: open count, stale, ready
cra> pr summary --full               # detailed counts per category
cra> pr unresolved                   # PRs with unresolved review feedback
```

---

## Repository Management

### `repo`

Switch between repositories for PR commands.

```
cra> repo list                       # list local + remote repos
cra> repo list --limit 50            # fetch more remote repos
cra> repo select                     # interactive full-screen picker
cra> repo select acme/api            # select by name directly
cra> repo current                    # show active repo and source
cra> repo clear                      # clear selection, use local git remote
```

**`repo select`** without arguments opens a full-screen picker:

```
 Select Repository
  Up/Down to navigate, Enter to select, Esc to cancel

 > (*) acme/app:local  (Python | public | Main web application)
   ( ) acme/api:remote  (Go | private | REST API service)
   ( ) acme/docs:remote  (MDX | public | Documentation site)
```

**Sources:**
- `:local` -- parsed from git remotes (origin, upstream, etc.)
- `:remote` -- fetched from GitHub API (your repos + collaborator access)

The active repo and its source are shown in the status toolbar.

---

## Configuration

### `config`

View and modify settings at runtime.

```
cra> config                          # show all settings (grouped, secrets masked)
cra> config llm                      # show LLM settings only
cra> config github                   # show GitHub settings only
cra> config budget                   # show token budget settings
cra> config review                   # show review settings
cra> config get llm_model            # get a single value
cra> config set llm_temperature 0.3  # set for this session only
cra> config set token_tier premium   # change tier mid-session
cra> config diff                     # show session overrides vs .env
cra> config reset                    # reload from .env (preserves API keys)
cra> config factory-reset            # full reset (clears history, keeps keys)
cra> config clean                    # remove all tool-generated files from ~/.cra/
cra> config validate                 # check config for errors
cra> config save                     # persist session overrides to config.yaml
```

### `config edit`

Opens a full-screen interactive editor:

```
 Configuration Editor
  Up/Down navigate | Enter edit | Esc exit

  LLM
  --------------------------------------------------------
  > llm_provider         nvidia          [nvidia|openrouter]
    llm_model            nvidia/nemotron...
    llm_api_key          nvap****HsL2
    llm_temperature      0.1
    ...

  Review
  --------------------------------------------------------
    dedup_strategy       exact           [exact|location|similar|disabled]
    max_review_seconds   300
    ...
```

**Controls:**
- Arrow keys to navigate between fields
- Enter/Space to start editing
- Left/Right arrows to cycle enum options
- Enter to confirm (validates input)
- Esc to cancel edit or exit editor
- Invalid input shows an error, keeps old value
- Paste supported: paste text from clipboard directly into text fields

Session overrides are active until `config reset` or session end.
Use `config save` to persist to `~/.cra/config.yaml` (survives restarts).
You can also press `Ctrl+A` to quickly change agents or `Ctrl+P` to
change the LLM provider -- it cascades model and base_url changes, and selections
are saved to `~/.cra/config.yaml` automatically.

### `config keys`

Opens a full-screen API key manager for viewing, editing, syncing, and deleting API keys across `secrets.env` and `.env`.

```
 API Key Manager  (Arrows navigate, Enter edit, s sync, d delete, q quit)

   Provider            >secrets.env             .env Key
   ────────────────────────────────────────────────────────────────
 > nvidia              [nvap****HsL2          ] nvap****HsL2
   openrouter           sk-o****3c77            sk-o****3c77
   ollama               --                      --

  3 providers
```

**Navigation:**
- Up/Down to select a provider row
- Left/Right to select a column (`secrets.env` or `.env`) -- the selected column header shows `>` and the active cell is highlighted with `[brackets]`

**Actions:**
- **Enter** -- edit the key in the selected cell. Opens an inline editor with masked display and paste support. Enter to save, Esc to cancel.
- **s** -- sync the key between columns. Opens a popup to choose direction (secrets.env -> .env or .env -> secrets.env).
- **d** -- delete the key from the selected column (with confirmation).
- **q / Esc** -- exit the key manager.

Values update immediately after edit, sync, or delete.

### `config reset`

Discards all session overrides and clears persisted config from `~/.cra/config.yaml`. Reloads settings from `.env`. **API keys and health marks are preserved.**

### `config factory-reset`

Full factory reset with confirmation. Clears all config overrides, health marks, and review history (findings, agent results, reviews). **API keys are preserved** so you don't have to re-enter them.

```
cra> config factory-reset

  Factory Reset

  Will clear:
    x All config overrides
    x All health marks (not working status)
    x All review history and findings

  Preserved:
    > API keys for all providers

  Type 'reset' to confirm:
```

### `config clean`

Removes all tool-generated files from `~/.cra/` and related paths. Opens a full-screen confirmation panel showing every managed file with its size.

**Removed:**
- `~/.cra/config.yaml`, `~/.cra/secrets.env`, `~/.cra/providers.yaml`
- `~/.cra/reviews.db` (+ WAL/SHM files)
- `~/.cra/agents/` directory
- `~/.cra_history`
- `~/.cra/providers.json` (legacy)
- `~/.cra/` directory itself if empty after cleanup

**Preserved:**
- Project `.env` file (user-managed). A warning is displayed if it exists.

Press `y` to confirm, `q` or Escape to cancel. This is a destructive operation that cannot be undone -- all API keys, config, history, and custom agents are permanently deleted.

---

## Provider Management

### `provider` / `pv`

Opens the full-screen provider/model browser. This is the primary interface for managing LLM providers and their models.

```
cra> provider
cra> pv                              # shortcut alias
```

#### Provider Browser Layout

```
 Provider Browser  (Up/Down navigate, Enter expand, a add provider, m add model, d delete, i edit, q quit)

 > v nvidia  [built-in]  https://integrate.api.nvidia.com/v1  (5 models)
       nvidia/nemotron-3-super-120b-a12b  (Nemotron 3 Super 120B free, 1,000,000 ctx)
       nvidia/nemotron-3-nano-30b-a3b  (Nemotron 3 Nano 30B free, 1,000,000 ctx)
       nvidia/llama-3.3-nemotron-super-49b-v1.5  (Llama 3.3 Nemotron Super 49B free, 131,072 ctx)
   > openrouter  [built-in]  https://openrouter.ai/api/v1  (6 models)
   > ollama  [custom]  http://localhost:11434/v1  (1 models)

  3 items | 1 expanded
```

#### Key Bindings

| Key | Action |
|-----|--------|
| Up/Down, j/k | Navigate providers and models |
| Enter | Expand/collapse provider to show models |
| `a` | Add a new provider (multi-step wizard) |
| `m` | Add a model to the selected provider |
| `d` | Delete selected provider or model (custom only, with confirmation) |
| `i` | Edit selected provider or model (opens field selector) |
| `q` / Esc | Exit browser |

#### Editing Fields

Press `i` on any provider or model (including built-in ones) to open the field selector:

**Provider fields:**
```
  Edit: nvidia
  Select a field to edit:

 > base_url             https://integrate.api.nvidia.com/v1
   default_model        nvidia/nemotron-3-super-120b-a12b
   rate_limit_rpm       40

  Up/Down navigate, Enter edit, Esc cancel
```

**Model fields:**
```
  Edit: nvidia/nemotron-3-super-120b-a12b
  Select a field to edit:

 > name                 Nemotron 3 Super 120B (MoE, 12B active)
   is_free              true
   context_window       1000000

  Up/Down navigate, Enter edit, Esc cancel
```

Edits to built-in providers are saved as user overrides in `~/.cra/providers.yaml` and merged on top of bundled defaults.

#### Adding a Provider

Press `a` in the browser to start the add wizard. The wizard prompts for:
1. Provider name
2. Base URL (validated: must start with http:// or https://)
3. API key (saved securely, masked in display)
4. Rate limit (requests per minute)

After saving, expand the new provider and press `m` to add models.

#### Adding a Model

Press `m` while a provider row is selected. The wizard prompts for:
1. Model name (the exact API identifier sent to the LLM server)
2. Display label (shown in selectors)
3. Is free? (yes/no)
4. Context window (tokens)

#### Deleting

Press `d` on a custom provider or model. A confirmation prompt appears:
```
  Delete provider 'ollama'?

  Press y to confirm, n/Esc to cancel
```

Built-in providers (nvidia, openrouter) cannot be deleted, only edited.

#### Health Status

Providers and models that failed a connection test show `(not working)` in red:
```
   openrouter (not working)  https://openrouter.ai/api/v1  (6 models)
```

This status is cleared automatically when a successful connection test runs.

### Other Provider Commands

```
cra> provider add                    # add provider via text wizard
cra> provider list                   # table view of all providers
cra> provider models nvidia          # list models for a specific provider
cra> provider remove my-custom       # remove a user-defined provider
```

### Provider Data Storage

- **Bundled providers:** `<package>/provider_registry.yaml` (ships with install, read-only)
- **User overrides:** `~/.cra/providers.yaml` (your additions and edits, merged on top)
- **API keys:** Stored in `~/.cra/secrets.env` (persists across restarts)

---

## Review History

### `history`

Browse and query past reviews stored in SQLite (`~/.cra/reviews.db`).

```
cra> history                         # last 20 reviews
cra> history --repo acme/app         # filter by repository
cra> history --days 30               # last 30 days
cra> history --limit 50              # more results
cra> history show 42                 # full detail for review #42
cra> history trends                  # aggregated stats
cra> history trends --days 7         # last week's trends
cra> history trends --repo acme/app  # trends for specific repo
cra> history export                  # export all reviews as JSON
cra> history export --repo acme/app  # export filtered
```

**`history trends`** shows:
- Total reviews, total findings, average findings per review
- Finding distribution by severity
- Cost trends over time
- Most-reviewed repositories

---

## Session Usage

### `usage`

Show current session statistics.

```
cra> usage
```

Displays:
- Total reviews completed this session
- Total tokens used (prompt + completion)
- Total LLM calls
- Estimated cost (USD)
- Per-agent breakdown (tokens, calls, cost)
- Time window stats (last hour, day, week)

---

## Watch Mode

### `watch`

Continuously monitors the working tree and auto-reviews when changes are
detected.

```
cra> watch                           # poll every 5s (default)
cra> watch --interval 10             # custom interval in seconds
cra> watch --agents security         # review with specific agents
cra> watch --agents security --format json
```

**Behavior:**
1. Captures initial `git status` snapshot
2. Polls at the configured interval
3. When status changes, captures the diff and runs a review
4. Shows changed file count before each review
5. Press Ctrl+C to stop

---

## Meta Commands

```
cra> help                            # show all command groups
cra> help pr                         # help for a specific group
cra> help review                     # help for a specific command
cra> agents                          # list all agents (built-in + custom)
cra> version                         # show version
cra> clear                           # clear screen
cra> !ls -la                         # run shell command
cra> !git remote -v                  # any shell command works
cra> exit                            # exit (or Ctrl+D)
```

### `agents`

Opens the full-screen agent browser for viewing, creating, editing, and deleting review agents.

```
cra> agents
```

#### Agent Browser Layout

```
 Agent Browser  (Up/Down navigate, Enter expand, a add, i edit, d delete, q quit)

 > v security  [built-in]  pri:0  Specialized security reviewer
       System prompt: You are a senior security engineer...
       File patterns: *
   > performance  [built-in]  pri:1  Specialized performance reviewer
   > style  [built-in]  pri:2  Specialized style reviewer
   > test_coverage  [built-in]  pri:3  Specialized test_coverage reviewer
   > django_security  [custom]  pri:10  Django-specific security review

  5 agents
```

#### Key Bindings

| Key | Action |
|-----|--------|
| Up/Down, j/k | Navigate agents |
| Enter | Expand/collapse agent to show details |
| `a` | Create a new custom agent (YAML) |
| `i` | Edit agent fields (name, description, prompt, priority, file patterns) |
| `d` | Delete a custom agent (with confirmation) |
| `q` / Esc | Exit browser |

#### Agent Commands

```
cra> agents list                    # table view of all agents
```

---

## Status Toolbar

The bottom toolbar shows session state at a glance:

```
 Branch: feat/login | Repo: acme/app:local | Reviews: 3 | Tokens: 12.4k | Tier: standard
```

| Field | Description |
|-------|-------------|
| Branch | Current git branch |
| Repo | Active repository and source (`:local` or `:remote`) |
| Reviews | Number of reviews completed this session |
| Tokens | Cumulative tokens used this session |
| Tier | Active token tier |

When cost-increasing overrides are active (`max_deepening_rounds > 1` or
`is_validation_enabled = true`), a `!` indicator appears after the tier.

---

## Common Workflows

### Review local changes before committing

```
cra> status                          # check what's changed
cra> diff                            # inspect the diff
cra> review                          # auto-review working tree
cra> findings                        # triage findings
cra> add .                           # stage changes
cra> commit -m "fix: resolve auth bug"
```

### Review a PR and post feedback

```
cra> repo select acme/app            # set active repo
cra> pr list                         # find the PR
cra> pr review 42                    # run code review
cra> findings                        # open navigator
  p  (stage critical findings)
  P  (post all staged as PR comments)
cra> pr approve 42 -m "LGTM, minor issues noted inline"
```

### Monitor a branch during development

```
cra> branch switch feat/auth
cra> watch --interval 15 --agents security
  (writes code, saves files...)
  (review runs automatically on each change)
  Ctrl+C                             # stop when done
```

### Compare review quality over time

```
cra> history trends --days 30
cra> history trends --repo acme/app --days 7
cra> usage                           # session cost so far
```
