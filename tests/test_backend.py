from cli.backend import build_args, build_resume_args, resolve_backend_reasoning_effort


def _codex_backend(**overrides):
    backend = {
        "type": "codex",
        "command": "codex",
        "session_flags": [
            "exec",
            "--json",
            "-m",
            "{model}",
            "-C",
            "{cwd}",
            "{prompt}",
        ],
        "continue_id_flags": [
            "exec",
            "resume",
            "--json",
            "{session_id}",
            "{prompt}",
        ],
    }
    backend.update(overrides)
    return backend


def test_codex_default_reasoning_effort_is_omitted():
    backend = _codex_backend(pro_model_reasoning_effort="xhigh")

    effort = resolve_backend_reasoning_effort(backend, is_pro=False)
    args = build_args(backend, "hello", model="gpt-5.5", model_reasoning_effort=effort, cwd="/repo")

    assert "model_reasoning_effort" not in " ".join(args)
    assert args[-1] == "hello"


def test_codex_pro_reasoning_effort_is_inserted_before_prompt():
    backend = _codex_backend(pro_model_reasoning_effort="xhigh")

    effort = resolve_backend_reasoning_effort(backend, is_pro=True)
    args = build_args(backend, "hello", model="gpt-5.5", model_reasoning_effort=effort, cwd="/repo")

    assert args[-3:] == ["-c", 'model_reasoning_effort="xhigh"', "hello"]


def test_codex_resume_omits_reasoning_effort_override():
    backend = _codex_backend(pro_model_reasoning_effort="xhigh")

    effort = resolve_backend_reasoning_effort(backend, is_pro=True)
    args = build_args(
        backend,
        "hello",
        session_id="session-1",
        model="gpt-5.5",
        model_reasoning_effort=effort,
        resume=True,
        cwd="/repo",
    )

    assert args == ["codex", "exec", "resume", "--json", "session-1", "hello"]


def test_codex_system_prompt_uses_developer_instructions_config():
    backend = _codex_backend(_system_prompt="base\n\nbackend extra")

    args = build_args(backend, "hello", model="gpt-5.5", cwd="/repo")

    assert args[-3:] == [
        "-c",
        'developer_instructions="base\\n\\nbackend extra"',
        "hello",
    ]


def test_codex_resume_keeps_developer_instructions_out_of_user_prompt():
    backend = _codex_backend(_system_prompt="backend extra")

    args = build_args(backend, "hello", session_id="session-1", resume=True)

    assert args == [
        "codex", "exec", "resume", "--json", "session-1",
        "-c", 'developer_instructions="backend extra"', "hello",
    ]


def test_codex_fast_service_tier_is_inserted_before_prompt():
    backend = _codex_backend()

    args = build_args(backend, "hello", model="gpt-5.6", fast=True, cwd="/repo")

    assert args[-5:] == [
        "-c", "features.fast_mode=true",
        "-c", 'service_tier="fast"',
        "hello",
    ]


def test_codex_fast_service_tier_is_forwarded_on_noninteractive_resume():
    backend = _codex_backend()

    args = build_args(backend, "hello", session_id="session-1", fast=True, resume=True)

    assert args == [
        "codex", "exec", "resume", "--json", "session-1",
        "-c", "features.fast_mode=true",
        "-c", 'service_tier="fast"',
        "hello",
    ]


def test_interactive_codex_open_uses_resume_subcommand_without_prompt():
    backend = _codex_backend(resume_flags=["resume", "{session_id}"])

    assert build_resume_args(backend, "session-1") == [
        "codex", "resume", "session-1",
    ]


def test_interactive_claude_open_uses_resume_flag_without_print_mode():
    backend = {
        "type": "claude",
        "command": "claude",
        "resume_flags": ["--resume", "{session_id}", "--dangerously-skip-permissions"],
    }

    assert build_resume_args(backend, "session-1") == [
        "claude", "--resume", "session-1", "--dangerously-skip-permissions",
    ]
