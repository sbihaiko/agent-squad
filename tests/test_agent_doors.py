"""Guard for the agent-doors rule in CLAUDE.md: each door in `.agents/skills/agent-*` is symlinked into `.claude/skills/`, is named in CLAUDE.md, and points at its global skill."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".agents" / "skills"
LINKS = ROOT / ".claude" / "skills"
# Doors to this repository's own skills. There is no borrowed skill to find by name, so the door names the file in the
# checkout instead (ADR-0129 covers the borrowed ones); `test_a_repo_own_door_names_the_repo_skill_it_runs` proves it.
REPO_OWN = {"agent-squad-dashboard", "agent-premise-check"}


def _doors() -> list[Path]:
    return sorted(p for p in SKILLS.glob("agent-*") if p.is_dir())


def _frontmatter(path: Path) -> dict[str, str]:
    block = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.S)
    assert block, f"{path} has no frontmatter"
    return dict(re.findall(r'^(\w+):\s*"?(.*?)"?$', block.group(1), re.M))


def test_there_are_doors():
    assert _doors()


def test_each_door_is_symlinked_into_claude_skills():
    for door in _doors():
        link = LINKS / door.name
        assert link.is_symlink(), f"{door.name} has no symlink in .claude/skills"
        assert link.resolve() == door.resolve(), f"{door.name} symlink points elsewhere"


def test_each_door_names_itself_and_has_a_description():
    for door in _doors():
        meta = _frontmatter(door / "SKILL.md")
        assert meta.get("name") == door.name
        assert meta.get("description"), f"{door.name} has no description"


def test_each_door_is_named_in_claude_md():
    text = (ROOT / "CLAUDE.md").read_text()
    for door in _doors():
        assert f"`{door.name}`" in text or f"`/{door.name}" in text, f"CLAUDE.md does not name {door.name}"


def test_each_agent_door_finds_its_global_skill_by_name_not_by_path():
    for door in _doors():
        if door.name in REPO_OWN:
            continue
        want = door.name.removeprefix("agent-")
        text = (door / "SKILL.md").read_text()
        assert f"python3 -m squad.skillpath {want}`" in text, door.name
        assert "~/.claude/skills" not in text, f"{door.name} hard-codes a path an upgrade can move"
        assert "removed or renamed upstream" in text, f"{door.name} does not say what to do when the skill is gone"


REPO_OWN_RUNS = {
    "agent-squad-dashboard": "uv run squad dashboard",
    "agent-premise-check": "`.claude/skills/premise-check/SKILL.md`",
}


def test_a_repo_own_door_names_what_it_runs_in_the_checkout():
    """A door with no borrowed skill behind it names a file or a command in the checkout, and reads nothing under `~`."""
    assert set(REPO_OWN_RUNS) == REPO_OWN
    for name, wanted in REPO_OWN_RUNS.items():
        text = (SKILLS / name / "SKILL.md").read_text()
        assert wanted in text, f"{name} does not name {wanted}"
        assert "~/" not in text, f"{name} reaches outside the checkout"


def test_the_ask_matt_door_checks_each_skill_it_recommends():
    text = (SKILLS / "agent-ask-matt" / "SKILL.md").read_text()
    assert "run `python3 -m squad.skillpath <that skill>`" in text and "do not recommend it" in text


def test_the_squad_skill_update_command_is_symlinked_named_and_names_each_command_it_runs():
    link = LINKS / "squad-skill-update"
    assert link.is_symlink() and link.resolve() == (SKILLS / "squad-skill-update").resolve()
    assert _frontmatter(link / "SKILL.md")["name"] == "squad-skill-update"
    assert "`/squad-skill-update`" in (ROOT / "CLAUDE.md").read_text()
    text = (link / "SKILL.md").read_text()
    for cmd in ("python3 -m squad.skillpath --check", "python3 scripts/build_vendor_skills.py --check", "python3 scripts/build_vendor_skills.py`", "tests/test_skillpath.py", "Call the Skill tool with `autonomy`"):
        assert cmd in text, cmd
