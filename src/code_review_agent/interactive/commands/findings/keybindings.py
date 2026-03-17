"""Key binding configuration for the findings navigator TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.key_binding import KeyBindings

from .actions import (
    copy_finding,
    delete_finding,
    post_to_pr,
    toggle_triage,
    unpost_from_pr,
)
from .models import ConfirmAction, TriageAction, ViewerMode

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from .state import FindingsViewer


def build_key_bindings(viewer: FindingsViewer) -> KeyBindings:
    """Build the complete key binding set for all modes."""
    kb = KeyBindings()

    # -- Navigation (Up/Down/j/k) --

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE or viewer.mode == ViewerMode.DETAIL:
            viewer.move_up()
        elif viewer.mode == ViewerMode.FILTER:
            viewer.filter_cursor = max(0, viewer.filter_cursor - 1)

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE or viewer.mode == ViewerMode.DETAIL:
            viewer.move_down()
        elif viewer.mode == ViewerMode.FILTER:
            max_idx = len(viewer.filter_suggestions) - 1
            viewer.filter_cursor = min(max_idx, viewer.filter_cursor + 1)

    # -- Horizontal scroll (Left/Right/h/l) --

    @kb.add("left")
    @kb.add("h")
    def on_left(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE:
            viewer.scroll_left()
        elif viewer.mode == ViewerMode.FILTER:
            viewer.cycle_filter_dimension(-1)

    @kb.add("right")
    @kb.add("l")
    def on_right(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE:
            viewer.scroll_right()
        elif viewer.mode == ViewerMode.FILTER:
            viewer.cycle_filter_dimension(1)

    # -- Enter: open detail or select filter suggestion --

    @kb.add("enter")
    def on_enter(event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE:
            viewer.open_detail()
        elif viewer.mode == ViewerMode.FILTER:
            viewer.select_filter_suggestion()

    # -- Filter (f) --

    @kb.add("f")
    def on_filter(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE:
            viewer.open_filter()
        elif viewer.mode == ViewerMode.FILTER:
            viewer.filter_input += "f"
            viewer.update_filter_suggestions()

    # -- Sort (s) --

    @kb.add("s")
    def on_sort(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.NAVIGATE:
            viewer.cycle_sort()

    # -- Triage actions (DETAIL mode only) --

    @kb.add("m")
    def on_mark_solved(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL:
            toggle_triage(viewer, TriageAction.SOLVED)

    @kb.add("F")
    def on_false_positive(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL:
            toggle_triage(viewer, TriageAction.FALSE_POSITIVE)

    @kb.add("I")
    def on_ignore(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL:
            toggle_triage(viewer, TriageAction.IGNORED)

    # -- PR actions (DETAIL mode, with confirmation) --

    @kb.add("p")
    def on_post(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL and viewer.visible_rows:
            row = viewer.visible_rows[viewer.cursor]
            repo = row.repo or "unknown"
            pr_num = row.pr_number or "?"
            viewer.pending_confirm = ConfirmAction(
                action="post",
                description=(
                    f"POST comment to {repo}#{pr_num}\n"
                    f"This will create an inline review comment on the PR."
                ),
                finding_row=row,
            )
            viewer.mode = ViewerMode.CONFIRM

    @kb.add("P")
    def on_unpost(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL and viewer.visible_rows:
            row = viewer.visible_rows[viewer.cursor]
            db_id = row.finding_db_id
            if db_id not in viewer.posted_indices:
                viewer.status_message = "! This finding has not been posted"
                return
            repo = row.repo or "unknown"
            pr_num = row.pr_number or "?"
            viewer.pending_confirm = ConfirmAction(
                action="unpost",
                description=(
                    f"UNPOST comment from {repo}#{pr_num}\n"
                    f"This will remove the review comment from the PR."
                ),
                finding_row=row,
            )
            viewer.mode = ViewerMode.CONFIRM

    # -- Delete finding (DETAIL mode, with confirmation) --

    @kb.add("d")
    def on_delete(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL and viewer.visible_rows:
            row = viewer.visible_rows[viewer.cursor]
            viewer.pending_confirm = ConfirmAction(
                action="delete",
                description=("DELETE finding permanently\nThis action cannot be undone."),
                finding_row=row,
            )
            viewer.mode = ViewerMode.CONFIRM
        elif viewer.mode == ViewerMode.FILTER:
            viewer.remove_current_filter()

    # -- Copy (DETAIL mode) --

    @kb.add("c")
    def on_copy(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.DETAIL:
            copy_finding(viewer)

    # -- Confirm (y/n) --

    @kb.add("y")
    def on_confirm_yes(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.CONFIRM and viewer.pending_confirm is not None:
            action = viewer.pending_confirm.action
            if action == "post":
                post_to_pr(viewer)
            elif action == "unpost":
                unpost_from_pr(viewer)
            elif action == "delete":
                delete_finding(viewer)
            viewer.pending_confirm = None
            viewer.mode = ViewerMode.DETAIL

    @kb.add("n")
    def on_confirm_no(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.CONFIRM:
            viewer.pending_confirm = None
            viewer.mode = ViewerMode.DETAIL

    # -- Help (?) --

    @kb.add("?")
    def on_help(_event: KeyPressEvent) -> None:
        if viewer.mode in (ViewerMode.NAVIGATE, ViewerMode.DETAIL):
            viewer.mode = ViewerMode.HELP

    # -- Tab: confirm filter --

    @kb.add("tab")
    def on_tab(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.FILTER:
            viewer.apply_filters()
            viewer.mode = ViewerMode.NAVIGATE

    # -- Escape / q: back or quit --

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.HELP:
            viewer.mode = ViewerMode.NAVIGATE
        elif viewer.mode == ViewerMode.CONFIRM:
            viewer.pending_confirm = None
            viewer.mode = ViewerMode.DETAIL
        elif viewer.mode == ViewerMode.FILTER:
            viewer.cancel_filter()
        elif viewer.mode == ViewerMode.DETAIL:
            viewer.close_detail()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.HELP:
            viewer.mode = ViewerMode.NAVIGATE
        elif viewer.mode == ViewerMode.CONFIRM:
            viewer.pending_confirm = None
            viewer.mode = ViewerMode.DETAIL
        elif viewer.mode == ViewerMode.FILTER:
            viewer.cancel_filter()
        elif viewer.mode == ViewerMode.DETAIL:
            viewer.close_detail()
        elif viewer.mode == ViewerMode.NAVIGATE:
            event.app.exit()

    # -- Filter text input --

    @kb.add("backspace")
    def on_backspace(_event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.FILTER and viewer.filter_input:
            viewer.filter_input = viewer.filter_input[:-1]
            viewer.update_filter_suggestions()

    @kb.add("<any>")
    def on_any(event: KeyPressEvent) -> None:
        if viewer.mode == ViewerMode.HELP:
            viewer.mode = ViewerMode.NAVIGATE
        elif viewer.mode == ViewerMode.FILTER:
            char = event.data
            if char.isprintable() and len(char) == 1:
                viewer.filter_input += char
                viewer.update_filter_suggestions()

    return kb
