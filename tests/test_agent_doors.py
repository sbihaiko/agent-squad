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


def test_the_agent_to_tickets_door_reads_the_premise_check_before_it_drafts():
    """#884: a breakdown of a spec reads the spec's check comment before it drafts, and each rule of the check comes in the order the skill needs."""
    text = (SKILLS / "agent-to-tickets" / "SKILL.md").read_text()
    order = (
        "Read the parent's check comment before you draft",
        "<!-- premise-check: spec=<n> -->",
        "no premise-check comment on spec",
        "Refuse a check with an open question",
        "exactly one probe ticket",
        "List the proxy answers before the owner approves",
        "Only the owner approves",
    )
    for m in order:
        assert m in text, f"agent-to-tickets does not state: {m}"
    where = [text.index(m) for m in order]
    assert where == sorted(where), "the agent-to-tickets premise-check rules are out of order"
    assert "before its step 3" in text
    assert "`needs a probe`" in text and "native blocking edge" in text and "`decided`" in text and "not the owner" in text


def test_the_squad_skill_update_command_is_symlinked_named_and_names_each_command_it_runs():
    link = LINKS / "squad-skill-update"
    assert link.is_symlink() and link.resolve() == (SKILLS / "squad-skill-update").resolve()
    assert _frontmatter(link / "SKILL.md")["name"] == "squad-skill-update"
    assert "`/squad-skill-update`" in (ROOT / "CLAUDE.md").read_text()
    text = (link / "SKILL.md").read_text()
    for cmd in ("python3 -m squad.skillpath --check", "python3 scripts/build_vendor_skills.py --check", "python3 scripts/build_vendor_skills.py`", "tests/test_skillpath.py", "Call the Skill tool with `autonomy`"):
        assert cmd in text, cmd


def test_the_agent_to_tickets_door_refuses_a_refuted_premise():
    """#888: a probe that refutes a premise sends its spec back to the check, so the breakdown is refused by name."""
    text = (SKILLS / "agent-to-tickets" / "SKILL.md").read_text()
    for m in ("A row whose state is `refuted`", "name each refuted premise", "held until the premise is settled"):
        assert m in text, f"agent-to-tickets does not state: {m}"


def test_the_agent_to_tickets_door_writes_the_premise_line_the_close_path_reads():
    """#888: a probe ticket names its spec and its premise, so the loop's close path can refute that premise."""
    text = (SKILLS / "agent-to-tickets" / "SKILL.md").read_text()
    for m in ("label `probe`", "`## Parent`", "`Premise: <premise>`", "squad premise-check <spec> --refute <premise>"):
        assert m in text, f"agent-to-tickets does not state: {m}"
