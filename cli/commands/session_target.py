"""Resolve a conversation target shared by ``handoff open`` and ``resume``."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys

from ..config import Config
from ..core import find_run, get_db, row_value
from ..runtime_info import parse_runtime_info


@dataclass(frozen=True)
class SessionTarget:
    backend_name: str
    session_id: str
    cwd: str
    pro: bool


def resolve_session_target(
    config: Config,
    *,
    command: str,
    selector: str = "",
    backend_arg: str = "",
    session_id_arg: str | None = None,
    cwd_arg: str = "",
    pro_override: bool | None = None,
) -> SessionTarget:
    """Resolve a managed run or an explicit backend/session/cwd triple.

    ``session_id_arg is not None`` selects explicit mode and deliberately avoids
    opening handoff.db. Managed mode inherits backend and cwd from the selected
    run, and pro from the latest run in that session, unless overridden.
    """
    prefix = f"handoff {command}"

    if session_id_arg is not None:
        if selector:
            _fail(prefix, f"<run-id|seq> cannot be combined with --session-id")
        if not session_id_arg:
            _fail(prefix, "--session-id requires a non-empty value")
        if not backend_arg:
            _fail(prefix, "--backend is required with --session-id")
        if not cwd_arg:
            _fail(prefix, "--cwd is required with --session-id")
        _require_cwd(prefix, cwd_arg)
        return SessionTarget(
            backend_name=backend_arg,
            session_id=session_id_arg,
            cwd=cwd_arg,
            pro=bool(pro_override),
        )

    conn = get_db()
    try:
        row = find_run(conn, selector or None)
        if not row:
            print(f"{prefix}: no run found", file=sys.stderr)
            raise SystemExit(1)

        session_id = row_value(row, "session_id", "") or row["uuid"]
        saved_backend = row_value(row, "backend", "") or ""
        if backend_arg and saved_backend and backend_arg != saved_backend:
            _fail(
                prefix,
                f"this conversation belongs to backend '{saved_backend}'; "
                f"it cannot be used with --backend {backend_arg}",
            )

        cwd = cwd_arg or row["cwd"]
        _require_cwd(prefix, cwd)
        if pro_override is None:
            latest = conn.execute(
                "SELECT runtime_info FROM runs "
                "WHERE COALESCE(NULLIF(session_id, ''), uuid) = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            latest_info = parse_runtime_info(
                row_value(latest, "runtime_info", "") if latest else ""
            )
            pro = bool(latest_info.get("pro"))
        else:
            pro = pro_override
        return SessionTarget(
            backend_name=saved_backend or backend_arg or config.default_backend,
            session_id=session_id,
            cwd=cwd,
            pro=pro,
        )
    finally:
        conn.close()


def _require_cwd(prefix: str, cwd: str) -> None:
    if not os.path.isdir(cwd):
        _fail(prefix, f"cwd not found: {cwd}")


def _fail(prefix: str, message: str) -> None:
    print(f"{prefix}: {message}", file=sys.stderr)
    raise SystemExit(2)
