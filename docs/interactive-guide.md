# Interactive REPL Guide

The interactive mode provides a full-featured REPL with git operations, PR
management, code review, findings triage, configuration editing, review
history, and continuous file monitoring -- all without leaving the terminal.

## Launch

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

**Features:**
- Tab completion for all commands and sub-commands
- Persistent command history (saved to `~/.cra_history`)
- Status toolbar showing branch, repo, review count, tokens, tier, and cost
- Vi keybinding mode (set `INTERACTIVE_VI_MODE=true`)
- Shell escape with `!` prefix

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+A` | Open agent selector (multi-select, saved to database) |
| `Ctrl+P` | Open provider selector (single-select, saved to database) |
| `Ctrl+O` | Open repo selector (interactive picker) |
| `Ctrl+L` | Open git graph navigator |
| `Tab` | Autocomplete commands and arguments |
| `Ctrl+D` | Exit the REPL |
| `!<cmd>` | Run a shell command |

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

**After review:**
- Results are auto-saved to SQLite history
- Token usage is tracked in the session
- `session.last_review_report` is set (used by `findings` command)

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
cra> config reset                    # discard all session overrides
cra> config validate                 # check config for errors
cra> config save                     # persist session overrides to database
```

### `config edit`

Opens a full-screen interactive editor:

```
 Configuration Editor
  Up/Down navigate | Enter edit | Esc exit

  LLM
  --------------------------------------------------------
  > llm_provider         openrouter      [openrouter|nvidia|openai]
    llm_model            nvidia/nemotron...
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

Session overrides are active until `config reset` or session end.
Use `config save` to persist to the database (survives restarts).
You can also press `Ctrl+A` to quickly change agents or `Ctrl+P` to
change the LLM provider -- selections
are saved to the database automatically.

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

Lists all registered agents with type, priority, and description:

```
cra> agents

 Available Agents
  Name            Type        Pri  Description
  security        [built-in]    0  Specialized security reviewer
  performance     [built-in]    1  Specialized performance reviewer
  style           [built-in]    2  Specialized style reviewer
  test_coverage   [built-in]    3  Specialized test_coverage reviewer
  django_security [custom]     10  Django-specific security review
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
