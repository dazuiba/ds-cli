import os

from cli.commands import init


def _dests(tmp_home):
    """Planned link destinations, home-relative."""
    return {
        os.path.relpath(dest, str(tmp_home))
        for _kind, _src, dest in init._planned_links()
    }


def _links_by_dest(tmp_home):
    """Planned links keyed by home-relative destination."""
    return {
        os.path.relpath(dest, str(tmp_home)): (kind, src, dest)
        for kind, src, dest in init._planned_links()
    }


def test_claude_skills_and_codex_agents_are_host_specific(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    dests = _dests(tmp_path)

    for skill in ("handoff-ds", "handoff-gemini", "handoff-codex"):
        assert f".claude/skills/{skill}/SKILL.md" in dests
    assert ".claude/skills/handoff-opus/SKILL.md" not in dests

    for agent in ("handoff-ds", "handoff-gemini", "handoff-opus"):
        assert f".codex/agents/{agent}.toml" in dests

    assert not [dest for dest in dests if dest.startswith(".codex/skills/")]
    assert ".codex/agents/handoff-codex.toml" not in dests


def test_codex_uses_hard_links_and_claude_uses_soft_links(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    links = _links_by_dest(tmp_path)

    assert links[".codex/agents/handoff-ds.toml"][0] == "hard link"
    assert links[".codex/agents/handoff-gemini.toml"][0] == "hard link"
    assert links[".codex/agents/handoff-opus.toml"][0] == "hard link"
    assert links[".claude/skills/handoff-codex/SKILL.md"][0] == "soft link"


def test_create_links_uses_each_hosts_planned_link_type(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    init._create_links()

    for relative_dest, (kind, src, dest) in _links_by_dest(tmp_path).items():
        if relative_dest.startswith(".codex/"):
            assert kind == "hard link"
            assert not os.path.islink(dest)
            assert os.path.samefile(src, dest)
        else:
            assert kind == "soft link"
            assert os.path.islink(dest)
            assert os.path.realpath(dest) == os.path.realpath(src)


def test_every_bundled_integration_reaches_its_host(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    dests = _dests(tmp_path)

    skills_dir = os.path.join(init._pkg_root(), "skills")
    for skill in init._bundled_skill_names(skills_dir):
        if skill not in init._CLAUDE_EXCLUDED_SKILLS:
            assert f".claude/skills/{skill}/SKILL.md" in dests

    for agent in init._bundled_agent_names(skills_dir):
        assert f".codex/agents/{agent}.toml" in dests


def test_create_links_replaces_v4_codex_skill_with_custom_agent(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    skills_dir = os.path.join(init._pkg_root(), "skills")
    old_skill = tmp_path / ".codex" / "skills" / "handoff-ds" / "SKILL.md"
    old_skill.parent.mkdir(parents=True)
    os.link(os.path.join(skills_dir, "handoff-ds", "SKILL.md"), old_skill)

    init._create_links()

    assert not old_skill.exists()
    agent = tmp_path / ".codex" / "agents" / "handoff-ds.toml"
    assert agent.is_file()
    assert os.path.samefile(os.path.join(skills_dir, "handoff-ds.toml"), agent)


def test_superseded_cleanup_ignores_user_owned_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for host in (".claude", ".codex"):
        user_skill = tmp_path / host / "skills" / "handoff-opus" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("my own skill", encoding="utf-8")

    assert init._superseded_skill_links() == []


def test_superseded_cleanup_targets_our_claude_symlink(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    src = os.path.join(init._pkg_root(), "skills", "handoff-opus", "SKILL.md")
    dest = tmp_path / ".claude" / "skills" / "handoff-opus" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    os.symlink(src, dest)

    assert init._superseded_skill_links() == [str(dest)]


def test_superseded_cleanup_targets_our_codex_hardlink(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    src = os.path.join(init._pkg_root(), "skills", "handoff-ds", "SKILL.md")
    dest = tmp_path / ".codex" / "skills" / "handoff-ds" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    os.link(src, dest)

    assert init._superseded_skill_links() == [str(dest)]


def test_superseded_cleanup_targets_v4_packaged_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    src = os.path.join(init._pkg_root(), "skills", "handoff-ds", "SKILL.md")
    dest = tmp_path / ".codex" / "skills" / "handoff-ds" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    with open(src, encoding="utf-8") as source:
        dest.write_text(source.read(), encoding="utf-8")

    assert init._superseded_skill_links() == [str(dest)]


def test_agent_templates_target_their_own_backends():
    skills_dir = os.path.join(init._pkg_root(), "skills")
    expected = {
        "handoff-ds": "deepseek",
        "handoff-gemini": "gemini",
        "handoff-opus": "opus",
    }

    for agent, backend in expected.items():
        path = os.path.join(skills_dir, f"{agent}.toml")
        with open(path, encoding="utf-8") as agent_file:
            content = agent_file.read()
        assert f'name = "{agent}"' in content
        assert f"handoff run --backend {backend}" in content
        assert f"handoff resume <RESUME_RUN_ID> --backend {backend}" in content
        assert 'model = "gpt-5.6-luna"' in content
