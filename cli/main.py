"""handoff main dispatch — import this from the entry point."""

import os
import sys

from . import __version__


def usage(config=None):
    print(
        """usage:
  handoff --help
  handoff env
  handoff init      [-y|--yes]
  handoff new       --backend <name> [--slug <slug>] [--write]
  handoff list|ls   [<run-id|seq>] [--uuid] [--cwd] [--follow]
  handoff open      [<run-id|seq>] [--backend <name>] [--session-id <id>] [--pro] [--cwd <dir>] [--verbose]
  handoff run       [--backend <name>] [--cwd <dir>] [--slug <slug>] [--pro] [--fast] [--verbose] [--dry-run] (<input-file|-> | --text <prompt...>)
  handoff resume    [<run-id|seq>] [--backend <name>] [--session-id <id>] [--slug <slug>] [--pro] [--fast] [--cwd <dir>] [--verbose] (<input-file|-> | --text <prompt...>)
  handoff tail [<run-id|seq>]

  handoff env              — print config / data paths (works even with broken config)
  handoff new              — pre-allocate a run_id; prints .prompt.md path to stdout
  handoff list             — browse your past sessions (`ls` alias supported)
  handoff list <seq> --follow
                          — jump straight into one run's live detail view
  handoff open <seq>      — reopen a past conversation (interactive)
  handoff open --backend codex --session-id <id> --cwd <dir>
                          — interactively open a conversation outside handoff.db
  handoff run --dry-run --text hi
                          — print the exact backend command without executing it
  handoff run --text hi    — quick smoke-test / debug your config.yaml
  handoff resume <seq> -   — dispatch a follow-up task to that conversation (heredoc/--text)
  handoff resume --backend opus --session-id <id> --cwd <dir> --text hi
                          — append a managed turn to an external conversation
  handoff tail             — alias for `handoff list --follow` on the latest run

Run ids: <mmdd>-<backend2>-<SEQ_CODE>-<slug>  (e.g. 0611-ds-03-fix-auth)
--cwd defaults to the calling process's current directory for run; explicit-session open/resume requires it.
--backend picks a backend (default: first entry in config.yaml backends).
--slug sets the semantic suffix in generated run ids.
--write on `handoff new` writes stdin to the pre-allocated .prompt.md file.
--pro uses the backend's pro_model. Managed open/resume inherit the session's latest pro setting.
--fast enables Codex Fast Mode for this run/resume only; it is off by default."""
    )


def main():
    # Run legacy migration early — before any config check — so that an
    # existing legacy dir is renamed to ~/.handoff before we look for config.
    from .core import _migrate_legacy_state
    _migrate_legacy_state()

    # Non-blocking version check against PyPI (daemon thread, max 1/24h).
    from .version_check import maybe_check

    maybe_check()

    if len(sys.argv) < 2:
        config_path = os.path.join(os.path.expanduser("~"), ".handoff", "config.yaml")
        if not os.path.isfile(config_path):
            from .commands.init import run_init

            run_init()
            return
        usage()
        sys.exit(2)

    subcmd = sys.argv[1]
    rest = sys.argv[2:]

    if subcmd in ("-h", "--help"):
        usage()
        return

    if subcmd == "--version":
        print(f"handoff {__version__}")
        return

    if subcmd == "init":
        from .commands.init import cmd_init

        cmd_init(rest)
        return

    if subcmd == "env":
        from .commands.env import cmd_env

        cmd_env(rest)
        return

    known = {"run", "list", "ls", "open", "resume", "tail", "new"}
    if subcmd not in known:
        print(
            f"handoff: unknown subcommand '{subcmd}' — expected: "
            f"env, init, list, ls, new, open, run, resume, tail",
            file=sys.stderr,
        )
        usage()
        sys.exit(2)

    from .config import Config
    from .commands.run import cmd_run
    from .commands.list import cmd_list
    from .commands.open import cmd_open
    from .commands.resume import cmd_resume
    from .commands.tail import cmd_tail
    from .commands.new import cmd_new

    config = Config()

    if subcmd == "run":
        cmd_run(rest, config)
    elif subcmd == "new":
        cmd_new(rest, config)
    elif subcmd in ("list", "ls"):
        cmd_list(rest, config)
    elif subcmd == "open":
        cmd_open(rest, config)
    elif subcmd == "resume":
        cmd_resume(rest, config)
    elif subcmd == "tail":
        cmd_tail(rest, config)
