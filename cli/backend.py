"""Backend resolution and command building for handoff.

Given a resolved backend configuration (merged from type_defaults[<type>] + the
backend's own fields in YAML), this module provides:

  - set_backend_env(backend, ...): Set environment variables for the backend CLI
  - build_args(backend, ...): Build the CLI argument list (claude or codex)

Backend types:
  claude — `claude -p ... --output-format stream-json` against any
           anthropic-compatible endpoint; identity flags combine with
           session_flags (`--session-id` fresh / `--resume` continuation).
  codex  — `codex exec --json ...`; a fresh run takes session_flags alone
           (codex assigns the thread id itself), a continuation uses
           continue_id_flags *instead of* session_flags because
           `codex exec resume` accepts a different flag set.

Placeholder substitution:
  {prompt}         — the prompt text
  {session_id}     — session UUID / codex thread id
  {system_prompt}  — configured system prompt
  {model}          — resolved model name (backend's model or pro_model)
  {pro_model}      — backend's pro_model
  {model_reasoning_effort} — resolved Codex reasoning effort
  {cwd}            — working directory of the run
  {home}           — $HOME
"""

from __future__ import annotations

import os
import shlex
import sys
import json
from typing import Optional


def _substitute(text: str, ctx: dict) -> str:
    """Replace {placeholders} in a string using ctx dict."""
    return text.format(**ctx)


def _resolve_env_val(val, ctx: dict):
    """Resolve a config value, handling strings with placeholders."""
    if isinstance(val, str):
        resolved = _substitute(val, ctx)
        return os.path.expanduser(resolved)
    return val


def _base_ctx(
    backend: dict,
    model: str = "",
    pro_model: str = "",
    model_reasoning_effort: str = "",
    cwd: str = "",
) -> dict:
    return {
        "prompt": "",
        "session_id": "",
        "system_prompt": backend.get("_system_prompt", ""),
        "model": model or backend.get("_resolved_model", ""),
        "pro_model": pro_model or backend.get("pro_model", ""),
        "model_reasoning_effort": model_reasoning_effort or backend.get("_resolved_model_reasoning_effort", ""),
        # legacy alias kept so old user configs with {default_model} don't crash
        "default_model": model or backend.get("_resolved_model", ""),
        "cwd": cwd,
        "home": os.path.expanduser("~"),
    }


def backend_type(backend: dict) -> str:
    return backend.get("type", "claude")


def _codex_fast_mode_args(enabled: bool) -> list[str]:
    """Translate handoff's per-run --fast switch into Codex config overrides."""
    if not enabled:
        return []
    return [
        "-c", "features.fast_mode=true",
        "-c", 'service_tier="fast"',
    ]


# Inherited values of these would silently redirect a claude-type backend to
# whatever endpoint/model the *calling* session uses. handoff is routinely
# invoked from inside another claude session (e.g. dispatching opus from a
# DeepSeek-proxied session), so that environment is always polluted — a
# claude-type run must see only what its backend declares.
_CLAUDE_HERMETIC_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


def resolved_backend_env(backend: dict, model: str, pro_model: str = "") -> tuple[list[str], dict[str, str]]:
    """Return env vars cleared and set for this backend after placeholder expansion."""
    ctx = _base_ctx(backend, model=model, pro_model=pro_model)
    unset_keys = list(_CLAUDE_HERMETIC_VARS) if backend_type(backend) == "claude" else []

    resolved_env: dict[str, str] = {}
    env_map = backend.get("env", {})
    for key, val in env_map.items():
        resolved = _resolve_env_val(val, ctx)
        if resolved == "":
            continue
        resolved_env[key] = str(resolved)

    if backend_type(backend) == "claude" and "CLAUDE_CONFIG_DIR" not in resolved_env:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        resolved_env["CLAUDE_CONFIG_DIR"] = config_dir

    return unset_keys, resolved_env


def set_backend_env(backend: dict, model: str, pro_model: str = ""):
    """Set environment variables for the backend CLI.

    Iterates the backend's 'env' mapping, substitutes placeholders,
    and sets each key=value in os.environ. claude-type backends are hermetic:
    known ANTHROPIC_*/model vars are cleared first, so only the backend's own
    env block takes effect.
    """
    unset_keys, resolved_env = resolved_backend_env(backend, model, pro_model)

    for key in unset_keys:
        os.environ.pop(key, None)

    for key, value in resolved_env.items():
        os.environ[key] = value


def format_shell_command(cwd: str, cmd: list[str], unset_keys: list[str], set_env: dict[str, str]) -> str:
    """Render a shell command that reproduces the backend invocation."""
    parts = [f"cd {shlex.quote(cwd)}", "&&", "env"]
    parts.extend(f"-u {shlex.quote(key)}" for key in unset_keys)
    parts.extend(f"{key}={shlex.quote(value)}" for key, value in set_env.items())
    parts.extend(shlex.quote(arg) for arg in cmd)
    return " ".join(parts)


def build_args(
    backend: dict,
    prompt: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    pro_model: Optional[str] = None,
    model_reasoning_effort: Optional[str] = None,
    fast: bool = False,
    resume: bool = False,
    cwd: str = "",
) -> list[str]:
    """Build the backend CLI argument list from a resolved backend config.

    claude type: session_flags carry -p/{prompt}/stream-json/etc.; when a
    session_id is present, `continue_id_flags` (--resume, continuation) or
    `session_id_flags` (--session-id, fresh) are appended on top.

    codex type: a fresh run is session_flags alone (`codex exec --json ...`;
    codex assigns the thread id itself, reported via the thread.started event).
    A continuation is continue_id_flags alone (`codex exec resume --json <id>
    <prompt>`) because `codex exec resume` rejects --sandbox/-C.
    """
    ctx = _base_ctx(
        backend,
        model=model or "",
        pro_model=pro_model or "",
        model_reasoning_effort=model_reasoning_effort or "",
        cwd=cwd,
    )
    ctx["prompt"] = prompt
    ctx["session_id"] = session_id or ""

    command = _resolve_env_val(backend.get("command") or backend.get("claude_command", "claude"), ctx)
    args = [command]

    is_codex = backend_type(backend) == "codex"

    if is_codex and resume and session_id:
        for flag in backend.get("continue_id_flags", []):
            resolved = _resolve_env_val(flag, ctx)
            if resolved:
                args.append(resolved)
    else:
        for flag in backend.get("session_flags", []):
            resolved = _resolve_env_val(flag, ctx)
            if resolved:
                args.append(resolved)

        if is_codex and model_reasoning_effort:
            prompt = args.pop() if args and args[-1] == prompt else None
            args.extend(["-c", f'model_reasoning_effort="{model_reasoning_effort}"'])
            if prompt is not None:
                args.append(prompt)

        if session_id:
            id_flags_key = "continue_id_flags" if resume else "session_id_flags"
            for flag in backend.get(id_flags_key, []):
                resolved = _resolve_env_val(flag, ctx)
                if resolved:
                    args.append(resolved)

    if is_codex and ctx["system_prompt"]:
        # Codex has no --append-system-prompt flag. Its documented
        # developer_instructions config key is injected as developer context;
        # JSON string encoding is also valid TOML basic-string encoding.
        if args and args[-1] == prompt:
            args[-1:-1] = ["-c", f"developer_instructions={json.dumps(ctx['system_prompt'], ensure_ascii=False)}"]
        else:
            args.extend(["-c", f"developer_instructions={json.dumps(ctx['system_prompt'], ensure_ascii=False)}"])

    if is_codex:
        fast_args = _codex_fast_mode_args(fast)
        if args and args[-1] == prompt:
            args[-1:-1] = fast_args
        else:
            args.extend(fast_args)

    return args


def wrap_with_pty(backend: dict, args: list[str]) -> list[str]:
    """Prefix args with the configured PTY wrapper, if any."""
    pty = backend.get("pty", [])
    if not pty:
        return args
    ctx = _base_ctx(backend)
    return [_resolve_env_val(part, ctx) for part in pty] + args


def build_resume_args(
    backend: dict,
    session_id: str,
    pro_model: Optional[str] = None,
) -> list[str]:
    """Build the interactive resume argument list (`claude --resume` / `codex resume`)."""
    ctx = _base_ctx(backend, pro_model=pro_model or "")
    ctx["session_id"] = session_id or ""

    command = _resolve_env_val(backend.get("command") or backend.get("claude_command", "claude"), ctx)
    args = [command]

    for flag in backend.get("resume_flags", []):
        resolved = _resolve_env_val(flag, ctx)
        if resolved:
            args.append(resolved)

    if backend_type(backend) == "codex" and ctx["system_prompt"]:
        # `codex resume` takes the thread id last, so insert config before it.
        args[-1:-1] = ["-c", f"developer_instructions={json.dumps(ctx['system_prompt'], ensure_ascii=False)}"]

    return args


def resolve_backend_model(backend: dict, is_pro: bool = False) -> str:
    """Return the model name for this backend (its own model / pro_model field)."""
    model = backend.get("pro_model" if is_pro else "model") or backend.get("model") or ""
    ctx = {"home": os.path.expanduser("~")}
    return _resolve_env_val(model, ctx) if model else ""


def resolve_backend_reasoning_effort(backend: dict, is_pro: bool = False) -> str:
    """Return the Codex reasoning effort for this backend, if configured."""
    key = "pro_model_reasoning_effort" if is_pro else "model_reasoning_effort"
    effort = backend.get(key) or ""
    ctx = {"home": os.path.expanduser("~")}
    return _resolve_env_val(effort, ctx) if effort else ""


def ensure_backend_token_ready(backend_name: str, backend: dict, user_config_path: str):
    """Fail fast when the selected backend declares a token that isn't usable.

    Only applies to backends whose env carries ANTHROPIC_AUTH_TOKEN (e.g. the
    bundled deepseek). Backends that ride on local login state (opus, codex)
    declare no token and are skipped. An empty value typically means an
    unexpanded ${ENV_VAR} reference (variable not set in the environment).
    """
    env_map = backend.get("env", {})
    if "ANTHROPIC_AUTH_TOKEN" not in env_map:
        return
    token = env_map.get("ANTHROPIC_AUTH_TOKEN")
    if isinstance(token, str) and token.startswith("<"):
        print(
            f"handoff: backend '{backend_name}' still uses placeholder token {token}. "
            f"Edit {user_config_path} and set a real ANTHROPIC_AUTH_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not token:
        print(
            f"handoff: backend '{backend_name}' has an empty ANTHROPIC_AUTH_TOKEN. "
            f"Set it in {user_config_path}, or export the environment variable it "
            f"references (e.g. DEEPSEEK_API_KEY).",
            file=sys.stderr,
        )
        sys.exit(2)
