"""Helpers for run runtime metadata stored as JSON in runs.runtime_info."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from typing import Any


DEFAULT_USAGE = {
    "turns": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cached_input_tokens": 0,
    "reasoning_output_tokens": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
}


def parse_runtime_info(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def dump_runtime_info(info: dict[str, Any]) -> str:
    return json.dumps(info, sort_keys=True, separators=(",", ":"))


def runtime_pid(value: str | None) -> int:
    info = parse_runtime_info(value)
    try:
        return int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def runtime_usage(value: str | None) -> dict[str, Any]:
    info = parse_runtime_info(value)
    usage = info.get("usage")
    return normalize_usage(usage if isinstance(usage, dict) else {})


def format_usage(value: str | None) -> str:
    return format_usage_value(runtime_usage(value))


def format_usage_value(usage: dict[str, Any] | None) -> str:
    usage = normalize_usage(usage or {})
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    cache_read = _safe_int(usage.get("cache_read_input_tokens"))
    if input_tokens == 0 and output_tokens == 0 and cache_read == 0:
        return "-"
    return f"{input_tokens}/{output_tokens}/{cache_read}"


def format_usage_detail(value: str | None) -> str:
    return format_usage_detail_value(runtime_usage(value))


def format_usage_detail_value(usage: dict[str, Any] | None) -> str:
    usage = normalize_usage(usage or {})
    turns = _safe_int(usage.get("turns"))
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    cache_read = _safe_int(usage.get("cache_read_input_tokens"))
    cache_create = _safe_int(usage.get("cache_creation_input_tokens"))
    reasoning = _safe_int(usage.get("reasoning_output_tokens"))
    total = _safe_int(usage.get("total_tokens"))
    cost = _safe_float(usage.get("cost_usd"))

    parts = [f"tokens {input_tokens}/{output_tokens}/{cache_read}/{cache_create}"]
    if total:
        parts.append(f"total {total}")
    if reasoning:
        parts.append(f"reasoning {reasoning}")
    if turns:
        parts.append(f"turns {turns}")
    if cost:
        parts.append(f"${cost:.4f}")
    return " · ".join(parts)


def usage_is_empty(usage: dict[str, Any] | None) -> bool:
    usage = normalize_usage(usage or {})
    return not any(_safe_int(usage.get(key)) for key in DEFAULT_USAGE if key != "cost_usd") and not _safe_float(usage.get("cost_usd"))


def update_runtime_info(
    conn,
    uid: str,
    *,
    pid: int | None = None,
    usage: dict[str, Any] | None = None,
    model: str | None = None,
    pro: bool | None = None,
    fast: bool | None = None,
    process_start_token: str | None = None,
) -> None:
    row = conn.execute("SELECT runtime_info FROM runs WHERE uuid = ?", (uid,)).fetchone()
    info = parse_runtime_info(row["runtime_info"] if row else None)
    if pid is not None:
        if pid:
            info["pid"] = int(pid)
        else:
            info.pop("pid", None)
            info.pop("process_start_token", None)
    if process_start_token is not None:
        if process_start_token:
            info["process_start_token"] = process_start_token
        else:
            info.pop("process_start_token", None)
    if usage is not None:
        info["usage"] = normalize_usage(usage)
    if model is not None:
        info["model"] = model
    if pro is not None:
        info["pro"] = bool(pro)
    if fast is not None:
        # Per-run display metadata only. Session resolution deliberately does
        # not inherit this value; every resume must opt in with --fast again.
        info["fast"] = bool(fast)
    conn.execute(
        "UPDATE runs SET runtime_info = ? WHERE uuid = ?",
        (dump_runtime_info(info), uid),
    )


def normalize_usage(usage: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_USAGE)
    if not isinstance(usage, dict):
        return normalized

    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict) and "cache_creation_input_tokens" not in usage:
        usage = dict(usage)
        usage["cache_creation_input_tokens"] = (
            _safe_int(cache_creation.get("ephemeral_1h_input_tokens"))
            + _safe_int(cache_creation.get("ephemeral_5m_input_tokens"))
        )

    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict) and "reasoning_output_tokens" not in usage:
        usage = dict(usage)
        usage["reasoning_output_tokens"] = output_details.get("reasoning_tokens", 0)

    cached = usage.get("cached_input_tokens")
    cache_read = usage.get("cache_read_input_tokens")
    if cache_read is None and cached is not None:
        usage = dict(usage)
        usage["cache_read_input_tokens"] = cached

    for key in DEFAULT_USAGE:
        value = usage.get(key)
        if value is None:
            continue
        if key == "cost_usd":
            normalized[key] = _safe_float(value)
        else:
            normalized[key] = _safe_int(value)

    if normalized["cached_input_tokens"] == 0 and normalized["cache_read_input_tokens"]:
        normalized["cached_input_tokens"] = normalized["cache_read_input_tokens"]
    return normalized


def merge_usage(current: dict[str, Any] | None, update: dict[str, Any] | None, *, accumulate_turn: bool = False) -> dict[str, Any]:
    merged = normalize_usage(current or {})
    if not update:
        return merged
    incoming = normalize_usage(update)
    if accumulate_turn:
        for key in DEFAULT_USAGE:
            if key == "cost_usd":
                merged[key] = _safe_float(merged.get(key)) + _safe_float(incoming.get(key))
            else:
                merged[key] = _safe_int(merged.get(key)) + _safe_int(incoming.get(key))
        return merged

    for key, value in incoming.items():
        if value:
            merged[key] = value
    return merged


def usage_from_json_line(line: str, backend_type: str = "") -> tuple[dict[str, Any] | None, bool]:
    """Return (usage, is_final_or_complete_turn) parsed from one JSONL line."""
    line = line.strip()
    if not line.startswith("{"):
        return None, False
    try:
        obj = json.loads(line)
    except ValueError:
        return None, False
    if not isinstance(obj, dict):
        return None, False

    etype = obj.get("type")
    if etype == "result":
        usage = obj.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        normalized = normalize_usage(usage)
        normalized["turns"] = _safe_int(obj.get("num_turns", 0))
        normalized["cost_usd"] = _safe_float(obj.get("total_cost_usd", 0.0))
        return normalized, True

    if etype == "turn.completed":
        usage = obj.get("usage") or {}
        if not isinstance(usage, dict):
            return {"turns": 1}, True
        normalized = normalize_usage(usage)
        normalized["turns"] = 1
        normalized["cost_usd"] = _safe_float(usage.get("total_cost_usd", usage.get("cost_usd", 0.0)))
        return normalized, True

    if backend_type == "claude" or not backend_type:
        if etype == "stream_event":
            event = obj.get("event") or {}
            if not isinstance(event, dict):
                return None, False
            if event.get("type") == "message_start":
                usage = ((event.get("message") or {}).get("usage") or {})
                if isinstance(usage, dict):
                    return normalize_usage(usage), False
            if event.get("type") == "message_delta":
                usage = event.get("usage") or {}
                if isinstance(usage, dict):
                    return normalize_usage(usage), False

    return None, False


def scan_jsonl_usage(jsonl_path: str, backend_type: str = "") -> dict[str, Any]:
    usage = dict(DEFAULT_USAGE)
    try:
        with open(jsonl_path, "r", encoding="utf-8") as handle:
            for line in handle:
                parsed, is_final = usage_from_json_line(line, backend_type)
                if not parsed:
                    continue
                if backend_type == "codex" and is_final:
                    usage = merge_usage(usage, parsed, accumulate_turn=True)
                else:
                    usage = merge_usage(usage, parsed)
    except (FileNotFoundError, OSError):
        pass
    return usage


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_group_alive(pid: int) -> bool:
    """Return whether the process group whose ID is *pid* still exists.

    Signal 0 is a permission/existence probe and does not deliver a signal to
    any member of the process group.  ``execute_run`` starts each run in a new
    session, so its saved process PID is also the process-group ID.  Probing
    that saved ID directly keeps detecting remaining children after the group
    leader exits.
    """
    if pid <= 0:
        return False
    try:
        killpg = getattr(os, "killpg", None)
        if killpg is None:
            return process_alive(pid)
        killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_start_token(pid: int) -> str:
    """Return a stable OS-reported start marker for *pid*, when available."""
    if pid <= 0:
        return ""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def reconcile_running_runs(conn, now: float | None = None) -> None:
    """Turn unverifiable ``running`` rows into ``error`` atomically."""
    observed_at = time.time() if now is None else float(now)
    rows = conn.execute(
        "SELECT uuid, runtime_info FROM runs WHERE status = 'running'"
    ).fetchall()
    changed = False

    for row in rows:
        raw_info = row["runtime_info"]
        info = parse_runtime_info(raw_info)
        pid = runtime_pid(raw_info)
        alive = bool(pid) and process_group_alive(pid)
        saved_start = info.get("process_start_token")
        if alive and isinstance(saved_start, str) and saved_start:
            current_start = process_start_token(pid)
            if current_start and current_start != saved_start:
                alive = False
        if alive:
            continue

        info.pop("pid", None)
        info.pop("process_start_token", None)
        info.pop("missing_since", None)  # clean up the former grace-period format
        info.pop("stale_at", None)
        info.pop("stale_reason", None)
        info["error_at"] = observed_at
        info["error_reason"] = "process_missing"
        conn.execute(
            "UPDATE runs SET status = 'error', runtime_info = ? "
            "WHERE uuid = ? AND status = 'running'",
            (dump_runtime_info(info), row["uuid"]),
        )
        changed = True

    if changed:
        conn.commit()


def kill_process_group(pid: int, *, grace_seconds: float = 3.0) -> None:
    if pid <= 0:
        raise ProcessLookupError(pid)
    # execute_run starts the subprocess with setsid, so the persisted PID is
    # also its PGID. Use it directly even if the original group leader exited.
    pgid = pid
    os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not process_group_alive(pgid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
