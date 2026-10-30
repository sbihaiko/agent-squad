"""Guards for ticket #46: the vendored skills plugin is a versioned copy, and every pinned role skill is in it."""
import importlib.util
import json
from pathlib import Path

import yaml

from squad import defaults, skillpath

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_vendor_skills", ROOT / "scripts" / "build_vendor_skills.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
VENDOR = ROOT / "vendor" / "skills"


def test_the_committed_copy_matches_its_versions_file():
    versions = json.loads((VENDOR / "VERSIONS.json").read_text())
    assert versions
    for name, v in versions.items():
        d = VENDOR / "skills" / name
        rels = B.files(d)
        assert (d / "SKILL.md").exists() and B.tree_hash(d, rels) == v["sha256"] and len(rels) == v["files"], name


def test_every_skill_a_role_pins_is_vendored_and_the_plugin_has_a_manifest():
    pinned = {s for r in defaults.load()["roles"].values() for s in r.get("skills", [])}
    assert pinned and pinned <= set(json.loads((VENDOR / "VERSIONS.json").read_text()))
    assert json.loads((VENDOR / ".claude-plugin" / "plugin.json").read_text())["name"] == "agent-squad-skills"


def test_the_config_lists_the_skills_the_role_map_names():
    listed = {s["name"] for s in yaml.safe_load((ROOT / "config" / "vendor-skills.yaml").read_text())["skills"]}
    assert {"to-spec", "to-tickets", "to-test-scenario", "tdd", "ego-browser", "codebase-design", "ask-matt",
            "diagnosing-bugs"} == listed
    assert "code-review" not in listed        # it starts subagents, which no role may do


def make_source(tmp_path):
    src = tmp_path / "src" / "demo"
    (src / "learnings").mkdir(parents=True)
    (src / "scripts").mkdir()
    (src / "SKILL.md").write_text("---\nname: demo\n---\nbody\n")
    (src / "learnings" / "notes.md").write_text("private")
    (src / "scripts" / "install.sh").write_text("curl | sh")
    (src / "scripts" / "run.sh").write_text("echo ok")
    cfg = tmp_path / "v.yaml"
    cfg.write_text(yaml.safe_dump({"skills": [{"name": "demo", "source": str(src)}]}))
    return src, cfg


def test_a_build_copies_a_skill_without_learnings_or_an_installer_and_check_passes(tmp_path):
    src, cfg = make_source(tmp_path)
    out = tmp_path / "out"
    assert B.build(cfg, out)[0] == []
    copied = sorted(str(p.relative_to(out / "skills" / "demo")) for p in (out / "skills" / "demo").rglob("*") if p.is_file())
    assert copied == ["SKILL.md", "scripts/run.sh"]
    assert B.build(cfg, out, check=True)[0] == []


def test_check_reports_a_source_that_changed_and_a_copy_edited_by_hand(tmp_path):
    src, cfg = make_source(tmp_path)
    out = tmp_path / "out"
    B.build(cfg, out)
    (src / "SKILL.md").write_text("changed")
    problems = B.build(cfg, out, check=True)[0]
    assert any("differs from its source" in p for p in problems) and any("out of date" in p for p in problems)
    B.build(cfg, out)
    (out / "skills" / "demo" / "SKILL.md").write_text("edited by hand")
    assert any("differs from its source" in p for p in B.build(cfg, out, check=True)[0])


def test_the_to_tickets_skill_states_the_spec_prefix_rule():
    """A child of a spec carries the spec's number first in its title, and the vendored copy carries the same lines."""
    markers = ("[SPEC <n>] <title>", "the first words of its title",
               "never replaces the parent relation", "carries no prefix")
    vendored = (VENDOR / "skills" / "to-tickets" / "SKILL.md").read_text()
    for m in markers:
        assert m in vendored, f"the vendored to-tickets skill does not state: {m}"
    personal = next((h for h in skillpath.resolve_all("to-tickets") if h.kind == "personal"), None)
    if personal:                                   # the source copy, when this machine has one
        source = personal.path.read_text()
        for m in markers:
            assert m in source, f"the personal to-tickets skill does not state: {m}"


def test_the_publish_skills_add_the_triage_label_only_after_the_status_reads_back():
    """#895: a new card is Todo the moment it joins the board, so ready-for-agent waits for the Status (vendored copies)."""
    markers = {
        "to-tickets": ("create the issue without `ready-for-agent`", "Read the Status back",
                       "add `ready-for-agent` only after", "then link the ticket to its parent"),
        "to-spec": ("Read the Status back",),
    }
    for name, found in markers.items():
        vendored = (VENDOR / "skills" / name / "SKILL.md").read_text()
        for m in found:
            assert m in vendored, f"the vendored {name} skill does not state: {m}"


def test_a_source_with_no_skill_file_is_a_problem(tmp_path):
    cfg = tmp_path / "v.yaml"
    cfg.write_text(yaml.safe_dump({"skills": [{"name": "ghost", "source": str(tmp_path / "nowhere")}]}))
    assert "no SKILL.md" in B.build(cfg, tmp_path / "o")[0][0]
