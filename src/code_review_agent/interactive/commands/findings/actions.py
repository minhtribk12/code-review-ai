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
    current = viewer.triage.get(row.index, TriageAction.OPEN)
    if current == action:
        viewer.triage.pop(row.index, None)
        _persist_triage(viewer, row.finding_db_id, TriageAction.OPEN)
        viewer.status_message = f"Unmarked: {row.title}"
    else:
        viewer.triage[row.index] = action
        _persist_triage(viewer, row.finding_db_id, action)
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

    inline_comments: list[dict[str, Any]] = []
    general_findings: list[str] = []

    if row.file_path is not None and row.line_number is not None:
        inline_comments.append(
            {
                "path": row.file_path,
                "line": row.line_number,
                "body": comment_body,
            }
        )
    else:
        general_findings.append(comment_body)

    body_parts = ["Code review finding from automated analysis."]
    if general_findings:
        body_parts.extend(["", "---", "", *general_findings])
    review_body = "\n".join(body_parts)

    try:
        result = submit_pr_review_with_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            pr_number=pr_number,
            body=review_body,
            comments=inline_comments,
        )
        viewer.comments_posted += 1
        viewer.posted_indices.add(row.index)
        _persist_posted(viewer, row.finding_db_id, is_posted=True)

        review_id = result.get("id")
        if review_id is not None:
            viewer.last_review_id = review_id
            _fetch_comment_ids(viewer, owner, repo, pr_number, review_id)

        viewer.status_message = f"Posted finding to PR #{pr_number}"

    except GitHubAuthError:
        viewer.status_message = "! Permission denied (check token scope)"
    except httpx.HTTPStatusError as exc:
        viewer.status_message = f"! GitHub API error: {exc.response.status_code}"
    except Exception as exc:
        logger.exception("pr review posting failed")
        viewer.status_message = f"! Error posting review: {exc}"


def unpost_from_pr(viewer: FindingsViewer) -> None:
    """Delete posted PR review comments for the current finding."""
    if not viewer.last_comment_ids:
        viewer.status_message = "! No posted comments to delete"
        return

    row = viewer.visible_rows[viewer.cursor] if viewer.visible_rows else None
    pr_url = _resolve_pr_url(viewer, row)
    if not pr_url:
        viewer.status_message = "! Not a PR review"
        return
    if not viewer.github_token:
        viewer.status_message = "! GITHUB_TOKEN required"
        return

    try:
        owner, repo, _pr_number = parse_pr_reference(pr_url)
    except ValueError as exc:
        viewer.status_message = f"! Invalid PR URL: {exc}"
        return

    try:
        deleted = delete_review_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            comment_ids=viewer.last_comment_ids,
        )
        viewer.comments_deleted += deleted
        viewer.last_comment_ids.clear()
        viewer.last_review_id = None
        if row is not None:
            viewer.posted_indices.discard(row.index)
            _persist_posted(viewer, row.finding_db_id, is_posted=False)
        viewer.status_message = f"Deleted {deleted} comment(s)"

    except GitHubAuthError:
        viewer.status_message = "! Permission denied"
    except httpx.HTTPStatusError as exc:
        viewer.status_message = f"! GitHub API error: {exc.response.status_code}"
    except Exception as exc:
        logger.exception("comment deletion failed")
        viewer.status_message = f"! Error deleting comments: {exc}"


def delete_finding(viewer: FindingsViewer) -> None:
    """Delete the current finding from the database."""
    if not viewer.visible_rows:
        viewer.status_message = "! No finding selected"
        return

    row = viewer.visible_rows[viewer.cursor]
    if viewer._storage is None or row.finding_db_id is None:
        viewer.status_message = "! Cannot delete (no DB connection)"
        return

    try:
        viewer._storage.delete_finding(row.finding_db_id)
        viewer.all_rows = [r for r in viewer.all_rows if r.finding_db_id != row.finding_db_id]
        viewer.triage.pop(row.index, None)
        viewer.posted_indices.discard(row.index)
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
        import shutil
        import subprocess

        clip_cmd = shutil.which("xclip") or shutil.which("xsel")
        if clip_cmd is None:
            viewer.status_message = "! xclip/xsel not installed"
            return

        clip_args = (
            [clip_cmd, "-selection", "clipboard"]
            if "xclip" in clip_cmd
            else [clip_cmd, "--clipboard", "--input"]
        )
        subprocess.run(  # noqa: S603
            clip_args,
            input=content.encode(),
            capture_output=True,
            timeout=5,
        )
        viewer.status_message = "Copied to clipboard"
    except FileNotFoundError:
        viewer.status_message = "! clipboard tool not found"
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
) -> None:
    """Persist posted status to the findings table."""
    if viewer._storage is None or finding_db_id is None:
        return
    try:
        viewer._storage.update_finding_posted(finding_db_id, is_posted=is_posted)
    except Exception:
        logger.debug("failed to persist posted state", exc_info=True)


def _fetch_comment_ids(
    viewer: FindingsViewer,
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
) -> None:
    """Fetch and store comment IDs from the posted review."""
    if viewer.github_token is None:
        return
    try:
        comments = get_review_comments(
            owner=owner,
            repo=repo,
            token=viewer.github_token,
            pr_number=pr_number,
            review_id=review_id,
        )
        viewer.last_comment_ids = [c["id"] for c in comments if "id" in c]
    except Exception:
        logger.debug("could not fetch review comment IDs", exc_info=True)
