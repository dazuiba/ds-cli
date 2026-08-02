"""Textual-based TUI for interactive run listing and detail viewing in handoff."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from typing import Optional, Callable

from textual.app import App, ComposeResult, InvalidThemeError
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Static
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Offset
from textual.selection import Selection
from textual.strip import Strip

from .config import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, read_tui_theme, write_tui_theme
from .core import format_run_row, get_db, prompt_prefix, row_value, task_paths
from .runtime_info import (
    dump_runtime_info,
    format_usage_detail_value,
    format_usage_value,
    kill_process_group,
    normalize_usage,
    parse_runtime_info,
    runtime_pid,
    scan_jsonl_usage,
)

# Seconds between DB polls for auto-refresh.
POLL_INTERVAL = 5.0
INITIAL_STATUS_CHECK_DELAY = 3.0
RUN_COLUMN_WIDTH = 34
RUN_COLUMN_MIN_WIDTH = 19
DATE_COLUMN_WIDTH = 11
DATE_COLUMN_MIN_WIDTH = 5
STATUS_COLUMN_WIDTH = 8
CWD_COLUMN_WIDTH = 10
CWD_COLUMN_MIN_WIDTH = 6
FULL_CWD_COLUMN_WIDTH = 28
FULL_CWD_COLUMN_MIN_WIDTH = 8
INFO_COLUMN_WIDTH = 14
PROMPT_COLUMN_MIN_WIDTH = 20
TABLE_WIDTH_SLACK = 18


def _copy_to_native_clipboard(text: str) -> bool:
    """Copy text with a platform clipboard command when one is available.

    Textual's clipboard implementation uses OSC 52, which isn't supported by
    macOS Terminal. Keep OSC 52 as the app-level fallback, but also use the
    native clipboard on local desktop sessions.
    """
    commands: list[list[str]] = []
    if sys.platform == "darwin":
        commands.append(["pbcopy"])
    elif os.name == "nt":
        commands.append(["clip.exe"])
    else:
        if os.environ.get("WAYLAND_DISPLAY"):
            commands.append(["wl-copy"])
        if os.environ.get("DISPLAY"):
            commands.extend(
                (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"])
            )

    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        return True
    return False


def _copy_selected_text(screen: Screen) -> bool:
    """Copy the screen's mouse selection, returning whether it was non-empty."""
    selected_text = screen.get_selected_text()
    if not selected_text:
        return False
    screen.app.copy_to_clipboard(selected_text)
    screen.notify(
        f"Copied selected text ({len(selected_text)} chars)",
        severity="information",
        timeout=3,
    )
    return True


class _SelectableDataTable(DataTable):
    """DataTable that can copy a mouse-selected portion of its visible text."""

    @property
    def allow_select(self) -> bool:
        return True

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        if self.size.height <= 0:
            return "", "\n"

        lines = [super().render_line(y) for y in range(self.size.height)]
        start = selection.start or Offset(0, 0)
        last_y = len(lines) - 1
        end = selection.end or Offset(lines[last_y].cell_length, last_y)
        start, end = sorted((start, end), key=lambda offset: (offset.y, offset.x))
        start_y = min(max(start.y, 0), last_y)
        end_y = min(max(end.y, 0), last_y)

        selected_lines: list[str] = []
        for y in range(start_y, end_y + 1):
            line = lines[y]
            start_x = start.x if y == start_y else 0
            end_x = end.x if y == end_y else line.cell_length
            selected = line.crop(start_x, end_x).text
            if end_x >= line.cell_length:
                selected = selected.rstrip()
            selected_lines.append(selected)
        return "\n".join(selected_lines), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self.refresh()

    def render_line(self, y: int) -> Strip:
        line = super().render_line(y)
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                end = line.cell_length if end == -1 else end
                line = Strip.join(
                    (
                        line.crop(0, start),
                        line.crop(start, end).apply_style(
                            self.screen.get_component_rich_style("screen--selection")
                        ),
                        line.crop(end),
                    )
                )
        return line.apply_offsets(0, y)


class KillRunError(Exception):
    """Raised when a running task cannot be killed from the TUI."""


class KillConfirmScreen(ModalScreen[bool]):
    """Confirm killing a running task."""

    CSS = """
    KillConfirmScreen {
        align: center middle;
    }

    #kill_dialog {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }

    #kill_title {
        text-style: bold;
        margin-bottom: 1;
    }

    #kill_message {
        margin-bottom: 1;
    }

    #kill_buttons {
        height: auto;
        align-horizontal: right;
    }

    #kill_buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Kill", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, run_id: str):
        self._run_id = run_id
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Kill running task?", id="kill_title"),
            Static(
                f"Send SIGTERM to the process group for {self._run_id}.",
                id="kill_message",
            ),
            Horizontal(
                Button("Cancel", id="cancel", variant="default"),
                Button("Kill", id="kill", variant="error"),
                id="kill_buttons",
            ),
            id="kill_dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#kill", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "kill")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def _runtime_info_column_exists(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "runtime_info" for row in conn.execute("PRAGMA table_info(runs)"))


def _load_runtime_info(run_id: str) -> dict:
    """Load runtime_info JSON for a run without requiring list rows to include it."""
    conn = get_db()
    try:
        if not _runtime_info_column_exists(conn):
            raise KillRunError("runtime_info column is not available yet")

        row = conn.execute(
            "SELECT runtime_info FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KillRunError("run no longer exists")

        info = parse_runtime_info(row["runtime_info"] if row else None)
        if not isinstance(info, dict):
            raise KillRunError("runtime_info is not a JSON object")
        return info
    finally:
        conn.close()


def _runtime_pid(info: dict) -> int:
    pid = runtime_pid(dump_runtime_info(info))
    if pid <= 0:
        raise KillRunError("running task has no runtime pid")
    return pid


def _kill_process_group(pid: int) -> None:
    try:
        kill_process_group(pid)
    except ProcessLookupError as exc:
        raise KillRunError(f"process {pid} is not running") from exc
    except PermissionError as exc:
        raise KillRunError(f"permission denied killing process group for {pid}") from exc
    except OSError as exc:
        raise KillRunError(f"failed to kill process group for {pid}: {exc}") from exc


def _mark_run_interrupted(run_id: str, info: dict) -> None:
    info = dict(info)
    info.pop("pid", None)
    conn = get_db()
    try:
        if not _runtime_info_column_exists(conn):
            raise KillRunError("runtime_info column is not available yet")

        cursor = conn.execute(
            "UPDATE runs SET status = ?, runtime_info = ? "
            "WHERE run_id = ? AND status = ?",
            ("interrupted", dump_runtime_info(info), run_id, "running"),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise KillRunError("run is no longer running")
    except sqlite3.Error as exc:
        raise KillRunError(f"failed to update run status: {exc}") from exc
    finally:
        conn.close()


class HandoffTuiApp(App):
    """Shared Textual app behavior for handoff TUI screens."""

    BINDINGS = [
        Binding("d", "cycle_theme", "Theme", show=True),
    ]

    def __init__(self, *args, theme_name: str | None = None, **kwargs):
        self._initial_theme_name = theme_name or read_tui_theme()
        super().__init__(*args, **kwargs)

    def apply_initial_theme(self) -> None:
        self._set_theme(self._initial_theme_name, quiet=False)

    def copy_to_clipboard(self, text: str) -> None:
        """Copy via Textual/OSC 52 and a native desktop clipboard when possible."""
        super().copy_to_clipboard(text)
        _copy_to_native_clipboard(text)

    def on_mouse_up(self, event) -> None:
        """Copy a completed app-level text selection without requiring a key chord."""
        _copy_selected_text(self.screen)

    def _set_theme(self, theme_name: str, *, quiet: bool) -> str:
        try:
            self.theme = theme_name
            return theme_name
        except InvalidThemeError:
            self.theme = DEFAULT_DARK_THEME
            if not quiet:
                self.notify(
                    f"Unknown theme: {theme_name}. Using {DEFAULT_DARK_THEME}.",
                    severity="warning",
                    timeout=3,
                )
            return DEFAULT_DARK_THEME

    def action_cycle_theme(self) -> None:
        next_theme = (
            DEFAULT_LIGHT_THEME if self.current_theme.dark else DEFAULT_DARK_THEME
        )
        applied_theme = self._set_theme(next_theme, quiet=False)
        write_tui_theme(applied_theme)
        self.notify(f"Theme saved: {applied_theme}", severity="information", timeout=2)


class RunListScreen(Screen):
    """Main screen showing the run list in a DataTable.

    Key bindings:
      Enter / →   — open detail view for the selected run
      O           — resume the selected run's session
      C           — copy selected text, or the session UUID if none is selected
      X           — kill the selected running task
      Q           — quit
    """

    BINDINGS = [
        Binding("right,space", "select_run", "Detail", show=True),
        Binding("o", "go_resume", "Open", show=True),
        Binding("c", "copy_session", "Copy", show=True),
        Binding("x", "kill_run", "Kill", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        rows: list,
        full_cwd: bool = False,
        refresh_fn: Callable[[], list] | None = None,
        initial_run_id: str | None = None,
        open_detail_on_mount: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        self._rows = rows          # sqlite3.Row objects
        self._full_cwd = full_cwd
        self._result: Optional[str] = None  # "resume:<run_id>" or None
        self._refresh_fn = refresh_fn
        self._initial_run_id = initial_run_id
        self._open_detail_on_mount = open_detail_on_mount
        self._fingerprint: str = ""               # change-detection fingerprint
        self._dirty: bool = False                 # data changed while detail view was active
        self._pending_cursor_run_id: str | None = None  # cursor-restore target
        self._active_column_widths: dict[str, int] = {}
        super().__init__(name=name, id=id, classes=classes)

    @property
    def action_result(self) -> Optional[str]:
        return self._result

    def compose(self) -> ComposeResult:
        count = len(self._rows)
        run_label = "run" if count == 1 else "runs"
        yield Static(f" handoff runs  ·  {count} recent {run_label}", id="title_bar")
        yield _SelectableDataTable(id="run_table", cursor_type="row")
        yield Static("", id="run_footer")

    def on_resize(self, event=None) -> None:
        """Recompute responsive table columns when the terminal changes size."""
        try:
            table = self.query_one("#run_table", DataTable)
        except Exception:
            return
        if table.row_count == 0 and not self._rows:
            return
        if self._column_widths() == self._active_column_widths:
            return
        self._save_cursor_run_id()
        self._rebuild_table(rebuild_columns=True)

    def on_mount(self) -> None:
        table = self.query_one("#run_table", DataTable)
        self._add_columns(table)

        if not self._rows:
            table.add_row("(no runs)", "", "", "", "", "")
            self._update_footer()
            return

        for row in self._rows:
            self._add_table_row(table, row)

        table.focus()

        if self._initial_run_id:
            self._pending_cursor_run_id = self._initial_run_id
            self._restore_cursor()

        # Start periodic DB polling for auto-refresh
        if self._refresh_fn is not None:
            self._fingerprint = self._compute_fingerprint(self._rows)
            self.set_timer(INITIAL_STATUS_CHECK_DELAY, self._refresh_now)
            self.set_interval(POLL_INTERVAL, self._poll_refresh)

        if self._open_detail_on_mount:
            self._open_detail()
        self._update_footer()

    def _selected_row(self):
        """Return the sqlite3.Row for the currently selected table row."""
        table = self.query_one("#run_table", DataTable)
        if table.row_count == 0:
            return None
        rc = table.cursor_row
        if rc >= len(self._rows):
            return None
        return self._rows[rc]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter key on a DataTable row."""
        event.stop()
        self._open_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh footer token details as the cursor moves."""
        event.stop()
        self._update_footer()

    def action_select_run(self) -> None:
        """Open detail view for the selected run."""
        self._open_detail()

    def _open_detail(self) -> None:
        """Shared detail-opening logic."""
        row = self._selected_row()
        if row is None:
            return

        jsonl_path = row["jsonl_path"]
        run_id = row["run_id"]
        prompt_path, out_path, result_path = task_paths(run_id)

        run_info = {
            "run_id": run_id,
            "date": row["created_at"],
            "cwd": row["cwd"],
            "uuid": row["uuid"],
            "out_path": out_path,
            "backend": row["backend"],
        }

        from .jsonl_viewer import make_viewer_screen
        viewer = make_viewer_screen(jsonl_path, prompt_path, out_path, result_path, run_info)
        self.app.push_screen(viewer)

    def action_go_resume(self) -> None:
        """Resume the selected session."""
        row = self._selected_row()
        if row is None:
            return
        self._result = f"resume:{row['run_id']}"
        # Write result to app so cmd_list can read it after run() returns
        if hasattr(self.app, '_action_result'):
            self.app._action_result = self._result
        self.app.exit()

    def action_copy_session(self) -> None:
        """Copy selected text, falling back to the session UUID."""
        if _copy_selected_text(self):
            return
        row = self._selected_row()
        if row is None:
            return
        uid = row["uuid"]
        if uid:
            self.app.copy_to_clipboard(uid)
            self.notify(f"Copied: {uid}", severity="information", timeout=3)

    def action_kill_run(self) -> None:
        """Confirm and kill the selected running task."""
        row = self._selected_row()
        if row is None:
            return

        run_id = row["run_id"]
        if row["status"] != "running":
            self.notify("Only running tasks can be killed", severity="warning", timeout=3)
            return

        self.app.push_screen(KillConfirmScreen(run_id), self._kill_run_after_confirm)

    def _kill_run_after_confirm(self, confirmed: bool) -> None:
        if not confirmed:
            return

        row = self._selected_row()
        if row is None:
            return
        run_id = row["run_id"]

        try:
            info = _load_runtime_info(run_id)
            pid = _runtime_pid(info)
            _kill_process_group(pid)
            _mark_run_interrupted(run_id, info)
        except KillRunError as exc:
            self.notify(f"Kill failed: {exc}", severity="error", timeout=5)
            return

        self.notify(f"Killed: {run_id}", severity="information", timeout=3)
        self._refresh_now()

    def action_quit(self) -> None:
        self.app.exit()

    # ── auto-refresh ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_fingerprint(rows: list) -> str:
        """Lightweight change-detection fingerprint: run_id:status per row."""
        parts = []
        for row in rows:
            jsonl_fp = ""
            if row["status"] == "running":
                try:
                    st = os.stat(row["jsonl_path"])
                    jsonl_fp = f":{st.st_size}:{st.st_mtime_ns}"
                except OSError:
                    jsonl_fp = ""
            parts.append(
                f"{row['run_id']}:{row['status']}:{row_value(row, 'runtime_info', '')}{jsonl_fp}"
            )
        return "|".join(parts)

    def _terminal_width(self) -> int:
        try:
            width = int(self.app.size.width)
            if width > 0:
                return width
        except Exception:
            pass
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 120

    def _column_widths(self) -> dict[str, int]:
        cwd_width = FULL_CWD_COLUMN_WIDTH if self._full_cwd else CWD_COLUMN_WIDTH
        cwd_min = FULL_CWD_COLUMN_MIN_WIDTH if self._full_cwd else CWD_COLUMN_MIN_WIDTH
        widths = {
            "run": RUN_COLUMN_WIDTH,
            "date": DATE_COLUMN_WIDTH,
            "status": self._status_column_width(),
            "cwd": cwd_width,
            "info": self._info_column_width(),
        }
        mins = {
            "date": DATE_COLUMN_MIN_WIDTH,
            "cwd": cwd_min,
            "info": self._info_column_width(),
            "run": RUN_COLUMN_MIN_WIDTH,
        }

        available = max(40, self._terminal_width() - TABLE_WIDTH_SLACK)
        fixed = sum(widths.values())
        prompt_width = available - fixed
        if prompt_width < PROMPT_COLUMN_MIN_WIDTH:
            needed = PROMPT_COLUMN_MIN_WIDTH - prompt_width
            for key in ("date", "cwd", "run"):
                reducible = max(0, widths[key] - mins[key])
                take = min(reducible, needed)
                widths[key] -= take
                needed -= take
                if needed <= 0:
                    break

        widths["prompt"] = max(8, available - sum(widths.values()))
        return widths

    def _prompt_width(self) -> int:
        return self._column_widths()["prompt"]

    def _add_columns(self, table: DataTable) -> None:
        widths = self._column_widths()
        self._active_column_widths = widths
        table.add_column("RUN", width=widths["run"], key="run")
        table.add_column("DATE", width=widths["date"], key="date")
        table.add_column("STATUS", width=widths["status"], key="status")
        table.add_column("CWD", width=widths["cwd"], key="cwd")
        table.add_column("INFO", width=widths["info"], key="info")
        table.add_column("PROMPT", width=widths["prompt"], key="prompt")

    def _add_table_row(self, table: DataTable, row) -> None:
        fmt = format_run_row(row, self._full_cwd)
        widths = self._active_column_widths or self._column_widths()
        table.add_row(
            self._run_for_row(fmt["id"], row, widths["run"]),
            self._date_for_row(fmt["date"], widths["date"]),
            self._status_for_row(row),
            self._clip(fmt["cwd"], widths["cwd"]),
            self._info_for_row(row),
            self._prompt_for_row(row),
            key=fmt["id"],
        )

    @staticmethod
    def _clip(value: str, width: int) -> str:
        value = value or ""
        if width <= 0 or len(value) <= width:
            return value
        if width <= 3:
            return value[:width]
        return value[: width - 3] + "..."

    @staticmethod
    def _clip_with_suffix(value: str, suffix: str, width: int) -> str:
        value = value or ""
        if not suffix:
            return RunListScreen._clip(value, width)
        full = value + suffix
        if len(full) <= width:
            return full
        if width <= len(suffix):
            return suffix[-width:]
        body_width = width - len(suffix)
        if body_width <= 3:
            return value[:body_width] + suffix
        return value[: body_width - 3] + "..." + suffix

    @staticmethod
    def _date_for_row(value: str, width: int) -> str:
        if len(value or "") <= width:
            return value or ""
        if width <= 5 and " " in value:
            return value.split(" ", 1)[0][:width]
        return RunListScreen._clip(value, width)

    @staticmethod
    def _resume_index_for_row(row) -> int:
        try:
            return int(row_value(row, "resume_index", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _run_for_row(self, run_id: str, row, width: int) -> str:
        suffix = "↩" if self._resume_index_for_row(row) > 0 else ""
        return self._clip_with_suffix(run_id, suffix, width)

    @staticmethod
    def _row_is_pro(row) -> bool:
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        return bool(info.get("pro"))

    @staticmethod
    def _row_is_fast(row) -> bool:
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        return bool(info.get("fast"))

    @staticmethod
    def _compact_count(value: int) -> str:
        if value >= 1_000_000:
            return f"{int(value / 1_000_000)}M"
        if value >= 1_000:
            tenths = int(value / 100_000)
            return f"{tenths / 10:.1f}M"
        return str(value)

    @staticmethod
    def _run_id_prefix(run_id: str) -> str:
        parts = (run_id or "").split("-")
        if len(parts) >= 3 and parts[0].isdigit():
            return f"{parts[1]}-{parts[2]}"
        if len(parts) >= 2:
            return f"{parts[0]}-{parts[1]}"
        return run_id or "?"

    def _context_size_for_row(self, row) -> str:
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        usage = normalize_usage(info.get("usage") if isinstance(info.get("usage"), dict) else {})
        input_tokens = int(usage.get("input_tokens") or 0)
        return self._compact_count(input_tokens) if input_tokens else ""

    @staticmethod
    def _status_for_row(row) -> str:
        status = row_value(row, "status", "") or ""
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        if status == "error" and info.get("error_reason") == "process_missing":
            return "error|lost"
        return status

    def _status_column_width(self) -> int:
        """Keep STATUS wide enough for the full status and reason."""
        return max(
            [STATUS_COLUMN_WIDTH]
            + [len(self._status_for_row(row)) for row in self._rows]
        )

    def _info_column_width(self) -> int:
        """Keep INFO wide enough for every row; never ellipsize its contents."""
        return max(
            [INFO_COLUMN_WIDTH]
            + [len(self._info_for_row(row)) for row in self._rows]
        )

    def _info_for_row(self, row) -> str:
        parts = []
        context_size = self._context_size_for_row(row)
        if context_size:
            parts.append(context_size)
        resume_index = self._resume_index_for_row(row)
        first_run_id = row_value(row, "first_session_run_id", "") or ""
        if resume_index > 0 and first_run_id:
            parts.append(f"↩{self._run_id_prefix(first_run_id)}")
        if self._row_is_pro(row):
            parts.append("Pro")
        if self._row_is_fast(row):
            parts.append("Fast")
        return "|".join(parts)

    def _prompt_for_row(self, row) -> str:
        widths = self._active_column_widths or self._column_widths()
        width = widths["prompt"]
        return prompt_prefix(row["prompt"], width)

    def _usage_for_row(self, row) -> dict:
        if row["status"] == "running":
            usage = scan_jsonl_usage(row["jsonl_path"], row_value(row, "backend", "") or "")
            if format_usage_value(usage) != "-":
                return usage
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        usage = info.get("usage")
        return usage if isinstance(usage, dict) else {}

    def _token_detail_for_row(self, row) -> str:
        return format_usage_detail_value(self._usage_for_row(row))

    def _update_footer(self) -> None:
        try:
            footer = self.query_one("#run_footer", Static)
        except Exception:
            return
        row = self._selected_row()
        detail = self._token_detail_for_row(row) if row is not None else ""
        left = " →/Space Detail   O Open   C Copy   X Kill   Q Quit"
        right = f"{detail}   ^P Palette" if detail else "^P Palette"
        width = self._terminal_width()
        if len(left) + len(right) + 1 > width:
            max_left = max(0, width - len(right) - 4)
            left = left[:max_left].rstrip()
        gap = max(1, width - len(left) - len(right))
        footer.update(left + (" " * gap) + right)

    def _save_cursor_run_id(self) -> None:
        """Remember the currently selected run_id before a table rebuild."""
        if not self._rows:
            self._pending_cursor_run_id = None
            return
        try:
            table = self.query_one("#run_table", DataTable)
            if table.row_count > 0:
                rc = table.cursor_row
                if 0 <= rc < len(self._rows):
                    self._pending_cursor_run_id = self._rows[rc]["run_id"]
                    return
        except Exception:
            pass
        self._pending_cursor_run_id = None

    def _restore_cursor(self) -> None:
        """Move DataTable cursor to the previously selected run_id."""
        if self._pending_cursor_run_id is None:
            return
        target_id = self._pending_cursor_run_id
        self._pending_cursor_run_id = None

        for i, row in enumerate(self._rows):
            if row["run_id"] == target_id:
                try:
                    table = self.query_one("#run_table", DataTable)
                    if i < table.row_count:
                        table.cursor_coordinate = Coordinate(i, 0)
                except Exception:
                    pass
                return

    def _rebuild_table(self, *, rebuild_columns: bool = False) -> None:
        """Clear and repopulate the DataTable from self._rows in place."""
        table = self.query_one("#run_table", DataTable)
        is_active = self.app.screen is self
        had_focus = table.has_focus if is_active else False

        table.clear(columns=rebuild_columns)
        if rebuild_columns:
            self._add_columns(table)

        if not self._rows:
            table.add_row("(no runs)", "", "", "", "", "")
            self.query_one("#title_bar", Static).update(" handoff runs  ·  0 runs")
            self._update_footer()
            return

        for row in self._rows:
            self._add_table_row(table, row)

        # Refresh title-bar count
        count = len(self._rows)
        run_label = "run" if count == 1 else "runs"
        self.query_one("#title_bar", Static).update(
            f" handoff runs  ·  {count} recent {run_label}"
        )

        self._restore_cursor()

        if had_focus:
            table.focus()
        self._update_footer()

    def _poll_refresh(self) -> None:
        """Periodic timer callback: check for new/changed runs from the DB."""
        if self._refresh_fn is None:
            return

        try:
            fresh_rows = self._refresh_fn()
            if fresh_rows is None:
                return

            new_fp = self._compute_fingerprint(fresh_rows)
            if new_fp == self._fingerprint:
                return  # nothing changed
        except Exception:
            return  # transient DB error — skip this tick

        # Data changed — save cursor, update rows/fingerprint, rebuild
        self._save_cursor_run_id()
        self._fingerprint = new_fp
        self._rows = fresh_rows

        if self.app.screen is self:
            # List screen is active → rebuild immediately.
            self._rebuild_table()
        else:
            # Detail view (or another screen) is on top → defer rebuild so the
            # user isn't kicked back to the list.  Data is already updated;
            # rebuild happens on the next poll tick after the screen resumes.
            self._dirty = True

    def _on_screen_resume(self) -> None:
        """Called by Textual when this screen becomes active again after a pop."""
        super()._on_screen_resume()
        if self._dirty:
            self._dirty = False
            self._rebuild_table()

    def _refresh_now(self) -> None:
        """Refresh immediately after an action mutates the selected run."""
        if self._refresh_fn is None:
            return
        try:
            fresh_rows = self._refresh_fn()
        except Exception:
            return
        if fresh_rows is None:
            return

        self._save_cursor_run_id()
        self._rows = fresh_rows
        self._fingerprint = self._compute_fingerprint(fresh_rows)
        if self.app.screen is self:
            self._rebuild_table()
        else:
            self._dirty = True


class RunListApp(HandoffTuiApp):
    """Textual app wrapping the run list screen.

    Usage:
        app = RunListApp(rows, full_cwd)
        app.run()
        if app.action_result:
            # app.action_result == "resume:<run_id>"
            ...
    """

    TITLE = "handoff list"
    CSS = """
    #title_bar {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #run_table {
        height: 1fr;
    }
    #run_footer {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        rows: list,
        full_cwd: bool = False,
        refresh_fn: Callable[[], list] | None = None,
        theme_name: str | None = None,
        initial_run_id: str | None = None,
        open_detail_on_mount: bool = False,
    ):
        self._rows = rows
        self._full_cwd = full_cwd
        self._refresh_fn = refresh_fn
        self._initial_run_id = initial_run_id
        self._open_detail_on_mount = open_detail_on_mount
        self._action_result: Optional[str] = None
        super().__init__(theme_name=theme_name)

    @property
    def action_result(self) -> Optional[str]:
        return self._action_result

    def on_mount(self) -> None:
        screen = RunListScreen(
            self._rows,
            self._full_cwd,
            refresh_fn=self._refresh_fn,
            initial_run_id=self._initial_run_id,
            open_detail_on_mount=self._open_detail_on_mount,
        )
        self.push_screen(screen)
        self.apply_initial_theme()

    def on_screen_dismiss(self, event: Screen.Dismissed) -> None:
        """Capture action result when a screen is dismissed."""
        if event.result and isinstance(event.result, str):
            self._action_result = event.result
