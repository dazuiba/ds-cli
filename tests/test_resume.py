import io
from types import SimpleNamespace

import pytest

from cli.commands import open as open_command
from cli.commands import resume, run, session_target


class _InteractiveStdin(io.StringIO):
    def isatty(self):
        return True


def _config():
    return SimpleNamespace(default_backend="default")


def test_external_session_open_skips_database(monkeypatch, tmp_path):
    opened = {}

    def fail_get_db():
        raise AssertionError("external session mode must not open handoff.db")

    def fake_open(config, backend, session_id, cwd, pro, verbose=False):
        opened.update(
            backend=backend,
            session_id=session_id,
            cwd=cwd,
            pro=pro,
            verbose=verbose,
        )

    monkeypatch.setattr(session_target, "get_db", fail_get_db)
    monkeypatch.setattr(open_command, "_open_interactive", fake_open)

    open_command.cmd_open(
        [
            "--backend",
            "codex",
            "--session-id",
            "external-session",
            "--cwd",
            str(tmp_path),
            "--verbose",
        ],
        _config(),
    )

    assert opened == {
        "backend": "codex",
        "session_id": "external-session",
        "cwd": str(tmp_path),
        "pro": False,
        "verbose": True,
    }


def test_external_session_with_prompt_enters_run_pipeline(monkeypatch, tmp_path):
    executed = {}

    def fail_get_db():
        raise AssertionError("external session resolution must not open handoff.db")

    def fake_execute(cwd, prompt, backend, pro, config, **kwargs):
        executed.update(
            cwd=cwd,
            prompt=prompt,
            backend=backend,
            pro=pro,
            fast=kwargs["fast"],
            resume_session_id=kwargs["resume_session_id"],
            slug=kwargs["slug"],
        )

    monkeypatch.setattr(session_target, "get_db", fail_get_db)
    monkeypatch.setattr("cli.commands.run._execute", fake_execute)

    resume.cmd_resume(
        [
            "--backend=codex",
            "--session-id=external-session",
            "--cwd",
            str(tmp_path),
            "--fast",
            "--slug",
            "follow-up",
            "--text",
            "continue",
            "the task",
        ],
        _config(),
    )

    assert executed == {
        "cwd": str(tmp_path),
        "prompt": "continue the task",
        "backend": "codex",
        "pro": False,
        "fast": True,
        "resume_session_id": "external-session",
        "slug": "follow-up",
    }


def test_resume_defaults_fast_off_for_each_invocation(monkeypatch, tmp_path):
    executed = {}

    def fail_get_db():
        raise AssertionError("external session resolution must not open handoff.db")

    def fake_execute(cwd, prompt, backend, pro, config, **kwargs):
        executed.update(fast=kwargs["fast"], pro=pro)

    monkeypatch.setattr(session_target, "get_db", fail_get_db)
    monkeypatch.setattr("cli.commands.run._execute", fake_execute)

    resume.cmd_resume(
        [
            "--backend=codex",
            "--session-id=external-session",
            "--cwd",
            str(tmp_path),
            "--text",
            "continue without fast",
        ],
        _config(),
    )

    assert executed == {"fast": False, "pro": False}


def test_run_forwards_fast_as_per_invocation_switch(monkeypatch, tmp_path):
    executed = {}

    def fake_execute(cwd, prompt, backend, pro, config, **kwargs):
        executed.update(
            cwd=cwd,
            prompt=prompt,
            backend=backend,
            pro=pro,
            fast=kwargs["fast"],
        )

    monkeypatch.setattr(run, "_execute", fake_execute)

    run.cmd_run(
        ["--backend=codex", "--cwd", str(tmp_path), "--text", "do it", "--fast"],
        _config(),
    )

    assert executed == {
        "cwd": str(tmp_path),
        "prompt": "do it",
        "backend": "codex",
        "pro": False,
        "fast": True,
    }


def test_resume_adopts_preallocated_run_id(monkeypatch, tmp_path):
    """A .prompt.md from `handoff new` keeps its run_id, so the caller can
    predict the .result.md path before dispatching."""
    executed = {}

    prompt_file = tmp_path / "0720-op-07-follow-up.prompt.md"
    prompt_file.write_text("continue the task", encoding="utf-8")

    def fake_execute(cwd, prompt, backend, pro, config, **kwargs):
        executed.update(prompt=prompt, adopted_run_id=kwargs["adopted_run_id"])

    monkeypatch.setattr(session_target, "get_db", lambda: pytest.fail("must not open db"))
    monkeypatch.setattr("cli.commands.run.TASKS_DIR", str(tmp_path))
    monkeypatch.setattr("cli.commands.run._execute", fake_execute)

    resume.cmd_resume(
        [
            "--backend=opus",
            "--session-id=external-session",
            "--cwd",
            str(tmp_path),
            str(prompt_file),
        ],
        _config(),
    )

    assert executed == {
        "prompt": "continue the task",
        "adopted_run_id": "0720-op-07-follow-up",
    }


def test_resume_rejects_adopted_file_from_another_backend(monkeypatch, tmp_path, capsys):
    prompt_file = tmp_path / "0720-ds-07-follow-up.prompt.md"
    prompt_file.write_text("continue the task", encoding="utf-8")

    monkeypatch.setattr(session_target, "get_db", lambda: pytest.fail("must not open db"))
    monkeypatch.setattr("cli.commands.run.TASKS_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exc:
        resume.cmd_resume(
            [
                "--backend=opus",
                "--session-id=external-session",
                "--cwd",
                str(tmp_path),
                str(prompt_file),
            ],
            _config(),
        )

    assert exc.value.code == 2
    assert "adopted file has backend 'ds'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--session-id", "id", "--cwd", ".", "--text", "continue"], "--backend is required"),
        (["--session-id", "id", "--backend", "codex", "--text", "continue"], "--cwd is required"),
    ],
)
def test_external_session_requires_backend_and_cwd(argv, message, capsys):
    with pytest.raises(SystemExit) as exc:
        resume.cmd_resume(argv, _config())

    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_external_session_rejects_empty_id(capsys):
    with pytest.raises(SystemExit) as exc:
        resume.cmd_resume(
            ["--session-id=", "--backend", "codex", "--cwd", ".", "--text", "continue"],
            _config(),
        )

    assert exc.value.code == 2
    assert "--session-id requires a non-empty value" in capsys.readouterr().err


def test_resume_without_prompt_directs_user_to_open(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(resume.sys, "stdin", _InteractiveStdin())

    with pytest.raises(SystemExit) as exc:
        resume.cmd_resume(
            [
                "--backend",
                "codex",
                "--session-id",
                "session-1",
                "--cwd",
                str(tmp_path),
            ],
            _config(),
        )

    assert exc.value.code == 2
    assert "use `handoff open`" in capsys.readouterr().err
