"""Git subprocess wrappers with structured output parsing."""

from __future__ import annotations

import subprocess

import structlog

logger = structlog.get_logger(__name__)

_GIT = "git"


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, command: str, stderr: str) -> None:
        self.command = command
        self.stderr = stderr
        super().__init__(f"git {command} failed: {stderr.strip()}")


def _run(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = [_GIT, *args]
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise GitError(args[0] if args else "unknown", result.stderr)
    return result


def is_git_repo() -> bool:
    """Check if the current directory is inside a git repository."""
    result = _run("rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def current_branch() -> str:
    """Return the current branch name."""
    return _run("branch", "--show-current").stdout.strip()


def status_short() -> str:
    """Return git status in short format."""
    return _run("status", "--short", "--branch").stdout


def diff(*, staged: bool = False, file_path: str | None = None) -> str:
    """Return git diff output."""
    args = ["diff"]
    if staged:
        args.append("--staged")
    if file_path is not None:
        args.extend(["--", file_path])
    return _run(*args).stdout


def diff_between(ref1: str, ref2: str) -> str:
    """Return diff between two refs (branches, commits)."""
    return _run("diff", f"{ref1}..{ref2}").stdout


def diff_ref(ref: str) -> str:
    """Return diff for a ref (e.g., HEAD~1)."""
    return _run("diff", ref).stdout


def log_oneline(*, count: int = 20, branch: str | None = None) -> str:
    """Return compact one-line log."""
    args = ["log", f"-{count}", "--oneline", "--decorate"]
    if branch is not None:
        args.append(branch)
    return _run(*args).stdout


def log_graph(
    *,
    count: int = 30,
    branches: list[str] | None = None,
) -> str:
    """Return graph log with branch topology and commit metadata."""
    args = [
        "log",
        "--graph",
        "--oneline",
        "--decorate",
        f"-{count}",
        "--color=never",
        "--format=%h %d %s (%cr, %an)",
    ]
    if branches:
        args.extend(branches)
    else:
        args.append("--all")
    return _run(*args).stdout


def show_commit(ref: str) -> str:
    """Return full commit details with diff."""
    return _run("show", ref).stdout


def show_commit_oneline(ref: str) -> str:
    """Return compact one-line commit info: hash subject | date | author."""
    return _run(
        "log",
        "-1",
        "--format=%h %s | %cr | %an",
        ref,
    ).stdout.strip()


def checkout_ref(ref: str) -> str:
    """Checkout a commit/branch ref. May detach HEAD."""
    return _run("checkout", ref).stdout


def list_branches(*, remote: bool = False) -> str:
    """Return branch list."""
    args = ["branch"]
    if remote:
        args.append("-r")
    args.append("--format=%(refname:short)")
    return _run(*args).stdout


def list_changed_files() -> list[str]:
    """Return list of changed file paths (unstaged)."""
    output = _run("diff", "--name-only").stdout
    return [f for f in output.strip().splitlines() if f]


def list_staged_files() -> list[str]:
    """Return list of staged file paths."""
    output = _run("diff", "--staged", "--name-only").stdout
    return [f for f in output.strip().splitlines() if f]


def remote_url() -> str | None:
    """Return the origin remote URL, or None if not set."""
    result = _run("remote", "get-url", "origin", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def list_remotes() -> dict[str, str]:
    """Return a dict of remote_name -> URL for all configured remotes."""
    result = _run("remote", "-v", check=False)
    if result.returncode != 0:
        return {}
    remotes: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if not line or "(push)" in line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            remotes[parts[0]] = parts[1]
    return remotes


def parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Parse owner/repo from a GitHub remote URL. Returns None if not GitHub."""
    url = url.rstrip("/").removesuffix(".git")
    if "github.com" not in url:
        return None
    parts = url.split("github.com")[-1].lstrip(":/").split("/")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def switch_branch(name: str) -> str:
    """Switch to an existing branch."""
    return _run("switch", name).stderr.strip()


def create_branch(name: str, start_point: str | None = None) -> str:
    """Create and switch to a new branch."""
    args = ["switch", "-c", name]
    if start_point is not None:
        args.append(start_point)
    return _run(*args).stderr.strip()


def delete_branch(name: str, *, force: bool = False) -> str:
    """Delete a local branch."""
    flag = "-D" if force else "-d"
    return _run("branch", flag, name).stderr.strip()


def rename_branch(old_name: str, new_name: str) -> str:
    """Rename a branch."""
    return _run("branch", "-m", old_name, new_name).stderr.strip()


def add_files(*paths: str) -> str:
    """Stage files."""
    return _run("add", *paths).stdout


def unstage_files(*paths: str) -> str:
    """Unstage files (restore --staged)."""
    return _run("restore", "--staged", *paths).stdout


def commit(message: str) -> str:
    """Create a commit with the given message."""
    return _run("commit", "-m", message).stdout


def stash_push() -> str:
    """Stash current changes."""
    return _run("stash", "push").stdout


def stash_pop() -> str:
    """Pop the latest stash."""
    return _run("stash", "pop").stdout


def stash_list() -> str:
    """List all stashes."""
    return _run("stash", "list").stdout


def is_working_tree_dirty() -> bool:
    """Return True if there are uncommitted changes."""
    result = _run("status", "--porcelain", check=False)
    return bool(result.stdout.strip())


def is_branch_merged(name: str) -> bool:
    """Return True if the branch is merged into the current branch."""
    result = _run("branch", "--merged", check=False)
    merged = [b.strip().lstrip("* ") for b in result.stdout.splitlines()]
    return name in merged


def list_untracked_files() -> list[str]:
    """Return list of untracked file paths."""
    output = _run("ls-files", "--others", "--exclude-standard").stdout
    return [f for f in output.strip().splitlines() if f]


def push_branch(
    *,
    remote: str = "origin",
    set_upstream: bool = True,
) -> str:
    """Push the current branch to a remote."""
    if set_upstream:
        branch = current_branch()
        args = ["push", "--set-upstream", remote, branch]
    else:
        args = ["push", remote]
    return _run(*args).stderr.strip()


def has_upstream() -> bool:
    """Check if the current branch tracks a remote branch."""
    result = _run(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    return result.returncode == 0


def log_oneline_commits_since(base: str = "main") -> list[str]:
    """Return commit subject lines since diverging from base.

    Used by ``pr create --fill`` to auto-fill title/body from commits.
    """
    result = _run("log", "--oneline", "--format=%s", f"{base}..HEAD", check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def fetch_pr_ref(pr_number: int, branch_name: str, remote: str = "origin") -> str:
    """Fetch a PR head ref into a local branch."""
    return _run("fetch", remote, f"pull/{pr_number}/head:{branch_name}").stderr.strip()


def status_porcelain() -> str:
    """Return git status in porcelain format (machine-readable)."""
    return _run("status", "--porcelain").stdout
