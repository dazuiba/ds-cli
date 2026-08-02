"""Append a managed non-interactive turn to an existing conversation."""

import os
import sys

from ..config import Config
from ..core import backend_abbrev, parse_new_run_id
from .session_target import resolve_session_target
from .run import _is_adopted_path


def cmd_resume(argv: list[str], config: Config):
    """handoff resume [<run-id|seq>] [--backend <name>] [--session-id <id>]
    [--slug <slug>] [--pro] [--fast] [--cwd <dir>] [--verbose]
    (<input-file|-> | --text <prompt...>)."""
    # Pre-scan switches that should work regardless of position (e.g. after --text).
    verbose = "--verbose" in argv
    fast = "--fast" in argv
    filtered = [a for a in argv if a not in ("--verbose", "--fast")]

    pro = False
    cwd = ""
    backend_arg = ""
    session_id_arg: str | None = None
    slug_arg = ""
    input_src = ""
    text_mode = False
    text_parts = []
    positionals = []

    i = 0
    while i < len(filtered):
        a = filtered[i]
        if a == "-":
            input_src = "-"
        elif a == "--cwd":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --cwd requires a value", file=sys.stderr)
                sys.exit(2)
            cwd = filtered[i]
        elif a == "--backend":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --backend requires a value", file=sys.stderr)
                sys.exit(2)
            backend_arg = filtered[i]
        elif a.startswith("--backend="):
            backend_arg = a.split("=", 1)[1]
        elif a == "--session-id":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --session-id requires a value", file=sys.stderr)
                sys.exit(2)
            session_id_arg = filtered[i]
        elif a.startswith("--session-id="):
            session_id_arg = a.split("=", 1)[1]
        elif a == "--slug":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --slug requires a value", file=sys.stderr)
                sys.exit(2)
            slug_arg = filtered[i]
        elif a.startswith("--slug="):
            slug_arg = a.split("=", 1)[1]
        elif a == "--text":
            text_mode = True
            if input_src:
                print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
                sys.exit(2)
            if i + 1 >= len(filtered):
                print("handoff resume: --text requires a value", file=sys.stderr)
                sys.exit(2)
            if filtered[i + 1] == "--":
                text_parts.extend(filtered[i + 2:])
            else:
                text_parts.extend(filtered[i + 1:])
            break
        elif a.startswith("--text="):
            text_mode = True
            if input_src:
                print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
                sys.exit(2)
            text_parts.append(a.split("=", 1)[1])
            text_parts.extend(filtered[i + 1:])
            break
        elif a == "--pro":
            pro = True
        elif a in ("-h", "--help"):
            from ..main import usage
            usage()
            sys.exit(0)
        elif a.startswith("-") and a != "-":
            print(f"handoff resume: unknown option {a}", file=sys.stderr)
            sys.exit(2)
        else:
            positionals.append(a)
        i += 1

    # In external-session mode the one optional positional is the prompt file;
    # otherwise positionals retain the existing selector + prompt-file meaning.
    if session_id_arg is not None:
        if len(positionals) > 1:
            print(f"handoff resume: unexpected extra argument {positionals[1]}", file=sys.stderr)
            sys.exit(2)
        if positionals:
            if input_src:
                print(f"handoff resume: unexpected extra argument {positionals[0]}", file=sys.stderr)
                sys.exit(2)
            input_src = positionals[0]
        if text_mode and input_src:
            print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
            sys.exit(2)
        selector = ""
    else:
        if len(positionals) > 2:
            print(f"handoff resume: unexpected extra argument {positionals[2]}", file=sys.stderr)
            sys.exit(2)
        selector = positionals[0] if positionals else ""
        if len(positionals) == 2:
            if input_src:
                print(f"handoff resume: unexpected extra argument {positionals[1]}", file=sys.stderr)
                sys.exit(2)
            input_src = positionals[1]

    target = resolve_session_target(
        config,
        command="resume",
        selector=selector,
        backend_arg=backend_arg,
        session_id_arg=session_id_arg,
        cwd_arg=cwd,
        pro_override=True if pro else None,
    )

    # Decide prompt source. Unlike `open`, `resume` always appends a prompt and
    # therefore always enters the managed run pipeline.
    prompt_text = None
    adopted_run_id: str | None = None
    if text_mode:
        prompt_text = " ".join(text_parts)
        if not prompt_text:
            print("handoff resume: --text requires a non-empty value", file=sys.stderr)
            sys.exit(2)
    elif input_src == "-" or (not input_src and not sys.stdin.isatty()):
        prompt_text = sys.stdin.read()
    elif input_src:
        if not os.path.isfile(input_src):
            print(f"handoff resume: input file not found: {input_src}", file=sys.stderr)
            sys.exit(2)
        with open(input_src, encoding="utf-8") as f:
            prompt_text = f.read()

        if _is_adopted_path(input_src):
            # Adopt the run_id pre-allocated by `handoff new`, so the caller
            # knows the .result.md path before the turn is dispatched.
            stem = os.path.basename(input_src)[: -len(".prompt.md")]
            _mmdd, file_b2, _seq_code, _slug = parse_new_run_id(stem)  # type: ignore[misc]
            expected_b2 = backend_abbrev(target.backend_name)
            if file_b2 != expected_b2:
                print(
                    f"handoff resume: adopted file has backend '{file_b2}' but this "
                    f"session resolves to '{target.backend_name}' (abbrev '{expected_b2}'). "
                    f"Create the prompt file with a matching --backend.",
                    file=sys.stderr,
                )
                sys.exit(2)
            adopted_run_id = stem

    if prompt_text is None or not prompt_text.strip():
        print(
            "handoff resume: prompt required; use `handoff open` for an interactive session",
            file=sys.stderr,
        )
        sys.exit(2)

    from .run import _execute
    _execute(
        target.cwd,
        prompt_text,
        target.backend_name,
        target.pro,
        config,
        fast=fast,
        resume_session_id=target.session_id,
        slug=slug_arg or "resume",
        adopted_run_id=adopted_run_id,
        verbose=verbose,
    )
