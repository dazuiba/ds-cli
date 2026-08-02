from types import SimpleNamespace

import pytest

from cli.commands import session_target


class _Connection:
    def __init__(self, latest_runtime_info='{"pro":true}'):
        self.closed = False
        self.latest_runtime_info = latest_runtime_info

    def execute(self, query, params):
        assert "ORDER BY created_at DESC" in query
        assert params == ("session-1",)
        return SimpleNamespace(
            fetchone=lambda: {"runtime_info": self.latest_runtime_info}
        )

    def close(self):
        self.closed = True


def _config(default_backend="default"):
    return SimpleNamespace(default_backend=default_backend)


def test_explicit_target_does_not_open_database(monkeypatch, tmp_path):
    def fail_get_db():
        raise AssertionError("explicit target must not open handoff.db")

    monkeypatch.setattr(session_target, "get_db", fail_get_db)

    target = session_target.resolve_session_target(
        _config(),
        command="open",
        backend_arg="codex",
        session_id_arg="session-1",
        cwd_arg=str(tmp_path),
    )

    assert target == session_target.SessionTarget(
        backend_name="codex",
        session_id="session-1",
        cwd=str(tmp_path),
        pro=False,
    )


def test_managed_target_inherits_backend_cwd_and_pro(monkeypatch, tmp_path):
    conn = _Connection()
    row = {
        "uuid": "run-uuid",
        "session_id": "session-1",
        "backend": "opus",
        "cwd": str(tmp_path),
        "runtime_info": '{"pro":true}',
    }
    monkeypatch.setattr(session_target, "get_db", lambda: conn)
    monkeypatch.setattr(session_target, "find_run", lambda actual, selector: row)

    target = session_target.resolve_session_target(
        _config(),
        command="resume",
        selector="known-run",
    )

    assert target == session_target.SessionTarget(
        backend_name="opus",
        session_id="session-1",
        cwd=str(tmp_path),
        pro=True,
    )
    assert conn.closed is True


def test_managed_target_reads_pro_from_latest_session_run(monkeypatch, tmp_path):
    conn = _Connection(latest_runtime_info='{"pro":true,"fast":false}')
    first_row = {
        "uuid": "run-uuid",
        "session_id": "session-1",
        "backend": "codex",
        "cwd": str(tmp_path),
        "runtime_info": '{"pro":false,"fast":true}',
    }
    monkeypatch.setattr(session_target, "get_db", lambda: conn)
    monkeypatch.setattr(session_target, "find_run", lambda actual, selector: first_row)

    target = session_target.resolve_session_target(
        _config(),
        command="resume",
        selector="first-run",
    )

    assert target.pro is True
    assert conn.closed is True


def test_explicit_target_rejects_selector(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        session_target,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("must not open DB")),
    )

    with pytest.raises(SystemExit) as exc:
        session_target.resolve_session_target(
            _config(),
            command="open",
            selector="known-run",
            backend_arg="codex",
            session_id_arg="session-1",
            cwd_arg=str(tmp_path),
        )

    assert exc.value.code == 2
    assert "cannot be combined with --session-id" in capsys.readouterr().err
