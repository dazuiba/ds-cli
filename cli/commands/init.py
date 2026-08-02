"""Interactive initializer for handoff."""

from __future__ import annotations

import os
import sys


def _pkg_root() -> str:
    """Absolute path to the cli/ package directory."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _home_path(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def _short(path: str) -> str:
    """Replace the home directory with ~ for display."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _color(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _bundled_skill_names(skills_dir: str) -> list[str]:
    """Every bundled Claude Code skill — any subdir holding a SKILL.md.

    Discovered rather than hardcoded so a new backend's skill cannot be
    silently left uninstalled.
    """
    return sorted(
        name for name in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md"))
    )


def _bundled_agent_names(skills_dir: str) -> list[str]:
    """Every bundled Codex custom agent — a top-level handoff-*.toml file."""
    return sorted(
        os.path.splitext(name)[0]
        for name in os.listdir(skills_dir)
        if name.startswith("handoff-") and name.endswith(".toml")
    )


# Claude Code gets skills backed by its background-shell completion events.
# Codex gets custom agents, whose completion events wake the parent thread.
_CLAUDE_SKILL_DIR = (".claude", "skills")
_CODEX_AGENT_DIR = (".codex", "agents")

# Do not install a skill that dispatches to the host's own model.
_CLAUDE_EXCLUDED_SKILLS = {"handoff-opus"}


def _planned_links():
    """Return (kind, src, dest) tuples for link files only (no config)."""
    skills_dir = os.path.join(_pkg_root(), "skills")
    links = []
    for skill_name in _bundled_skill_names(skills_dir):
        if skill_name in _CLAUDE_EXCLUDED_SKILLS:
            continue
        links.append((
            "soft link",
            os.path.join(skills_dir, skill_name, "SKILL.md"),
            _home_path(*_CLAUDE_SKILL_DIR, skill_name, "SKILL.md"),
        ))

    for agent_name in _bundled_agent_names(skills_dir):
        filename = f"{agent_name}.toml"
        links.append((
            "hard link",
            os.path.join(skills_dir, filename),
            _home_path(*_CODEX_AGENT_DIR, filename),
        ))
    return links


def _is_bundled_link(src: str, dest: str) -> bool:
    """Whether dest is a managed link/copy of this bundled handoff skill."""
    linked = (
        os.path.islink(dest)
        and os.path.realpath(dest) == os.path.realpath(src)
    ) or (
        not os.path.islink(dest)
        and os.path.isfile(dest)
        and os.path.samefile(src, dest)
    )
    if linked:
        return True
    if not os.path.isfile(dest):
        return False

    # Package upgrades can replace the source inode while a v4 hard link keeps
    # pointing at the previous wheel's inode. Recognize that managed copy by
    # its deliberately host-specific contract, without deleting arbitrary
    # user files that merely reuse the same path.
    skill_name = os.path.basename(os.path.dirname(src))
    try:
        with open(dest, encoding="utf-8") as installed:
            text = installed.read()
    except (OSError, UnicodeError):
        return False
    return all((
        f"name: {skill_name}" in text,
        "This skill is executed by Claude Code" in text,
        "handoff new --backend" in text,
        "<interaction_contract>" in text,
    ))


def _superseded_skill_links():
    """Bundled skill links that this host split no longer installs.

    Claude's self-dispatch skill remains excluded. All Codex skill links from
    v4.0.0 are superseded by custom agents. User-owned regular files are left
    alone; only links to this package's bundled files are reported.
    """
    skills_dir = os.path.join(_pkg_root(), "skills")
    stale = []
    stale_targets = [
        (skill_name, _CLAUDE_SKILL_DIR)
        for skill_name in sorted(_CLAUDE_EXCLUDED_SKILLS)
    ]
    stale_targets.extend(
        (skill_name, (".codex", "skills"))
        for skill_name in _bundled_skill_names(skills_dir)
    )

    for skill_name, host_dir in stale_targets:
        src = os.path.join(skills_dir, skill_name, "SKILL.md")
        dest = _home_path(*host_dir, skill_name, "SKILL.md")
        if _is_bundled_link(src, dest):
            stale.append(dest)
    return stale


def _print_plan():
    from ..config import user_config_path

    print(_color("1", "handoff initialization"))
    print("")

    links = _planned_links()
    print("The following will be created/updated:")
    for kind, src, dest in links:
        print(f"  {kind}: {_short(dest)} -> {_short(src)}")

    stale_links = _superseded_skill_links()
    if stale_links:
        print("\nThe following superseded links will be removed:")
        for path in stale_links:
            print(f"  remove: {_short(path)}")

    config_path = user_config_path()
    if os.path.isfile(config_path):
        print(f"\nConfig {_short(config_path)} already exists — will not be overwritten.")
    else:
        print(f"\nConfig {_short(config_path)} will be written.")

    print("")


def _confirm() -> bool:
    _print_plan()
    try:
        answer = input("Type Y to continue, anything else to exit: ").strip()
    except EOFError:
        answer = ""
    return answer.lower() == "y"


def _create_links():
    """Install every planned integration link and remove superseded skills.

    Consumes `_planned_links()` directly so what `handoff init` previews is
    exactly what it writes.
    """
    links = _planned_links()
    link_functions = {
        "hard link": os.link,
        "soft link": os.symlink,
    }
    for kind, src, dest in links:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            os.remove(dest)
        link_functions[kind](src, dest)

    removed = 0
    for stale in _superseded_skill_links():
        os.remove(stale)
        removed += 1
        # Drop the skill's directory too, once its SKILL.md is gone.
        parent = os.path.dirname(stale)
        if os.path.basename(stale) == "SKILL.md" and not os.listdir(parent):
            os.rmdir(parent)

    print(f"✓ Created {len(links)} integration links")
    if removed:
        print(f"✓ Removed {removed} superseded links")


def run_init(assume_yes: bool = False):
    if not assume_yes and not _confirm():
        print("handoff: initialization cancelled")
        sys.exit(1)

    print("")
    from ..config import user_config_path, write_default_user_config

    wrote_config = write_default_user_config()
    if wrote_config:
        print(f"✓ Wrote {_short(user_config_path())}")
    else:
        print(f"  Config {_short(user_config_path())} already exists (skipped)")

    _create_links()

    readme_url = "https://github.com/dazuiba/handoff#configuration"

    print("")
    print("Next:")
    print(f"  1. Edit {_short(user_config_path())} and replace"
          f" ${{DEEPSEEK_API_KEY}} with your API key.")
    print(f"  2. For help, see {readme_url}")


def cmd_init(args):
    if args and args[0] in ("-h", "--help"):
        print("usage: handoff init [-y|--yes]")
        return
    assume_yes = False
    for arg in args:
        if arg in ("-y", "--yes"):
            assume_yes = True
        else:
            print(f"handoff: init: unexpected argument '{arg}'", file=sys.stderr)
            sys.exit(2)
    run_init(assume_yes=assume_yes)
