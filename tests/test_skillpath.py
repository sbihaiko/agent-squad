"""Guards: a borrowed skill is found by name, so an upgrade, a move or a removal cannot break a door silently."""
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

from squad import skillpath as sp

ROOT = Path(__file__).resolve().parent.parent
TRUSTED = ["mattpocock-skills"]
_spec = importlib.util.spec_from_file_location("build_vendor_skills", ROOT / "scripts" / "build_vendor_skills.py")
assert _spec and _spec.loader
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


def skill(folder: Path, body: str = "v1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {folder.name}\n---\n{body}\n")
    return folder / "SKILL.md"


class Home:
    """A fake home: personal skills, agents skills, and a plugin cache with an installed_plugins.json."""

    def __init__(self, tmp: Path):
        self.base = tmp / "home"
        self.plugins: dict = {}
        (self.base / ".claude" / "plugins").mkdir(parents=True)
        (self.base / ".claude" / "skills").mkdir()
        (self.base / ".agents" / "skills").mkdir(parents=True)

    def personal(self, name, body="v1"):
        return skill(self.base / ".claude" / "skills" / name, body)

    def agents(self, name, body="v1"):
        return skill(self.base / ".agents" / "skills" / name, body)

    def install(self, plugin, version, bucket_skills, scope="user", project=None, unlisted=(), manifest=True):
        """bucket_skills: {"engineering/ask-matt": "body"}. Installs under a versioned folder, lists the skills in its
        plugin.json (except `unlisted`, as the real plugin leaves `in-progress/` out) and points the registry at it."""
        install = self.base / ".claude" / "plugins" / "cache" / plugin / version
        for rel, body in bucket_skills.items():
            skill(install / "skills" / rel, body)
        if manifest:
            (install / ".claude-plugin").mkdir(exist_ok=True)
            listed = [f"./skills/{rel}" for rel in bucket_skills if rel not in unlisted]
            (install / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": plugin, "skills": listed}))
        entry = {"scope": scope, "installPath": str(install), "version": version}
        if project:
            entry["projectPath"] = str(project)
        key = f"{plugin}@market"
        same = lambda e: e["scope"] == scope and e.get("projectPath") == (str(project) if project else None)  # noqa: E731
        self.plugins[key] = [e for e in self.plugins.get(key, []) if not same(e)] + [entry]
        (self.base / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": self.plugins}))
        return install


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path)


def find(home, name, **kw):
    return sp.resolve(name, home.base, kw.pop("cwd", home.base), kw.pop("plugins", TRUSTED))


def test_a_personal_copy_wins_over_a_plugin_copy(home):
    home.personal("ask-matt", "mine")
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "plugin"})
    assert [h.kind for h in sp.resolve_all("ask-matt", home.base, home.base, TRUSTED)] == ["personal", "plugin"]
    assert "mine" in find(home, "ask-matt").path.read_text()


def test_the_agents_folder_is_not_a_source_because_claude_code_reads_it_only_through_links(home):
    home.agents("grilling")
    assert find(home, "grilling") is None


def test_a_plugin_update_moves_the_folder_and_the_name_still_resolves(home):
    home.install("mattpocock-skills", "1.2.0", {"engineering/ask-matt": "old"})
    assert "old" in find(home, "ask-matt").path.read_text()
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "new"})      # the update: a new folder, the old one stays in the cache
    hit = find(home, "ask-matt")
    assert "1.2.3" in str(hit.path) and "new" in hit.path.read_text() and hit.detail == "mattpocock-skills 1.2.3"


def test_a_skill_that_moves_to_another_bucket_still_resolves(home):
    home.install("mattpocock-skills", "1.2.3", {"engineering/handoff": "x"})
    assert find(home, "handoff")
    home.install("mattpocock-skills", "1.3.0", {"productivity/handoff": "x"})
    assert "productivity" in str(find(home, "handoff").path)


def test_a_skill_the_plugin_does_not_list_is_not_a_hit(home):
    home.install("mattpocock-skills", "1.2.3", {"deprecated/old-thing": "x", "in-progress/implement-spec": "x", "engineering/tdd": "x"},
                 unlisted=("deprecated/old-thing", "in-progress/implement-spec"))
    assert find(home, "old-thing") is None and find(home, "implement-spec") is None and find(home, "tdd")


def test_a_plugin_with_no_manifest_loads_its_skills_folder_and_nothing_else(home):
    install = home.install("mattpocock-skills", "1.2.3", {"tdd": "x"}, manifest=False)
    skill(install / "examples" / "tdd", "a fixture, not the skill")
    skill(install / "skills" / "deprecated" / "old-thing")
    assert "a fixture" not in find(home, "tdd").path.read_text()
    assert find(home, "old-thing") is None


def test_a_plugin_helper_folder_with_the_same_name_does_not_shadow_the_listed_skill(home):
    install = home.install("mattpocock-skills", "1.2.3", {"engineering/tdd": "the skill"})
    skill(install / "examples" / "tdd", "fixture")
    skill(install / "tests" / "tdd", "fixture")
    assert "the skill" in find(home, "tdd").path.read_text()


def test_a_skill_the_newest_install_dropped_is_gone_though_an_older_install_has_it(home):
    home.install("mattpocock-skills", "1.0.0", {"engineering/removedup": "x"}, scope="local", project=home.base)
    home.install("mattpocock-skills", "1.0.1", {"engineering/other": "x"})
    assert find(home, "removedup") is None


def test_an_empty_skill_file_is_not_a_hit(home):
    home.personal("blank", "").write_text("  \n")
    assert find(home, "blank") is None


def test_a_name_that_is_not_a_plain_skill_name_resolves_to_nothing(home):
    home.personal("tdd")
    for bad in ("../tdd", "tdd/..", "t*", "TDD", "", "-tdd"):
        assert find(home, bad) is None, bad


def test_a_removed_skill_resolves_to_nothing_and_the_cli_exits_1(home, monkeypatch, capsys):
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "x"})
    home.install("mattpocock-skills", "1.3.0", {"engineering/other": "x"})            # ask-matt is gone upstream
    assert find(home, "ask-matt") is None
    monkeypatch.setenv("SQUAD_SKILL_HOME", str(home.base))
    monkeypatch.setattr(sp, "trusted_plugins", lambda root=ROOT: TRUSTED)
    assert sp.main(["ask-matt"]) == 1
    assert "removed or renamed upstream" in capsys.readouterr().err


def test_a_dangling_link_left_by_a_removed_install_is_not_a_hit(home):
    target = skill(home.base / "store" / "wizard")
    (home.base / ".claude" / "skills" / "wizard").symlink_to(target.parent)
    assert find(home, "wizard").kind == "personal"
    target.unlink()
    target.parent.rmdir()
    assert find(home, "wizard") is None


def test_a_skill_file_that_is_a_link_to_a_removed_file_is_not_a_hit(home):
    real = home.base / "store" / "SKILL.md"
    real.parent.mkdir()
    real.write_text("x")
    folder = home.base / ".claude" / "skills" / "teach"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(real)
    assert find(home, "teach")
    real.unlink()
    assert find(home, "teach") is None


def test_an_untrusted_plugin_that_ships_the_same_name_is_not_used(home):
    home.install("some-other-plugin", "9.9.9", {"ask-matt": "not ours"})
    assert find(home, "ask-matt") is None


def test_a_project_scoped_install_applies_to_that_project_only(home, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    home.install("mattpocock-skills", "1.2.0", {"engineering/triage": "x"}, scope="project", project=project)
    assert find(home, "triage", cwd=project)
    assert find(home, "triage", cwd=tmp_path) is None


def test_the_newest_install_wins_by_version_number_not_by_text(home):
    home.install("mattpocock-skills", "1.2.0", {"engineering/triage": "old"}, scope="local", project=home.base)
    home.install("mattpocock-skills", "1.10.0", {"engineering/triage": "new"})
    assert "new" in find(home, "triage").path.read_text()      # 1.10.0 is newer than 1.2.0, though it sorts lower as text


def test_a_broken_registry_is_no_plugin_not_a_crash(home):
    (home.base / ".claude" / "plugins" / "installed_plugins.json").write_text("{not json")
    assert find(home, "anything") is None


def test_check_fails_for_a_missing_door_skill_and_only_warns_for_a_missing_reference(home):
    problems, warnings = sp.check(ROOT, home.base, home.base, TRUSTED)
    assert any(p.startswith("grill-me:") for p in problems) and not any(p.startswith("wizard:") for p in problems)
    assert any(w.startswith("wizard:") for w in warnings)


def test_check_warns_about_a_personal_copy_that_shadows_a_different_plugin_copy(home):
    home.personal("ask-matt", "stale")
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "fresh"})
    problems, warnings = sp.check(ROOT, home.base, home.base, TRUSTED)
    assert not any(p.startswith("ask-matt:") for p in problems)
    assert any(w.startswith("ask-matt:") and "shadows" in w for w in warnings)
    assert any(re.search(r"personal copy \(\d{4}-\d\d-\d\d\) differs from the mattpocock-skills 1\.2\.3 copy \(\d{4}-\d\d-\d\d\)", w) for w in warnings)


def test_check_does_not_call_two_plugin_versions_a_shadow(home):
    home.install("mattpocock-skills", "1.2.0", {"engineering/ask-matt": "old"}, scope="local", project=home.base)
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "new"})
    assert not any(w.startswith("ask-matt:") for w in sp.check(ROOT, home.base, home.base, TRUSTED)[1])


def test_a_per_user_learnings_folder_does_not_make_a_copy_differ(home):
    mine = home.personal("ask-matt", "same")
    (mine.parent / "learnings").mkdir()
    (mine.parent / "learnings" / "notes.md").write_text("private")
    (mine.parent / "install.sh").write_text("curl | sh")
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "same"})
    assert not any(w.startswith("ask-matt:") for w in sp.check(ROOT, home.base, home.base, TRUSTED)[1])


def test_check_is_quiet_about_a_personal_copy_equal_to_the_plugin_copy(home):
    home.personal("ask-matt", "same")
    home.install("mattpocock-skills", "1.2.3", {"engineering/ask-matt": "same"})
    assert not any(w.startswith("ask-matt:") for w in sp.check(ROOT, home.base, home.base, TRUSTED)[1])


def test_every_door_target_and_every_vendored_skill_counts_as_borrowed():
    b = sp.borrowed(ROOT)
    assert sp.door_targets(ROOT) <= b and {"ask-matt", "to-tickets", "grill-with-docs"} <= b
    vendored = {s["source"][6:] for s in yaml.safe_load((ROOT / "config" / "vendor-skills.yaml").read_text())["skills"] if s["source"].startswith("skill:")}
    assert vendored and vendored <= b


def test_every_command_the_workflow_map_names_is_registered():
    text = (ROOT / "docs" / "skill-workflow-map.md").read_text()
    named = set(re.findall(r"(?<![\w/.\-])/([a-z][a-z0-9-]*)(?![\w/.\-]*[/.]\w)", text))
    assert len(named) >= 20, "the pattern no longer finds the commands in the map"
    missing = named - sp.registered(ROOT)
    assert not missing, f"register in config/external-skills.yaml: {sorted(missing)}"


def test_the_manifest_lists_no_name_twice_and_no_project_skill():
    m = sp.load_manifest(ROOT)
    for key in ("referenced", "harness", "plugins"):
        assert len(m[key]) == len(set(m[key])), key
    assert not set(m["referenced"]) & sp.project_skills(ROOT), "a project skill is not borrowed"


def test_the_vendor_builder_finds_a_skill_by_name_through_the_resolver(home, monkeypatch, tmp_path):
    b = B
    home.install("mattpocock-skills", "1.2.3", {"engineering/demo": "body"})
    cfg = tmp_path / "v.yaml"
    cfg.write_text(yaml.safe_dump({"skills": [{"name": "demo", "source": "skill:demo"}, {"name": "gone", "source": "skill:gone"}]}))
    monkeypatch.setenv("SQUAD_SKILL_HOME", str(home.base))
    monkeypatch.chdir(home.base)
    monkeypatch.setattr(sp, "trusted_plugins", lambda root=ROOT: TRUSTED)
    problems, versions = b.build(cfg, tmp_path / "out")
    assert "demo" in versions and any(p.startswith("gone: no SKILL.md") for p in problems)


LIVE = pytest.mark.skipif(not (Path.home() / ".claude" / "skills").is_dir(), reason="no personal skills here (CI)")


@LIVE
@pytest.mark.heavy
def test_on_this_machine_every_skill_a_door_opens_or_the_vendor_builder_copies_still_resolves():
    problems, _ = sp.check(ROOT)
    assert not problems, f"{problems}; a plugin or npx update moved or removed it: reinstall it or retire its door"


@LIVE
def test_on_this_machine_every_command_the_ask_matt_router_names_is_registered_and_resolves_unless_optional():
    """The /ask-matt sequence, read from the copy a door opens: each skill it routes to is known, and found unless the manifest calls it optional."""
    hit = sp.resolve("ask-matt")
    if hit is None:
        pytest.skip("ask-matt does not resolve here; the check above says so")
    text = "".join(f.read_text() for f in sorted(hit.folder.glob("*.md")))
    named = {a or b for a, b in re.findall(r"`/([a-z][a-z0-9-]*)`|\*\*`/([a-z][a-z0-9-]*)`", text)}
    assert len(named) >= 15, "the router no longer names its skills in backticks"
    m = sp.load_manifest(ROOT)
    skills = named - set(m["harness"])
    unknown = skills - sp.registered(ROOT)
    assert not unknown, f"/ask-matt now routes to {sorted(unknown)}: register them in config/external-skills.yaml"
    gone = {n for n in skills - set(m["optional"]) if sp.resolve(n) is None}
    assert not gone, f"/ask-matt routes to {sorted(gone)}, which no longer resolves"


@LIVE
def test_on_this_machine_the_vendored_copy_matches_the_skills_it_was_built_from():
    if any(sp.resolve(n) is None for n in sp.vendored(ROOT)):
        pytest.skip("a vendored skill does not resolve here; the test above says which")
    problems, _ = B.build(check=True)
    assert not problems, f"{problems}; a skill was updated: run `python3 scripts/build_vendor_skills.py` and commit the result"


def test_a_pack_that_has_no_retro_and_no_understand_leaves_the_check_clean():
    """Pack 1.2.3 ships `retro` as a stub and has no `understand`: the docs name them, so a missing one is optional, never a failure."""
    m = sp.load_manifest(ROOT)
    assert {"retro", "understand"} <= set(m["optional"]) and not {"retro", "understand"} & set(m["referenced"])
