"""Rendering functions for the findings navigator TUI.

All functions are pure -- they read viewer state and return styled text
fragments (list of (style, text) tuples). No state mutation in this module.
"""

from __future__ import annotations

import shutil
import textwrap
from datetime import datetime
from typing import TYPE_CHECKING

from code_review_agent.theme import SEVERITY_STYLES, theme

from .models import TriageAction, ViewerMode

if TYPE_CHECKING:
    from .state import FindingsViewer

# Type alias matching prompt_toolkit's FormattedText inner list
_Lines = list[tuple[str, str]]


# -- Helpers ----------------------------------------------------------------


def _format_timestamp(reviewed_at: str | None) -> str:
    """Format an ISO timestamp into a compact display string."""
    if not reviewed_at:
        return ""
    # Input: "2026-03-18T12:30:00" or "2026-03-18 12:30:00"
    # Output: "Mar 18 12:30"
    try:
        dt = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except (ValueError, AttributeError):
        return reviewed_at[:16] if len(reviewed_at) >= 16 else reviewed_at


def _format_severity(sev: str) -> str:
    return sev.upper()[:4].ljust(4)


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[:width] if width <= 3 else text[: width - 3] + "..."


def _format_location(file_path: str | None, line_number: int | None, width: int = 30) -> str:
    if file_path is None:
        return ""
    loc = f"{file_path}:{line_number}" if line_number is not None else file_path
    return "..." + loc[-(width - 3) :] if len(loc) > width else loc


def _triage_label(viewer: FindingsViewer, db_id: int | None) -> tuple[str, str]:
    action = viewer.triage.get(db_id, TriageAction.OPEN)
    is_posted = db_id in viewer.posted_indices
    parts: list[str] = []
    if action == TriageAction.FALSE_POSITIVE:
        parts.append("FP")
    elif action == TriageAction.IGNORED:
        parts.append("IGN")
    elif action == TriageAction.SOLVED:
        parts.append("DONE")
    if is_posted:
        parts.append("POST")
    if not parts:
        return ("", "")
    label = "[" + "|".join(parts) + "]"
    if action == TriageAction.SOLVED:
        return (theme.success, label)
    if is_posted:
        return (theme.accent, label)
    return (theme.muted, label)


def _pr_status_label(viewer: FindingsViewer, db_id: int | None) -> tuple[str, str]:
    # Posted state is shown in the triage/status column as [POST] or [DONE|POST]
    # PR column only shows staging state (pre-post)
    if db_id in viewer.staged_for_pr:
        return (theme.accent, "[STAGED]")
    return ("", "")


def _render_cell(
    viewer: FindingsViewer,
    row: object,  # FindingRow
    col_key: str,
    width: int,
    base_style: str,
    triage_action: TriageAction,
) -> tuple[str, str]:
    """Render a single table cell as ``(style, text)``."""
    r = row  # short alias; attrs accessed via type: ignore below
    if col_key == "severity":
        label = _format_severity(r.severity.value)  # type: ignore[attr-defined]
        sty = SEVERITY_STYLES.get(r.severity.value, "")  # type: ignore[attr-defined]
        if triage_action != TriageAction.OPEN:
            sty = base_style
        return (sty, f"{label:<{width}}")
    if col_key == "agent_name":
        return (base_style, f"{_truncate(r.agent_name, width):<{width}}")  # type: ignore[attr-defined]
    if col_key == "file_line":
        return (base_style, f"{_format_location(r.file_path, r.line_number, width):<{width}}")  # type: ignore[attr-defined]
    if col_key == "title":
        return (base_style, f"{_truncate(r.title, width):<{width}}")  # type: ignore[attr-defined]
    if col_key == "triage":
        s, t = _triage_label(viewer, r.finding_db_id)  # type: ignore[attr-defined]
        return (s, f"{_truncate(t, width):<{width}}")
    if col_key == "pr_status":
        s, t = _pr_status_label(viewer, r.finding_db_id)  # type: ignore[attr-defined]
        return (s, f"{_truncate(t, width):<{width}}")
    if col_key == "reviewed_at":
        return (base_style, f"{_truncate(_format_timestamp(r.reviewed_at), width):<{width}}")  # type: ignore[attr-defined]
    if col_key == "repo":
        return (base_style, f"{_truncate(r.repo or '', width):<{width}}")  # type: ignore[attr-defined]
    if col_key == "pr_number":
        v = f"#{r.pr_number}" if r.pr_number is not None else ""  # type: ignore[attr-defined]
        return (base_style, f"{v:<{width}}")
    if col_key == "confidence":
        return (base_style, f"{r.confidence.value:<{width}}")  # type: ignore[attr-defined]
    if col_key == "category":
        return (base_style, f"{_truncate(r.category, width):<{width}}")  # type: ignore[attr-defined]
    return (base_style, f"{'':<{width}}")


# -- Public render functions ------------------------------------------------


def render_header(viewer: FindingsViewer) -> _Lines:
    """Title, stats bar, and status message."""
    lines: _Lines = [("bold", " Findings Navigator\n")]
    total, all_total = len(viewer.visible_rows), len(viewer.all_rows)
    staged, posted = len(viewer.staged_for_pr), len(viewer.posted_indices)
    parts = [f" {total}/{all_total} findings"]
    if total < all_total:
        parts.append("(filtered)")
    parts.append(f"| sort: {viewer.sort_columns[viewer.sort_index]}")
    if staged:
        parts.append(f"| {staged} staged")
    if posted:
        parts.append(f"| {posted} posted")
    lines.append((theme.muted, " ".join(parts) + "\n"))
    if viewer.status_message:
        sty = theme.error if viewer.status_message.startswith("!") else theme.success
        lines.append((sty, f" {viewer.status_message}\n"))
    lines.append(("", "\n"))
    return lines


def render_table(viewer: FindingsViewer, term_width: int) -> _Lines:
    """Scrollable findings table with header, separator, and data rows."""
    lines: _Lines = []
    if not viewer.visible_rows:
        lines.append((theme.muted, "  No findings match current filters.\n"))
        return lines

    detail_h = 12 if viewer.is_detail_open else 0
    vp = max(10, 28 - detail_h)
    v_start = max(0, viewer.cursor - vp // 2)
    v_end = min(len(viewer.visible_rows), v_start + vp)

    col_meta = viewer._compute_column_widths(viewer.visible_columns, term_width)
    total_cw = sum(w for _, _, w in col_meta)
    cw = term_width - 3
    scrollable = total_cw > cw
    h = viewer.h_offset if scrollable else 0

    # Header -- highlight the active sort column with an arrow
    active_sort = viewer.sort_columns[viewer.sort_index]
    sort_arrow = "\u2191" if viewer.is_sort_reversed else "\u2193"
    # Map sort key -> column key (file_path sorts the file_line column)
    _sort_to_col = {"file_path": "file_line"}
    active_col_key = _sort_to_col.get(active_sort, active_sort)

    hf: list[tuple[str, str]] = []
    for key, lb, w in col_meta:
        if key == active_col_key:
            label = f"{lb}{sort_arrow}"
            hf.append(("bold " + theme.accent, f"{label:<{w}}"))
        else:
            hf.append(("bold " + theme.muted, f"{lb:<{w}}"))
    if scrollable:
        hf = viewer._slice_styled_line(hf, h, cw)
    sh = ""
    if scrollable:
        sh = (" <" if h > 0 else "  ") + (">" if h < viewer._max_h_offset() else " ")
    lines.append(("", "  "))
    lines.append((theme.muted, sh[:1] if sh else " "))
    lines.extend(hf)
    lines.append(("", "\n"))
    lines.append(("", "   "))
    lines.append((theme.muted, "-" * min(total_cw, cw) + "\n"))

    # Rows
    for i in range(v_start, v_end):
        row = viewer.visible_rows[i]
        sel = i == viewer.cursor
        ta = viewer.triage.get(row.finding_db_id, TriageAction.OPEN)
        lines.append((theme.highlight, " > ") if sel else ("", "   "))
        if ta == TriageAction.FALSE_POSITIVE:
            bs = "strike dim"
        elif ta in (TriageAction.IGNORED, TriageAction.SOLVED):
            bs = "dim"
        elif sel:
            bs = "bold"
        else:
            bs = ""
        rf: list[tuple[str, str]] = [
            _render_cell(viewer, row, k, w, bs, ta) for k, _, w in col_meta
        ]
        if scrollable:
            rf = viewer._slice_styled_line(rf, h, cw)
        lines.extend(rf)
        lines.append(("", "\n"))
    return lines


def render_detail(viewer: FindingsViewer) -> _Lines:
    """Full detail panel for the selected finding."""
    if not viewer.visible_rows:
        return []
    tw = shutil.get_terminal_size((120, 40)).columns
    sep_width = max(40, tw - 6)
    lines: _Lines = [("", "\n"), (theme.muted, "   " + "=" * sep_width + "\n")]
    row = viewer.visible_rows[viewer.cursor]

    lines.append(("bold", f"   {row.title}\n"))
    lines.append(("", "\n"))

    # Agent | Severity | Confidence
    sev_sty = SEVERITY_STYLES.get(row.severity.value, "")
    lines.extend(
        [
            (theme.muted, "   Agent: "),
            ("", row.agent_name),
            (theme.muted, "  |  Severity: "),
            (sev_sty, row.severity.value.upper()),
            (theme.muted, "  |  Confidence: "),
            ("", f"{row.confidence.value}\n"),
        ]
    )

    # File:Line | Repo | PR#
    lp: _Lines = []
    if row.file_path:
        loc = row.file_path
        if row.line_number is not None:
            loc = f"{row.file_path}:{row.line_number}"
        lp.extend([(theme.muted, "   File: "), (theme.accent, loc)])
    if row.repo:
        lp.append((theme.muted, "  |  " if lp else "   "))
        lp.extend([(theme.muted, "Repo: "), ("", row.repo)])
    if row.pr_number is not None:
        lp.extend([(theme.muted, "  |  PR: "), ("", f"#{row.pr_number}")])
    if lp:
        lines.extend(lp)
        lines.append(("", "\n"))

    # Status
    ts, tl = _triage_label(viewer, row.finding_db_id)
    if tl:
        lines.extend([(theme.muted, "   Status: "), (ts, f"{tl}\n")])

    lines.append(("", "\n"))
    wrap_width = max(40, tw - 10)
    lines.append((theme.muted, "   Description:\n"))
    for dl in row.description.split("\n"):
        for w in textwrap.wrap(dl, width=wrap_width) or [""]:
            lines.append(("", f"     {w}\n"))

    if row.suggestion:
        lines.append(("", "\n"))
        lines.append((theme.muted, "   Suggestion:\n"))
        for sl in row.suggestion.split("\n"):
            for w in textwrap.wrap(sl, width=wrap_width) or [""]:
                lines.append((theme.success, f"     {w}\n"))
    return lines


def render_footer(viewer: FindingsViewer) -> _Lines:
    """Mode-aware key hints footer bar."""
    tw = shutil.get_terminal_size((120, 40)).columns
    lines: _Lines = [("", "\n"), (theme.muted, " " + "-" * max(40, tw - 4) + "\n")]

    if viewer.mode == ViewerMode.DETAIL:
        hints: _Lines = [
            (theme.accent, " [m]"),
            ("", "solved "),
            (theme.accent, "[F]"),
            ("", "false-pos "),
            (theme.accent, "[I]"),
            ("", "ignore "),
            (theme.accent, "[p]"),
            ("", "post "),
            (theme.accent, "[P]"),
            ("", "unpost "),
            (theme.accent, "[d]"),
            ("", "delete "),
            (theme.accent, "[c]"),
            ("", "copy "),
            (theme.accent, "[q]"),
            ("", "back "),
            (theme.accent, "[?]"),
            ("", "help"),
        ]
    elif viewer.mode == ViewerMode.FILTER:
        hints = [
            (theme.accent, " [Enter]"),
            ("", "add "),
            (theme.accent, "[d]"),
            ("", "remove "),
            (theme.accent, "[Tab]"),
            ("", "apply "),
            (theme.accent, "[Esc]"),
            ("", "cancel"),
        ]
    elif viewer.mode == ViewerMode.CONFIRM:
        hints = [(theme.accent, " [y]"), ("", "es "), (theme.accent, "[n]"), ("", "o")]
    else:
        hints = [
            (theme.accent, " [Enter]"),
            ("", "select "),
            (theme.accent, "[f]"),
            ("", "ilter "),
            (theme.accent, "[s/S]"),
            ("", "ort "),
            (theme.accent, "[?]"),
            ("", "help "),
            (theme.accent, "[q]"),
            ("", "uit"),
        ]
        tw = shutil.get_terminal_size((120, 40)).columns
        cm = viewer._compute_column_widths(viewer.visible_columns, tw)
        if sum(w for _, _, w in cm) > tw - 3:
            hints.extend(
                [
                    ("", " "),
                    (theme.muted, "["),
                    (theme.accent, "<>"),
                    (theme.muted, "]scroll"),
                ]
            )
    lines.extend(hints)
    lines.append(("", "\n"))
    return lines


def render_filter(viewer: FindingsViewer) -> _Lines:
    """Filter overlay with dimension selector, text input, and suggestions."""
    from .filters import FILTER_DIMENSIONS

    lines: _Lines = [("bold", " Filter Findings\n"), ("", "\n")]

    # Dimension selector bar
    lines.append((theme.muted, " Filter by: "))
    for key, label in FILTER_DIMENSIONS:
        if key == viewer.filter_dimension:
            lines.append(("bold " + theme.accent, f"[{label}]"))
        else:
            lines.append((theme.muted, f" {label} "))
    lines.append(("", "\n\n"))

    # Text input
    lines.append((theme.muted, " Value: "))
    lines.append(("bold", viewer.filter_input))
    lines.append((theme.muted, "_\n\n"))

    # Suggestions
    for i, suggestion in enumerate(viewer.filter_suggestions[:10]):
        is_selected = i == viewer.filter_cursor
        if is_selected:
            lines.append((theme.highlight, " > "))
        else:
            lines.append(("", "   "))
        lines.append(("bold" if is_selected else "", f"{suggestion}\n"))

    if not viewer.filter_suggestions and viewer.filter_input:
        lines.append((theme.muted, "   No matches\n"))

    # Active filters
    lines.append(("", "\n"))
    if viewer.active_filters:
        lines.append((theme.muted, " Active filters:\n"))
        for af in viewer.active_filters:
            vals = ", ".join(sorted(af.values))
            lines.append((theme.accent, f"   {af.field}={vals}\n"))
    else:
        lines.append((theme.muted, " No active filters\n"))

    lines.append(("", "\n"))
    lines.append(
        (theme.muted, " [Enter]add [d]remove [Tab]apply [Esc]cancel\n"),
    )
    return lines


def render_help(viewer: FindingsViewer) -> _Lines:
    """Full keyboard reference organized by section."""
    lines: _Lines = [("bold", "\n  Findings Navigator -- Keyboard Reference\n"), ("", "\n")]
    sections: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "Navigation",
            [
                ("Up/Down / j/k", "Move between findings"),
                ("Left/Right / h/l", "Scroll table horizontally"),
                ("Enter", "Select / toggle detail"),
                ("Esc / q", "Back / quit"),
            ],
        ),
        (
            "Triage (DETAIL mode)",
            [
                ("m", "Mark / unmark as solved"),
                ("F", "Mark / unmark as false positive"),
                ("I", "Mark / unmark as ignored"),
            ],
        ),
        (
            "PR (DETAIL mode)",
            [
                ("p", "Stage / unstage for PR posting"),
                ("P", "Unpost previously posted comments"),
            ],
        ),
        (
            "Other (DETAIL mode)",
            [
                ("d", "Delete finding"),
                ("c", "Copy finding to clipboard"),
            ],
        ),
        (
            "View",
            [
                ("f", "Open filter modal"),
                ("s", "Sort forward (reverse, then next column)"),
                ("S", "Sort backward (reverse, then previous column)"),
                ("?", "Show this help"),
            ],
        ),
    ]
    for title, bindings in sections:
        lines.append(("bold", f"  {title}\n"))
        for key, desc in bindings:
            lines.extend([(theme.accent, f"    {key:<16}"), ("", f"{desc}\n")])
        lines.append(("", "\n"))
    lines.append((theme.muted, "  Press any key to dismiss.\n"))
    return lines


def render_confirm(viewer: FindingsViewer) -> _Lines:
    """Confirmation dialog rendered in the detail panel area.

    Layout inspired by lazygit/gh CLI confirmation prompts:
    - Action verb in bold (POST/UNPOST/DELETE)
    - Context: what will happen
    - Finding summary with severity and location
    - Clear y/n prompt with action labels
    """
    if viewer.pending_confirm is None:
        return []

    action = viewer.pending_confirm.action
    row = viewer.pending_confirm.finding_row

    # Color-code by action type
    if action == "delete":
        action_style = "bold " + theme.error
    else:
        action_style = "bold " + theme.warning if hasattr(theme, "warning") else "bold"

    tw = shutil.get_terminal_size((120, 40)).columns
    csep = max(30, tw - 10)
    lines: _Lines = [("", "\n"), (theme.muted, "   " + "-" * csep + "\n"), ("", "\n")]

    # Description lines (may be multi-line)
    for desc_line in viewer.pending_confirm.description.split("\n"):
        if desc_line == desc_line.upper().split("\n")[0]:
            lines.append((action_style, f"   {desc_line}\n"))
        else:
            lines.append((theme.muted, f"   {desc_line}\n"))

    lines.append(("", "\n"))

    # Finding context
    sev_style = SEVERITY_STYLES.get(row.severity.value, "")
    lines.extend(
        [
            (theme.muted, "   Finding: "),
            (sev_style, f"[{row.severity.value.upper()}] "),
            ("bold", f"{row.title}\n"),
        ]
    )
    if row.file_path:
        loc = row.file_path
        if row.line_number:
            loc += f":{row.line_number}"
        lines.extend(
            [
                (theme.muted, "   File:    "),
                (theme.accent, f"{loc}\n"),
            ]
        )
    if row.agent_name:
        lines.extend(
            [
                (theme.muted, "   Agent:   "),
                ("", f"{row.agent_name}\n"),
            ]
        )

    lines.append(("", "\n"))

    # Action buttons with verb labels
    action_labels = {
        "post": ("Post comment", "Cancel"),
        "unpost": ("Remove comment", "Cancel"),
        "delete": ("Delete permanently", "Cancel"),
    }
    yes_label, no_label = action_labels.get(action, ("Yes", "No"))

    lines.extend(
        [
            (theme.muted, "   "),
            (theme.accent, "[y]"),
            ("bold", f" {yes_label}    "),
            (theme.muted, "[n]"),
            ("", f" {no_label}\n"),
        ]
    )

    lines.extend([("", "\n"), (theme.muted, "   " + "-" * csep + "\n")])
    return lines
