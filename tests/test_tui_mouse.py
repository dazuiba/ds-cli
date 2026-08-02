import asyncio
from types import SimpleNamespace

from textual import events
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Markdown, TabbedContent

from cli import config, tui
from cli.commands import list as list_command
from cli.jsonl_viewer import JsonlViewerScreen


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _query, _params=()):
        return _Result(self._rows)

    def close(self):
        self.closed = True


class _TTY:
    def isatty(self):
        return True


def test_list_enables_mouse_for_detail_pane_scrolling(monkeypatch):
    connection = _Connection([{"run_id": "0729-ds-01"}])
    run_calls = []

    class FakeRunListApp:
        action_result = None

        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, **kwargs):
            run_calls.append(kwargs)

    monkeypatch.setattr(list_command, "get_db", lambda: connection)
    monkeypatch.setattr(list_command.sys, "stdin", _TTY())
    monkeypatch.setattr(list_command.sys, "stdout", _TTY())
    monkeypatch.setattr(tui, "RunListApp", FakeRunListApp)
    monkeypatch.setattr(config, "read_tui_theme", lambda: "textual-dark")

    list_command.cmd_list([], SimpleNamespace())

    assert run_calls == [{"mouse": True}]
    assert connection.closed is True


def test_prompt_has_one_vertical_scroll_owner(tmp_path):
    async def run_test():
        prompt_path = tmp_path / "prompt.md"
        out_path = tmp_path / "output.out"
        prompt_path.write_text(
            "Before\n\n```json\n"
            + "\n".join(
                f'{{\"line\": {line}, \"value\": \"{"x" * 100}\"}}'
                for line in range(45)
            )
            + "\n```\n\nAfter",
            encoding="utf-8",
        )
        out_path.write_text("", encoding="utf-8")

        viewer = JsonlViewerScreen(
            jsonl_path=str(tmp_path / "stream.jsonl"),
            prompt_path=str(prompt_path),
            out_path=str(out_path),
            result_path=str(tmp_path / "result.md"),
            run_info={
                "backend": "codex",
                "run_id": "0729-cx-21",
                "date": "2026-07-29",
                "cwd": str(tmp_path),
            },
        )

        class ViewerApp(App):
            def on_mount(self):
                self.push_screen(viewer)

        async with ViewerApp().run_test(size=(100, 35)) as pilot:
            viewer.query_one(TabbedContent).active = "prompt"
            await pilot.pause()
            await pilot.pause()

            document_scroll = viewer.query_one("#prompt_scroll", VerticalScroll)
            markdown = viewer.query_one("#prompt_md", Markdown)
            code_fence = viewer.query_one("MarkdownFence")

            assert document_scroll.max_scroll_y > 0
            assert markdown.max_scroll_y == 0
            assert code_fence.allow_vertical_scroll is False
            assert code_fence.max_scroll_x > 0

            for _ in range(4):
                await pilot._post_mouse_events(
                    [events.MouseScrollDown],
                    offset=(5, 10),
                )

            assert document_scroll.scroll_y > 0
            assert code_fence.scroll_y == 0

            upward_positions = []
            for _ in range(8):
                await pilot._post_mouse_events(
                    [events.MouseScrollUp],
                    offset=(5, 10),
                )
                upward_positions.append(document_scroll.scroll_y)

            assert upward_positions == sorted(upward_positions, reverse=True)
            assert upward_positions[-1] == 0
            assert code_fence.scroll_y == 0

    asyncio.run(run_test())


def test_native_clipboard_uses_pbcopy_on_macos(monkeypatch):
    calls = []

    monkeypatch.setattr(tui.sys, "platform", "darwin")
    monkeypatch.setattr(tui.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        tui.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    assert tui._copy_to_native_clipboard("selected text") is True
    assert calls == [
        (
            ["pbcopy"],
            {
                "input": "selected text",
                "text": True,
                "check": True,
                "stdout": tui.subprocess.DEVNULL,
                "stderr": tui.subprocess.DEVNULL,
            },
        )
    ]


def test_copy_key_prefers_mouse_selection_over_session_uuid(monkeypatch, tmp_path):
    async def run_test():
        viewer = JsonlViewerScreen(
            jsonl_path=str(tmp_path / "stream.jsonl"),
            prompt_path=str(tmp_path / "prompt.md"),
            out_path=str(tmp_path / "output.out"),
            result_path=str(tmp_path / "result.md"),
            run_info={
                "backend": "codex",
                "run_id": "0802-cx-01",
                "uuid": "session-uuid-must-not-be-copied",
                "date": "2026-08-02",
                "cwd": str(tmp_path),
            },
        )

        class ViewerApp(tui.HandoffTuiApp):
            def on_mount(self):
                self.push_screen(viewer)

        native_copies = []
        monkeypatch.setattr(
            tui,
            "_copy_to_native_clipboard",
            lambda text: native_copies.append(text) or True,
        )

        app = ViewerApp(theme_name="textual-dark")
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.mouse_down("#output_log", offset=(0, 0))
            await pilot._post_mouse_events(
                [events.MouseMove],
                "#output_log",
                offset=(9, 0),
                button=1,
            )
            await pilot.mouse_up("#output_log", offset=(9, 0))
            await pilot.pause()

            selected_text = app.screen.get_selected_text()
            assert selected_text
            assert "session-uuid-must-not-be-copied" not in selected_text
            assert app.clipboard == selected_text
            assert native_copies == [selected_text]

            await pilot.press("c")

            assert app.clipboard == selected_text
            assert native_copies == [selected_text, selected_text]

    asyncio.run(run_test())


def test_ctrl_c_copies_mouse_selection_from_run_table(monkeypatch):
    async def run_test():
        class TableApp(tui.HandoffTuiApp):
            def compose(self):
                yield tui._SelectableDataTable(id="table")

            def on_mount(self):
                table = self.query_one("#table", tui._SelectableDataTable)
                table.add_columns("RUN", "STATUS")
                table.add_row("0802-cx-01", "completed")

        native_copies = []
        monkeypatch.setattr(
            tui,
            "_copy_to_native_clipboard",
            lambda text: native_copies.append(text) or True,
        )

        app = TableApp(theme_name="textual-dark")
        async with app.run_test(size=(50, 8)) as pilot:
            await pilot.pause()
            await pilot.mouse_down("#table", offset=(1, 1))
            await pilot._post_mouse_events(
                [events.MouseMove],
                "#table",
                offset=(11, 1),
                button=1,
            )
            await pilot.mouse_up("#table", offset=(11, 1))
            await pilot.pause()

            selected_text = app.screen.get_selected_text()
            assert selected_text
            assert "0802-cx-01" in selected_text
            assert app.clipboard == selected_text
            assert native_copies == [selected_text]

            await pilot.press("ctrl+c")

            assert app.clipboard == selected_text
            assert native_copies == [selected_text, selected_text]

    asyncio.run(run_test())
