# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What handoff is

A CLI proxy that dispatches coding tasks to configurable AI backends — Claude (any Anthropic-compatible endpoint) and Codex (`codex exec`). Users invoke it as a Claude Code skill or Codex subagent, rarely typing `handoff` directly.

## File map

```
cli/
├── main.py                  # Entry point: parses argv[1], dispatches to cli/commands/<cmd>.py
├── config.py                # Loads ~/.handoff/config.yaml (user config), deep-merges with backend_types.yaml (mechanism)
├── backend.py               # Resolves backend config → CLI args + env vars for claude/codex subprocess
├── core.py                  # SQLite state (~/.handoff/runs/handoff.db), run ID allocation, legacy migration
├── stream.py                # execute_run(): spawn backend, parse JSONL stream, write .out.txt / .result.md
├── tui.py                   # Textual TUI for `handoff list` (DataTable, detail view, auto-refresh)
├── jsonl_parser.py          # Low-level JSONL line parser
├── jsonl_viewer.py          # JSONL log viewer (used by TUI detail panel)
├── backend_types.yaml       # Mechanism contract: how each backend TYPE is launched (command, PTY, flags). NOT user-overridable.
├── user_config_template.yaml# Template for `handoff init` → ~/.handoff/config.yaml
├── commands/
│   ├── run.py               # `handoff run` — execute a prompt against a backend
│   ├── new.py               # `handoff new` — pre-allocate run_id, print prompt file path
│   ├── list.py              # `handoff list` / `handoff ls` — TUI or plain-text listing
│   ├── resume.py            # `handoff resume` — reopen or continue a past conversation
│   ├── tail.py              # `handoff tail` — live-tail a run's output stream
│   ├── init.py              # `handoff init` — create config, symlink skill/agent files
│   └── env.py               # `handoff env` — print config/data paths
└── skills/
    ├── handoff-ds/SKILL.md   # Claude Code skill → deepseek backend
    ├── handoff-codex/SKILL.md# Claude Code skill → codex backend
    ├── handoff-gemini/SKILL.md# Claude Code skill → gemini backend
    ├── handoff-opus/SKILL.md # Shared generated skill source (not installed into Claude)
    ├── handoff-ds.toml       # Codex custom agent → deepseek backend (master)
    ├── handoff-gemini.toml   # Generated Codex custom agent → gemini backend
    └── handoff-opus.toml     # Generated Codex custom agent → opus backend
```

## How to release

1. Bump `version` in `pyproject.toml`
2. Commit: `git commit -m "release: vX.Y.Z"`
3. Tag: `git tag vX.Y.Z`
4. Push: `git push && git push --tags`

Pushing a `v*` tag triggers `.github/workflows/publish.yml` → builds with `uv build`, publishes to PyPI, creates a GitHub Release.
