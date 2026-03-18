"""Triage, PR posting, comment deletion, and finding deletion actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from code_review_agent.github_client import (
    GitHubAuthError,
    delete_review_comments,
    get_review_comments,
    parse_pr_reference,
    submit_pr_review_with_comments,
)

from .models import TriageAction

if TYPE_CHECKING:
    from .state import FindingsViewer

logger = structlog.get_logger(__name__)


def toggle_triage(viewer: FindingsViewer, action: TriageAction) -> None:
    """Toggle a triage action on the current finding and persist it."""
    if not viewer.visible_rows:
        return
    row = viewer.visible_rows[viewer.cursor]
    db_id = row.finding_db_id
    if db_id is None:
        return
    current = viewer.triage.get(db_id, TriageAction.OPEN)
    if current == action:
        viewer.triage[db_id] = TriageAction.OPEN
        _persist_triage(viewer, db_id, TriageAction.OPEN)
        viewer.status_message = f"Unmarked: {row.title}"
    else:
        viewer.triage[db_id] = action
        _persist_triage(viewer, db_id, action)
        viewer.status_message = f"{action.value}: {row.title}"


def post_to_pr(viewer: FindingsViewer) -> None:
    """Post the current finding as an inline PR review comment."""
    if not viewer.visible_rows:
        viewer.status_message = "! No finding selected"
        return

    row = viewer.visible_rows[viewer.cursor]
    pr_url = _resolve_pr_url(viewer, row)
    if not pr_url:
        viewer.status_message = "! Not a PR review (local diff)"
        return
    if not viewer.github_token:
        viewer.status_message = "! GITHUB_TOKEN required for PR posting"
        return

    try:
        owner, repo, pr_number = parse_pr_reference(pr_url)
    except ValueError as exc:
        viewer.status_message = f"! Invalid PR URL: {exc}"
        return

    comment_body = (
        f"**{row.severity.value.upper()}** ({row.agent_name}): {row.title}\n\n{row.description}"
    )
    if row.suggestion:
        comment_body += f"\n\n**Suggestion:** {row.suggestion}"

    if row.file_path is None or row.line_number is None:
        viewer.status_message = "! Finding has no file/line -- cannot post inline comment"
        return

    inline_comments: list[dict[str, Any]] = [
        {
            "path": row.file_path,
            "line": row.line_number,
            "body": comment_body,
        }
    ]

    try:
        result = submit_pr_review_with_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            pr_number=pr_number,
            body="",
            comments=inline_comments,
        )
        viewer.comments_posted += 1
        if row.finding_db_id is not None:
            viewer.posted_indices.add(row.finding_db_id)

        # Get comment IDs from the result (fetched by submit_pr_review_with_comments)
        comment_ids: list[int] = result.get("comment_ids", [])
        posted_comment_id = comment_ids[0] if comment_ids else None
        viewer.last_review_id = result.get("id")
        viewer.last_comment_ids = comment_ids

        _persist_posted(
            viewer,
            row.finding_db_id,
            is_posted=True,
            comment_id=posted_comment_id,
        )

        viewer.status_message = f"Posted finding to PR #{pr_number}"

    except GitHubAuthError:
        viewer.status_message = "! Permission denied (check token scope)"
    except httpx.HTTPStatusError as exc:
        viewer.status_message = f"! GitHub API error: {exc.response.status_code}"
    except Exception as exc:
        logger.exception("pr review posting failed")
        viewer.status_message = f"! Error posting review: {exc}"


def unpost_from_pr(viewer: FindingsViewer) -> None:
    """Delete the posted PR comment using the stored comment_id.

    The comment_id is persisted in the findings table, so this works
    across sessions without needing the same-session post context.
    """
    row = None
    if viewer.pending_confirm is not None:
        row = viewer.pending_confirm.finding_row
    elif viewer.visible_rows:
        row = viewer.visible_rows[viewer.cursor]

    if row is None or row.finding_db_id is None:
        viewer.status_message = "! No finding selected"
        return

    # Get comment ID: from DB row first, then from session memory
    comment_id = row.comment_id
    comment_ids_to_delete: list[int] = []
    if comment_id is not None:
        comment_ids_to_delete = [comment_id]
    elif viewer.last_comment_ids:
        comment_ids_to_delete = list(viewer.last_comment_ids)

    if not comment_ids_to_delete:
        viewer.status_message = "! No comment ID found for this finding"
        return

    pr_url = _resolve_pr_url(viewer, row)
    if not pr_url:
        viewer.status_message = "! Cannot determine PR URL"
        return
    if not viewer.github_token:
        viewer.status_message = "! GITHUB_TOKEN required"
        return

    try:
        owner, repo, _pr_number = parse_pr_reference(pr_url)
        deleted = delete_review_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            comment_ids=comment_ids_to_delete,
        )
        viewer.comments_deleted += deleted
        viewer.last_comment_ids.clear()
    except GitHubAuthError:
        viewer.status_message = "! Permission denied"
        return
    except httpx.HTTPStatusError as exc:
        viewer.status_message = f"! GitHub API error: {exc.response.status_code}"
        return
    except Exception as exc:
        logger.exception("comment deletion failed")
        viewer.status_message = f"! Error: {exc}"
        return

    # Mark as unposted in DB (clears comment_id)
    viewer.posted_indices.discard(row.finding_db_id)
    _persist_posted(viewer, row.finding_db_id, is_posted=False)
    viewer.status_message = f"Unposted: {row.title}"


def delete_finding(viewer: FindingsViewer) -> None:
    """Delete the confirmed finding from the database."""
    # Use pending_confirm's finding_row if available (from confirm dialog)
    row = None
    if viewer.pending_confirm is not None:
        row = viewer.pending_confirm.finding_row
    elif viewer.visible_rows:
        row = viewer.visible_rows[viewer.cursor]

    if row is None:
        viewer.status_message = "! No finding selected"
        return

    db_id = row.finding_db_id
    if viewer._storage is None or db_id is None:
        viewer.status_message = "! Cannot delete (no DB connection)"
        return

    try:
        viewer._storage.delete_finding(db_id)
        viewer.all_rows = [r for r in viewer.all_rows if r.finding_db_id != db_id]
        viewer.triage.pop(db_id, None)
        viewer.posted_indices.discard(db_id)
        viewer._apply_filters()
        viewer.cursor = min(viewer.cursor, max(0, len(viewer.visible_rows) - 1))
        viewer.status_message = f"Deleted: {row.title}"
    except Exception as exc:
        logger.exception("finding deletion failed")
        viewer.status_message = f"! Error deleting finding: {exc}"


def copy_finding(viewer: FindingsViewer) -> None:
    """Copy the current finding's content to clipboard."""
    if not viewer.visible_rows:
        return

    row = viewer.visible_rows[viewer.cursor]
    lines = [
        f"[{row.severity.value.upper()}] {row.title}",
        f"Agent: {row.agent_name}",
    ]
    if row.file_path:
        loc = row.file_path
        if row.line_number:
            loc += f":{row.line_number}"
        lines.append(f"File: {loc}")
    lines.append(f"\n{row.description}")
    if row.suggestion:
        lines.append(f"\nSuggestion: {row.suggestion}")

    content = "\n".join(lines)

    try:
        import platform
        import shutil
        import subprocess

        system = platform.system()
        clip_cmd: str | None = None
        clip_args: list[str] = []

        if system == "Darwin":
            clip_cmd = shutil.which("pbcopy")
            if clip_cmd:
                clip_args = [clip_cmd]
        elif system == "Linux":
            clip_cmd = shutil.which("xclip")
            if clip_cmd:
                clip_args = [clip_cmd, "-selection", "clipboard"]
            else:
                clip_cmd = shutil.which("xsel")
                if clip_cmd:
                    clip_args = [clip_cmd, "--clipboard", "--input"]

        if not clip_cmd:
            # Fallback: try wl-copy (Wayland)
            clip_cmd = shutil.which("wl-copy")
            if clip_cmd:
                clip_args = [clip_cmd]

        if not clip_cmd:
            viewer.status_message = "! No clipboard tool found (install xclip, xsel, or wl-copy)"
            return

        subprocess.run(  # noqa: S603
            clip_args,
            input=content.encode(),
            capture_output=True,
            timeout=5,
        )
        viewer.status_message = "Copied to clipboard"
    except FileNotFoundError:
        viewer.status_message = "! Clipboard tool not found"
    except subprocess.TimeoutExpired:
        viewer.status_message = "! Clipboard operation timed out"
    except Exception:
        viewer.status_message = "! Failed to copy to clipboard"


# -- Helpers --


def _resolve_pr_url(
    viewer: FindingsViewer,
    row: object | None,
) -> str | None:
    """Get the PR URL for posting, from report or from the finding row."""
    if viewer.report is not None and viewer.report.pr_url:
        return viewer.report.pr_url
    if row is not None:
        repo = getattr(row, "repo", None)
        pr_number = getattr(row, "pr_number", None)
        if repo and pr_number:
            return f"https://github.com/{repo}/pull/{pr_number}"
    return None


def _persist_triage(
    viewer: FindingsViewer,
    finding_db_id: int | None,
    action: TriageAction,
) -> None:
    """Persist triage change to the findings table."""
    if viewer._storage is None or finding_db_id is None:
        return
    try:
        viewer._storage.update_finding_triage(finding_db_id, action.value)
    except Exception:
        logger.debug("failed to persist triage", exc_info=True)


def _persist_posted(
    viewer: FindingsViewer,
    finding_db_id: int | None,
    *,
    is_posted: bool,
    comment_id: int | None = None,
) -> None:
    """Persist posted status and comment ID to the findings table."""
    if viewer._storage is None or finding_db_id is None:
        return
    try:
        viewer._storage.update_finding_posted(
            finding_db_id,
            is_posted=is_posted,
            comment_id=comment_id,
        )
    except Exception:
        logger.debug("failed to persist posted state", exc_info=True)


def _fetch_comment_ids_list(
    viewer: FindingsViewer,
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
) -> list[int]:
    """Fetch comment IDs from the posted review. Returns the list."""
    if viewer.github_token is None:
        return []
    try:
        comments = get_review_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            pr_number=pr_number,
            review_id=review_id,
        )
        return [c["id"] for c in comments if "id" in c]
    except Exception:
        logger.debug("could not fetch review comment IDs", exc_info=True)
        return []
