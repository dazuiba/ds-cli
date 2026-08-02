import json
import sqlite3

import cli.runtime_info as runtime_info
from cli.runtime_info import (
    dump_runtime_info,
    format_usage_detail_value,
    format_usage_value,
    kill_process_group,
    merge_usage,
    parse_runtime_info,
    process_group_alive,
    process_start_token,
    reconcile_running_runs,
    update_runtime_info,
    usage_from_json_line,
)


def test_claude_result_usage_includes_cache_fields():
    line = json.dumps(
        {
            "type": "result",
            "num_turns": 3,
            "total_cost_usd": 1.23,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 7,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 2,
                    "ephemeral_5m_input_tokens": 3,
                },
            },
        }
    )

    usage, is_final = usage_from_json_line(line, "claude")

    assert is_final is True
    assert usage["turns"] == 3
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["cache_read_input_tokens"] == 7
    assert usage["cache_creation_input_tokens"] == 5
    assert usage["cost_usd"] == 1.23
    assert format_usage_value(usage) == "10/5/7"


def test_codex_cached_input_tokens_are_cache_read():
    line = json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 9,
                "reasoning_output_tokens": 2,
                "total_tokens": 149,
            },
        }
    )

    usage, is_final = usage_from_json_line(line, "codex")

    assert is_final is True
    assert usage["turns"] == 1
    assert usage["cache_read_input_tokens"] == 40
    assert usage["cached_input_tokens"] == 40
    assert usage["reasoning_output_tokens"] == 2
    assert format_usage_value(usage) == "100/9/40"
    assert "reasoning 2" in format_usage_detail_value(usage)


def test_accumulate_turn_merges_all_token_fields():
    current = {"turns": 1, "input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 4}
    update = {"turns": 1, "input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 5}

    merged = merge_usage(current, update, accumulate_turn=True)

    assert merged["turns"] == 2
    assert merged["input_tokens"] == 30
    assert merged["output_tokens"] == 5
    assert merged["cache_read_input_tokens"] == 9


def _runtime_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE runs (uuid TEXT PRIMARY KEY, status TEXT, runtime_info TEXT)"
    )
    return conn


def _insert_running(conn, uid="run-1", info=None, *, status="running"):
    conn.execute(
        "INSERT INTO runs (uuid, status, runtime_info) VALUES (?, ?, ?)",
        (uid, status, dump_runtime_info(info or {})),
    )
    conn.commit()


def _run(conn, uid="run-1"):
    return conn.execute("SELECT * FROM runs WHERE uuid = ?", (uid,)).fetchone()


def test_process_group_alive_probes_group_without_delivering_a_signal(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_info.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))

    assert process_group_alive(123) is True
    assert calls == [(123, 0)]


def test_kill_process_group_uses_saved_pid_as_pgid(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_info.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pgid: False)

    kill_process_group(123)

    assert calls == [(123, runtime_info.signal.SIGTERM)]


def test_reconcile_alive_run_is_unchanged(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123})
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pid: True)

    reconcile_running_runs(conn, now=10.0)

    row = _run(conn)
    assert row["status"] == "running"
    assert parse_runtime_info(row["runtime_info"]) == {"pid": 123}


def test_process_start_token_reads_os_marker(monkeypatch):
    result = runtime_info.subprocess.CompletedProcess([], 0, "Sun Jul 20 12:00:00 2026\n", "")
    monkeypatch.setattr(runtime_info.subprocess, "run", lambda *args, **kwargs: result)

    assert process_start_token(123) == "Sun Jul 20 12:00:00 2026"


def test_reconcile_missing_process_is_error_immediately(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123, "usage": {"turns": 1}})
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pid: False)

    reconcile_running_runs(conn, now=100.5)

    row = _run(conn)
    assert row["status"] == "error"
    assert parse_runtime_info(row["runtime_info"]) == {
        "error_at": 100.5,
        "error_reason": "process_missing",
        "usage": {"turns": 1},
    }


def test_reconcile_detects_pid_reuse_from_start_token(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123, "process_start_token": "old-start"})
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pid: True)
    monkeypatch.setattr(runtime_info, "process_start_token", lambda pid: "new-start")

    reconcile_running_runs(conn, now=30.0)

    row = _run(conn)
    assert row["status"] == "error"
    assert parse_runtime_info(row["runtime_info"]) == {
        "error_at": 30.0,
        "error_reason": "process_missing",
    }


def test_reconcile_same_pid_start_token_stays_running(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123, "process_start_token": "same-start"})
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pid: True)
    monkeypatch.setattr(runtime_info, "process_start_token", lambda pid: "same-start")

    reconcile_running_runs(conn, now=25.0)

    row = _run(conn)
    assert row["status"] == "running"
    assert parse_runtime_info(row["runtime_info"]) == {
        "pid": 123,
        "process_start_token": "same-start",
    }


def test_reconcile_run_without_pid_is_error(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"model": "codex"})
    monkeypatch.setattr(
        runtime_info,
        "process_group_alive",
        lambda pid: (_ for _ in ()).throw(AssertionError("must not probe without a PID")),
    )

    reconcile_running_runs(conn, now=100.0)

    row = _run(conn)
    assert row["status"] == "error"
    assert parse_runtime_info(row["runtime_info"]) == {
        "error_at": 100.0,
        "error_reason": "process_missing",
        "model": "codex",
    }


def test_clearing_pid_also_clears_process_identity():
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123, "process_start_token": "start"})

    update_runtime_info(conn, "run-1", pid=0)
    conn.commit()

    assert parse_runtime_info(_run(conn)["runtime_info"]) == {}


def test_runtime_info_records_fast_for_current_run():
    conn = _runtime_db()
    _insert_running(conn)

    update_runtime_info(conn, "run-1", model="gpt-5.6", pro=True, fast=False)
    conn.commit()

    assert parse_runtime_info(_run(conn)["runtime_info"]) == {
        "fast": False,
        "model": "gpt-5.6",
        "pro": True,
    }


def test_reconcile_conditional_error_update_and_idempotence(monkeypatch):
    conn = _runtime_db()
    _insert_running(conn, info={"pid": 123})

    def race_to_completed(pid):
        conn.execute("UPDATE runs SET status = 'completed' WHERE uuid = 'run-1'")
        return False

    monkeypatch.setattr(runtime_info, "process_group_alive", race_to_completed)
    reconcile_running_runs(conn, now=20.0)
    assert _run(conn)["status"] == "completed"

    # An error row is no longer selected, so reconciliation is idempotent.
    conn.execute("UPDATE runs SET status = 'running' WHERE uuid = 'run-1'")
    conn.execute(
        "UPDATE runs SET runtime_info = ? WHERE uuid = 'run-1'",
        (dump_runtime_info({"pid": 123}),),
    )
    conn.commit()
    monkeypatch.setattr(runtime_info, "process_group_alive", lambda pid: False)
    reconcile_running_runs(conn, now=20.0)
    error_info = _run(conn)["runtime_info"]
    reconcile_running_runs(conn, now=30.0)
    assert _run(conn)["status"] == "error"
    assert _run(conn)["runtime_info"] == error_info
