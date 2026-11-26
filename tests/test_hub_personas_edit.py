"""GitHub issue #3011: the owner edits a persona in the Personas theme (SPEC 3009).

The theme posts the persona id, the changed fields and the target projects. The hub builds each project's document
through the persona rules module and calls that project's existing save path from the project's own worktree: a preview
writes no file, and a confirmed save writes, commits and lands each target project.

The seams under test are the hub's own read and edit, `personas_overview` and `personas_edit`, the rules module
`squad/personas.py`, and the served page's renderer run in node. No browser runs here. Every id and name is a
neutral placeholder.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

from squad import clients as CL
from squad import personas as PS

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A neutral project that declares two personas.
PROJECT = {
    "version": 1,
    "project": {"language": "en-US", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                 {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
}
LOCAL = {"version": 1}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module resolves from this tree."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_personas_edit", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def seat(pid, committed=None, local=None, checkout="/co/alpha", cf="slack.yaml", lf="slack.local.yaml",
         read_defects=()):
    """One roster seat as the roster hands it in: its project id, its documents and their files, and its worktree."""
    return {"id": pid, "label": pid, "checkout": checkout, "committed": committed or {}, "local": local or {},
            "cf": cf, "lf": lf, "committed_path": f"/co/{pid}/docs/squad/slack.yaml",
            "local_path": f"/profiles/{pid}.slack.local.yaml", "read_defects": list(read_defects)}


# ---------------------------------------------------------------- the rules module: one persona's fields

def test_the_rules_set_only_the_named_fields_of_one_persona():
    """The changed fields alone move; the persona's other keys, the other personas and every other key keep their form."""
    doc = {"version": 1, "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "note": "x"},
                                      {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}]}
    got = PS.with_fields(doc, "blue", {"voice": "Puck", "tone": "  Direct.  "})
    assert got["personas"][0] == {"id": "blue", "name": "Blue", "voice": "Puck", "tone": "Direct.", "note": "x"}, got
    assert got["personas"][1] == doc["personas"][1], got
    assert doc["personas"][0]["voice"] == "Kore", "the input document changed"


def test_a_persona_the_committed_file_does_not_declare_answers_no_document():
    """A local-only persona is read-only in the theme: the rules answer no document for it."""
    assert PS.with_fields({"personas": [{"id": "amber", "name": "Amber"}]}, "blue", {"voice": "Puck"}) is None


def test_the_edit_names_each_refused_project_and_still_carries_the_other_writes():
    """A target outside the roster, a project with no worktree, a file whose read answered a defect and a project whose
    committed file does not declare the id are each refused by name; the other targets keep their write."""
    seats = [seat("alpha", PROJECT),
             seat("beta", PROJECT, checkout=""),
             seat("gamma", PROJECT, read_defects=["slack.yaml: not YAML"]),
             seat("delta", {"personas": [{"id": "amber", "name": "Amber"}]})]
    plan = PS.edit(seats, "blue", {"tone": "Direct."}, ["alpha", "beta", "gamma", "delta", "ghost"])
    rows = {r["project"]: r["result"] for r in plan["rows"]}
    assert rows["alpha"] == "writes Blue", rows
    assert rows["beta"] == "refused: the project has no worktree", rows
    assert rows["gamma"] == "refused: the file does not read: slack.yaml: not YAML", rows
    assert rows["delta"] == "refused: Blue is not declared in this project's committed file", rows
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert [w["project"] for w in plan["writes"]] == ["alpha"], plan["writes"]
    assert plan["writes"][0]["committed"]["personas"][0]["tone"] == "Direct.", plan["writes"]


# ---------------------------------------------------------------- the rules module: the rename's own facts

CLIENT = {"id": "atlas", "name": "Atlas Corp", "default_persona": {"id": "blue", "name": "Blue"},
          "projects": [{"id": "alpha", "follows": True}]}


def test_a_rename_names_the_address_and_the_authorship_mark():
    """#3012 acceptance 2 and 3: the plan states that the @<name> address and the authorship mark change, and states
    nothing of either when the name stays."""
    plan = PS.edit([seat("alpha", PROJECT)], "blue", {"name": "Azure"}, ["alpha"], clients=[])
    assert plan["notes"] == ["the @Blue address becomes @Azure",
                             "the authorship mark becomes *Azure's Msg*"], plan["notes"]
    kept = PS.edit([seat("alpha", PROJECT)], "blue", {"tone": "Direct."}, ["alpha"], clients=[CLIENT])
    assert kept["notes"] == [] and kept["map"] == [], kept


def test_a_rename_names_the_client_map_of_each_default_that_holds_the_persona():
    """#3012 acceptance 4: the plan names the client map when a client default names the persona, and one map row for
    each default whose stored name differs."""
    plan = PS.edit([seat("alpha", PROJECT)], "blue", {"name": "Azure"}, ["alpha"], clients=[CLIENT])
    assert plan["notes"][-1] == "the client map stores Azure as the default name of Atlas Corp", plan["notes"]
    assert plan["map"] == [{"client": "atlas", "label": "Atlas Corp", "result": "writes Azure"}], plan["map"]
    # A default that names another persona is not this persona's, and it stays out of the plan.
    other = {**CLIENT, "default_persona": {"id": "amber", "name": "Amber"}}
    plan = PS.edit([seat("alpha", PROJECT)], "blue", {"name": "Azure"}, ["alpha"], clients=[other])
    assert "the client map" not in " ".join(plan["notes"]), plan["notes"]
    assert plan["map"] == [], plan["map"]


def test_a_rename_preview_reads_the_old_name_from_the_roster_not_from_a_written_target():
    """A target set the hub can read nothing from must not make the preview state the persona id as the persona's old
    name: the roster seat's own local declaration, or the name a client default stores, answers the real name."""
    local_only = seat("alpha", {"personas": [{"id": "amber", "name": "Amber"}]},
                      local={"personas": [{"id": "blue", "name": "Blue"}]})
    plan = PS.edit([local_only], "blue", {"name": "Azure"}, ["ghost"], clients=[])
    assert plan["notes"] == ["the @Blue address becomes @Azure",
                             "the authorship mark becomes *Azure's Msg*"], plan["notes"]
    # No seat declares the persona at all: the client default's stored name still answers it.
    plan = PS.edit([seat("alpha", {})], "blue", {"name": "Azure"}, ["alpha"], clients=[CLIENT])
    assert plan["notes"] == ["the @Blue address becomes @Azure",
                             "the authorship mark becomes *Azure's Msg*",
                             "the client map stores Azure as the default name of Atlas Corp"], plan["notes"]


def test_a_holder_is_named_before_the_missing_committed_row():
    """#3014 acceptance 6: a place that names the persona refuses the removal and names the holder, even when the
    committed file does not declare the row, so the local file's holder is never reported as a missing row."""
    seats = [seat("alpha", {"personas": [{"id": "blue", "name": "Blue"}]},
                  {"workspaces": [{"id": "T1", "label": "team",
                                   "channels": [{"id": "C1", "label": "support", "persona": "amber"}]}]})]
    plan = PS.remove(seats, "amber", ["alpha"],
                     {"alpha": ['channel "support" of workspace "team" in slack.local.yaml']})
    assert plan["rows"][0]["result"] == \
        'refused: held by channel "support" of workspace "team" in slack.local.yaml', plan["rows"]
    assert plan["writes"] == [], plan["writes"]


# ---------------------------------------------------------------- the route: the fields, the preview and the save

def git_repo(tmp_path, name, doc):
    """A project checkout that is its own git repository, one commit on dev, and the bare origin it lands in."""
    origin = tmp_path / f"{name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/dev"], check=True, timeout=60)
    co = tmp_path / name
    co.mkdir()
    for args in (("init", "-q", "-b", "dev"), ("config", "user.name", "fixture"),
                 ("config", "user.email", "fixture@localhost")):
        subprocess.run(["git", "-C", str(co), *args], check=True, timeout=60)
    c = co / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    c.write_text(yaml.safe_dump(doc, sort_keys=False))
    for args in (("add", "--", "docs/squad/slack.yaml"), ("commit", "-q", "-m", "seed"),
                 ("remote", "add", "origin", str(origin)), ("push", "-q", "origin", "dev"), ("fetch", "-q", "origin")):
        subprocess.run(["git", "-C", str(co), *args], check=True, timeout=60)
    return co, origin


def wire(tmp_path, monkeypatch, hub, docs, git=False):
    """One checkout and one profile file for each project, and the profile folder the roster reads."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, doc in docs.items():
        if git:
            co, origin = git_repo(tmp_path, slug, doc)
            made[slug] = (co, co / "docs" / "squad" / "slack.yaml", origin)
        else:
            co = tmp_path / "checkouts" / slug
            c = co / "docs" / "squad" / "slack.yaml"
            c.parent.mkdir(parents=True)
            c.write_text(yaml.safe_dump(doc, sort_keys=False))
            made[slug] = (co, c, None)
        (prof / f"{slug}.sh").write_text(
            f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={slug}}}"; '
            f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
        (prof / f"{slug}.slack.local.yaml").write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(hub, "STATE", state)
    return prof, made


def test_the_read_offers_only_the_gemini_voices_and_names_the_tone_limit(hub):
    """#3011 acceptance 1: the read answers the Gemini voices alone, so the voice field offers no other name, and the
    tone limit the field states."""
    P = hub.slack_policy()
    got = hub.personas_overview([], [])
    assert got["voices"] == sorted(P.GEMINI_VOICES), got["voices"]
    assert got["tone_max"] == P.TONE_MAX, got


def test_a_tone_over_the_limit_is_refused_and_the_message_names_the_limit(hub):
    """#3011 acceptance 3: the refusal states the limit and the length given, and no project is written."""
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "x" * 301}, "projects": ["alpha"]})
    assert out.get("err") == "personas[].tone: a tone holds at most 300 characters (301 given)", out


def test_a_voice_outside_the_gemini_list_is_refused(hub):
    """#3011 acceptance 1: the route refuses a voice the voice field never offers."""
    out = hub.personas_edit({"persona": "blue", "fields": {"voice": "Nope"}, "projects": ["alpha"]})
    assert "is not a Gemini voice" in out["err"], out


def test_a_name_outside_the_policy_pattern_is_refused_with_the_policy_message(hub):
    """#3012 acceptance 1: the route refuses a name the policy module's pattern refuses, and states the policy's own
    message, before any project is read."""
    P = hub.slack_policy()
    out = hub.personas_edit({"persona": "blue", "fields": {"name": "Blue!"}, "projects": ["alpha"]})
    assert out.get("err") == f"personas[].name: {P.name_reason('Blue!')}", out


def test_the_read_answers_the_policy_name_pattern(hub):
    """#3012 acceptance 1: the read answers the pattern the name field states, from the policy module alone."""
    P = hub.slack_policy()
    got = hub.personas_overview([], [])
    assert got["name_pattern"] == P.NAME.pattern, got.get("name_pattern")


def test_the_preview_names_each_project_file_and_writes_none(hub, tmp_path, monkeypatch):
    """#3011 acceptance 4 and 5: the preview names each project's file the save would write, and no file changes and no
    worktree appears."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT})
    before = {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()}
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct."}, "projects": ["alpha", "beta"]})
    assert out.get("err") is None, out
    files = {r["project"]: r["file"] for r in out["rows"]}
    assert files["alpha"].endswith("/alpha/docs/squad/slack.yaml"), files
    assert files["beta"].endswith("/beta/docs/squad/slack.yaml"), files
    assert {r["project"]: r["result"] for r in out["rows"]} == {"alpha": "writes Blue", "beta": "writes Blue"}, out
    assert {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()} == before, "the preview wrote a file"
    assert not (tmp_path / "state" / "slack-wt").exists(), "the preview made a worktree"


@pytest.mark.heavy   # one real repository and its worktree
def test_the_read_answers_the_landing_branch_never_an_uncommitted_hub_worktree_file(hub, tmp_path, monkeypatch):
    """A hub worktree left with an uncommitted edit answers nothing: the theme reads the document committed on the
    landing branch, so a refused or unlanded save never draws itself as the project's declaration."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": BLUE_A}, git=True)
    co, c, _origin = made["alpha"]
    wt = tmp_path / "hubs" / "alpha"
    subprocess.run(["git", "-C", str(co), "worktree", "add", "-q", "--detach", str(wt), "HEAD"],
                   check=True, timeout=60)
    (wt / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(BLUE_B, sort_keys=False))
    got = hub._pr_newest_committed({"id": "alpha", "label": "alpha", "checkout": str(co),
                                    "committed": yaml.safe_load(c.read_text()), "committed_path": str(c),
                                    "cf": "slack.yaml"})
    assert got["committed"]["personas"][0] == BLUE_A["personas"][0], got
def test_the_preview_of_a_rename_names_the_address_the_mark_and_the_client_map(hub, tmp_path, monkeypatch):
    """#3012 acceptance 2, 3 and 4: the preview states the address change and the mark change, names the client map of a
    default that holds the persona, and writes no client name into a project file."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    (prof / "clients.local.yaml").write_text(yaml.safe_dump({"clients": [CLIENT]}, sort_keys=False))
    out = hub.personas_edit({"persona": "blue", "fields": {"name": "Azure"}, "projects": ["alpha"]})
    assert out.get("err") is None, out
    assert "the @Blue address becomes @Azure" in out["notes"], out["notes"]
    assert "the authorship mark becomes *Azure's Msg*" in out["notes"], out["notes"]
    assert "the client map stores Azure as the default name of Atlas Corp" in out["notes"], out["notes"]
    assert out["map"] == [{"client": "atlas", "label": "Atlas Corp", "result": "writes Azure"}], out["map"]
    assert "Atlas Corp" not in made["alpha"][1].read_text(), "the rename wrote the client name into a project file"


@pytest.mark.heavy   # two real repositories and their worktrees
def test_a_confirmed_save_writes_each_project_from_its_own_tree_with_one_commit_and_one_push(
        hub, tmp_path, monkeypatch):
    """#3011 acceptance 6 and 7: each target project's committed file carries the edit, and each project lands one commit
    in its own origin."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT}, git=True)
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct.", "voice": "Puck"},
                             "projects": ["alpha", "beta"], "confirm": True})
    assert out.get("err") is None, out
    assert {r["project"]: r["result"].startswith("wrote and pushed commit ") for r in out["rows"]} == \
           {"alpha": True, "beta": True}, out["rows"]
    for slug, (_co, _c, origin) in made.items():
        landed = subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                                capture_output=True, text=True, check=True, timeout=60).stdout
        doc = yaml.safe_load(landed)
        assert doc["personas"][0] == {"id": "blue", "name": "Blue", "voice": "Puck", "tone": "Direct."}, doc
        count = subprocess.run(["git", "-C", str(origin), "rev-list", "--count", "dev"],
                               capture_output=True, text=True, check=True, timeout=60).stdout.strip()
        assert count == "2", f"{slug}: one commit above the seed: {count}"


@pytest.mark.heavy   # two real repositories and their worktrees
def test_a_confirmed_rename_writes_each_project_and_the_client_default_stored_name(hub, tmp_path, monkeypatch):
    """#3012 acceptance 5 to 8: a confirmed rename lands the new name in each target project and stores it in each
    client default that holds the persona, the client badge and tile read it, and no client name enters a committed
    file."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT}, git=True)
    other = {"id": "other", "name": "Other Co", "default_persona": {"id": "amber", "name": "Amber"}, "projects": []}
    (prof / "clients.local.yaml").write_text(yaml.safe_dump({"clients": [CLIENT, other]}, sort_keys=False))
    out = hub.personas_edit({"persona": "blue", "fields": {"name": "Azure"},
                             "projects": ["alpha", "beta"], "confirm": True})
    assert out.get("err") is None, out
    assert {r["project"]: r["result"].startswith("wrote and pushed commit ") for r in out["rows"]} == \
           {"alpha": True, "beta": True}, out["rows"]
    for slug, (_co, _c, origin) in made.items():
        landed = subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                                capture_output=True, text=True, check=True, timeout=60).stdout
        doc = yaml.safe_load(landed)
        assert doc["personas"][0]["name"] == "Azure", (slug, doc)
        assert "Atlas Corp" not in landed, f"{slug}: a client name entered the committed file"
    stored = {c["id"]: c for c in yaml.safe_load((prof / "clients.local.yaml").read_text())["clients"]}
    assert stored["atlas"]["default_persona"] == {"id": "blue", "name": "Azure"}, stored["atlas"]
    assert stored["other"]["default_persona"] == {"id": "amber", "name": "Amber"}, stored["other"]
    assert out["map"] == [{"client": "atlas", "label": "Atlas Corp",
                           "result": "stored Azure as the default name"}], out["map"]
    # The client badge and tile read the stored name, so they show the new one after the save.
    seats, _err = hub._slack_roster_checked(0.0)
    tree = hub._slack_clients_tree_for(hub.slack_policy(), seats)
    node = next(n for n in tree["clients"] if n.get("client") == "atlas")
    assert node["badges"][0]["text"] == "Azure", node["badges"]
    assert next(t for t in node["now"] if t["key"] == "Default persona")["value"] == "Azure", node["now"]


def test_a_confirmed_rename_stores_the_client_default_even_when_no_project_is_saved(hub, tmp_path, monkeypatch):
    """#3012 acceptance 6 and 7: the client default's stored name changes on confirm even when every project target is
    refused, so the badge and the tile never keep the old name after the owner confirmed the rename."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    (prof / "clients.local.yaml").write_text(yaml.safe_dump({"clients": [CLIENT]}, sort_keys=False))
    out = hub.personas_edit({"persona": "blue", "fields": {"name": "Azure"}, "projects": ["ghost"],
                             "confirm": True})
    assert out.get("err") is None, out
    assert out["saved"] == [], out["saved"]
    assert {r["project"]: r["result"] for r in out["rows"]} == \
           {"ghost": "refused: the project is not in the roster"}, out["rows"]
    assert out["map"] == [{"client": "atlas", "label": "Atlas Corp",
                           "result": "stored Azure as the default name"}], out["map"]
    stored = yaml.safe_load((prof / "clients.local.yaml").read_text())["clients"][0]
    assert stored["default_persona"] == {"id": "blue", "name": "Azure"}, stored
    seats, _err = hub._slack_roster_checked(0.0)
    tree = hub._slack_clients_tree_for(hub.slack_policy(), seats)
    node = next(n for n in tree["clients"] if n.get("client") == "atlas")
    assert node["badges"][0]["text"] == "Azure", node["badges"]
    assert next(t for t in node["now"] if t["key"] == "Default persona")["value"] == "Azure", node["now"]


@pytest.mark.heavy   # two real repositories and their worktrees
def test_a_project_that_does_not_validate_is_named_refused_and_the_others_are_written(hub, tmp_path, monkeypatch):
    """#3011 acceptance 8 and 9: the preview names the project whose file a save would not validate, and a confirmed save
    refuses it by name and still writes every other target. A name outside the pattern is refused earlier, by the
    route itself (#3012 acceptance 1), so this file breaks another rule: a voice the policy does not know."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    broken = {**PROJECT, "personas": [{"id": "blue", "name": "Blue", "voice": "Nova", "tone": "Calm."},
                                      PROJECT["personas"][1]]}
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": broken, "beta": PROJECT}, git=True)
    preview = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct."}, "projects": ["alpha"]})
    assert preview["rows"][0]["result"].startswith("refused: slack.yaml: personas[0].voice:"), preview["rows"]
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct."},
                             "projects": ["alpha", "beta", "ghost"], "confirm": True})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert rows["beta"].startswith("wrote and pushed commit "), rows
    assert rows["alpha"].startswith("refused: slack.yaml: personas[0].voice:"), rows
    assert out["partial"], out
    assert [s["project"] for s in out["saved"]] == ["beta"], out["saved"]


@pytest.mark.heavy   # two real repositories and their worktrees
def test_a_commit_written_but_not_pushed_is_named_with_that_result(hub, tmp_path, monkeypatch):
    """#3011 acceptance 10: the save of a project whose commit was written and whose push failed states the commit and
    the failed push, never a plain success and never a silent loss."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT}, git=True)
    origin = made["beta"][2]
    hook = origin / "hooks" / "pre-receive"   # the origin still serves a fetch and refuses every push
    hook.write_text("#!/bin/sh\necho refused by the fixture\nexit 1\n")
    hook.chmod(0o755)
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct."}, "projects": ["alpha", "beta"],
                             "confirm": True})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert re.match(r"^wrote commit [0-9a-f]{7,40} but not pushed: .*push", rows["beta"]), rows["beta"]
    assert rows["alpha"].startswith("wrote and pushed commit "), rows["alpha"]
    assert origin.is_dir(), "the fixture's origin left"
    assert [s["project"] for s in out["saved"]] == ["alpha"], out["saved"]


# ---------------------------------------------------------------- the route: choosing one copy of a drifted persona (#3015)

# Two projects of one client declare `blue` with different fields; one project of another client declares `blue` too.
BLUE_A = {"version": 1, "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."}]}
BLUE_B = {"version": 1, "personas": [{"id": "blue", "name": "Blue9", "voice": "Sulafat", "tone": "Warm."}]}
BLUE_C = {"version": 1, "personas": [{"id": "blue", "name": "BlueX", "voice": "Puck", "tone": "Direct."}]}
CLIENTS = {"clients": [{"id": "acme", "name": "Acme", "projects": ["alpha", "beta"]},
                       {"id": "globex", "name": "Globex", "projects": ["gamma"]}]}


def write_clients(tmp_path):
    (tmp_path / "profiles" / "clients.local.yaml").write_text(yaml.safe_dump(CLIENTS, sort_keys=False))


def test_the_preview_of_a_chosen_copy_targets_only_that_client_s_projects(hub, tmp_path, monkeypatch):
    """#3015 acceptance 2, 3 and 6: the body names the client and the copy; the preview writes the chosen copy for each
    project of that client that declares the id, and never a project of another client."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": BLUE_A, "beta": BLUE_B, "gamma": BLUE_C})
    write_clients(tmp_path)
    before = {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()}
    out = hub.personas_edit({"persona": "blue", "client": "acme", "copy": "beta"})
    assert out.get("err") is None, out
    assert [(r["project"], r["result"]) for r in out["rows"]] == [("alpha", "writes Blue9"), ("beta", "writes Blue9")], out
    assert {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()} == before, "the preview wrote a file"


def test_a_copy_the_client_does_not_hold_is_refused(hub, tmp_path, monkeypatch):
    """#3015 acceptance 6: a copy named from another client's row answers its reason and writes nothing."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": BLUE_A, "beta": BLUE_B, "gamma": BLUE_C})
    write_clients(tmp_path)
    out = hub.personas_edit({"persona": "blue", "client": "acme", "copy": "gamma"})
    assert "gamma" in out["err"] and "not a project of this client" in out["err"], out


@pytest.mark.heavy   # three real repositories and their worktrees
def test_a_confirmed_settle_writes_the_chosen_copy_to_each_client_project_and_clears_the_drift(
        hub, tmp_path, monkeypatch):
    """#3015 acceptance 4, 5 and 6: each project of the client carries the chosen copy, the drift flag clears on the next
    read, and the project of the other client keeps its own copy and its one commit."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": BLUE_A, "beta": BLUE_B, "gamma": BLUE_C}, git=True)
    write_clients(tmp_path)
    preview = hub.personas_overview()
    acme = next(g for g in preview["groups"] if g["id"] == "acme")
    assert acme["rows"][0]["drift"] is True, acme
    out = hub.personas_edit({"persona": "blue", "client": "acme", "copy": "beta", "confirm": True})
    assert out.get("err") is None, out
    assert [s["project"] for s in out["saved"]] == ["alpha", "beta"], out
    landed_docs = {}
    for slug in ("alpha", "beta"):
        co, _c, origin = made[slug]
        landed = subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                                capture_output=True, text=True, check=True, timeout=60).stdout
        assert yaml.safe_load(landed)["personas"][0] == \
               {"id": "blue", "name": "Blue9", "voice": "Sulafat", "tone": "Warm."}, slug
        # The branch the checkout tracks carries the choice, so the project itself is settled, not the hub's tree alone.
        subprocess.run(["git", "-C", str(co), "fetch", "-q", "origin"], check=True, timeout=60)
        subprocess.run(["git", "-C", str(co), "reset", "--hard", "-q", "origin/dev"], check=True, timeout=60)
        landed_docs[slug] = yaml.safe_load((co / "docs" / "squad" / "slack.yaml").read_text())
        assert landed_docs[slug]["personas"][0] == \
               {"id": "blue", "name": "Blue9", "voice": "Sulafat", "tone": "Warm."}, slug
    _co, _c, origin = made["gamma"]
    landed = subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                            capture_output=True, text=True, check=True, timeout=60).stdout
    assert yaml.safe_load(landed)["personas"][0] == BLUE_C["personas"][0], "another client's project was written"
    count = subprocess.run(["git", "-C", str(origin), "rev-list", "--count", "dev"],
                           capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    assert count == "1", f"the other client's project took a commit: {count}"
    after = hub.personas_overview()
    acme_after = next(g for g in after["groups"] if g["id"] == "acme")
    assert acme_after["rows"][0]["drift"] is False, acme_after
    # The landed branches alone settle the row: a read of the projects' own documents carries no drift.
    seats = [{**seat(slug), "committed": doc} for slug, doc in landed_docs.items()]
    row = PS.groups(seats, CL.normalize(CLIENTS))[0]["rows"][0]
    assert row["drift"] is False, row


# ---------------------------------------------------------------- the page: the editor, the preview and the sample

def _node(body):
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node is required")
    out = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _block(page):
    m = re.search(r"^// PRS\{.*?^// \}PRS$", page, re.M | re.S)
    assert m, "the page carries no PRS block"
    return m.group(0)


def _src(hub, tail):
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, _block(hub.PAGE), tail])


ROW = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.",
       "projects": [{"id": "alpha", "label": "alpha", "file": "slack.yaml"},
                    {"id": "beta", "label": "beta", "file": "slack.yaml"}],
       "default_of": [], "drift": False, "read_only": False}


def test_the_editor_offers_only_the_gemini_voices_and_states_the_tone_limit(hub):
    """#3011 acceptance 1 and 3: the voice field draws one option for each voice the read answers and no other, and the
    tone field states the limit the read names."""
    edit = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "projects": ["alpha"],
            "all": ROW["projects"]}
    tail = (f"prS={json.dumps({'err': None, 'groups': [], 'voices': ['Kore', 'Puck'], 'tone_max': 300})};"
            f"prEd={json.dumps(edit)};console.log(prEditor())")
    html = _node(_src(hub, tail))
    opts = re.findall(r"<option[^>]*>([^<]*)</option>", html)
    assert opts == ["Kore", "Puck"], opts
    assert re.search(r"<option selected>Kore</option>", html), html
    assert "at most 300 characters" in html, html
    assert 'data-prproj="alpha"' in html and 'data-prproj="beta"' in html, html


def test_the_editor_puts_the_policy_name_pattern_on_the_name_field(hub):
    """#3012 acceptance 1: the name field states the pattern the read answers, so the browser refuses a name the policy
    refuses before the edit reaches the hub."""
    pat = "^[A-Za-z0-9 _.\\-]{1,32}$"
    edit = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "projects": ["alpha"], "all": []}
    tail = (f"prS={json.dumps({'voices': [], 'tone_max': 300, 'name_pattern': pat})};"
            f"prEd={json.dumps(edit)};console.log(prEditor())")
    html = _node(_src(hub, tail))
    assert re.search(rf'<input id=prname[^>]* pattern="{re.escape(pat)}"', html), html


def test_the_preview_body_names_no_confirm_and_the_confirm_body_confirms(hub):
    """#3011 acceptance 11: the preview body carries `confirm: false` and the confirm body `confirm: true`, so no edit
    of the theme writes before the confirm."""
    edit = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "projects": ["alpha"], "all": []}
    tail = (f"prS={{voices:[],tone_max:300}};prEd={json.dumps(edit)};"
            "console.log(JSON.stringify([JSON.parse(prBody(false)),JSON.parse(prBody(true))]))")
    prev, conf = json.loads(_node(_src(hub, tail)))
    assert prev["confirm"] is False and conf["confirm"] is True, (prev, conf)
    for body in (prev, conf):
        assert body["persona"] == "blue", body
        assert body["fields"] == {"name": "Blue", "voice": "Kore", "tone": "Calm."}, body
        assert body["projects"] == ["alpha"], body


def test_the_body_follows_the_editor_mode_not_a_stale_copy_field(hub):
    """The editor's mode decides the body: a field edit that still carries a copy project from an earlier choice posts
    its fields and projects, never the copy."""
    edit = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "projects": ["alpha"], "all": [],
            "mode": "fields", "copy": "beta"}
    tail = (f"prS={{voices:[],tone_max:300}};prEd={json.dumps(edit)};console.log(prBody(false))")
    body = json.loads(_node(_src(hub, tail)))
    assert body == {"persona": "blue", "fields": {"name": "Blue", "voice": "Kore", "tone": "Calm."},
                    "projects": ["alpha"], "confirm": False}, body


def test_the_preview_draws_the_rename_notes_and_the_client_map_rows(hub):
    """#3012 acceptance 2, 3 and 4: the page draws the sentences the route answers, the client map rows included."""
    plan = {"rows": [], "notes": ["the @Blue address becomes @Azure",
                                  "the authorship mark becomes *Azure's Msg*"],
            "map": [{"client": "atlas", "label": "Atlas Corp", "result": "writes Azure"}]}
    tail = f"prPlan={json.dumps(plan)};console.log(prPlanHtml())"
    html = _node(_src(hub, tail))
    assert "the @Blue address becomes @Azure" in html, html
    assert "the authorship mark becomes *Azure&#39;s Msg*" in html or "the authorship mark becomes *Azure's Msg*" in html, html
    assert "Atlas Corp" in html and "writes Azure" in html, html


def test_the_preview_survives_a_plan_whose_notes_and_map_are_not_rows(hub):
    """A plan from an older hub, whose `notes` or `map` carries no list, still draws the project rows instead of
    throwing, so the preview never goes blank on an answer of another shape."""
    plan = {"rows": [{"project": "alpha", "file": "slack.yaml", "result": "writes Azure"}],
            "notes": "old", "map": {"client": "atlas"}}
    tail = f"prPlan={json.dumps(plan)};console.log(prPlanHtml())"
    html = _node(_src(hub, tail))
    assert "writes Azure" in html, html


def test_the_sample_button_plays_the_chosen_voice_through_the_sample_route(hub):
    """#3011 acceptance 2: the sample button posts the edited voice and tone to the existing sample route."""
    edit = {"id": "blue", "name": "Blue", "voice": "Puck", "tone": "Warm.", "projects": ["alpha"], "all": []}
    tail = (f"prS={{voices:['Kore','Puck'],tone_max:300,groups:[]}};prEd={json.dumps(edit)};"
            "const CALLS=[];globalThis.fetch=(u,o)=>{CALLS.push([u,JSON.parse(o.body)]);"
            "return Promise.resolve({json:()=>Promise.resolve({err:'refused'})})};"
            "(async()=>{await prSample();console.log(JSON.stringify(CALLS))})()")
    calls = json.loads(_node(_src(hub, tail)))
    assert calls == [["/api/slack/sample", {"voice": "Puck", "tone": "Warm."}]], calls


def test_the_personas_theme_holds_no_autosave_and_the_slack_theme_keeps_its_own(hub):
    """#3011 acceptance 11 and 12: the Personas block schedules no save of its own, and the Slack theme's autosave stays
    on the page."""
    block = _block(hub.PAGE)
    assert "setTimeout" not in block, "the Personas theme schedules a write of its own"
    assert "/api/slack/files" not in block, "the Personas theme takes the Slack save path"
    assert "slAutoSave" in hub.PAGE and "SL_COMMIT_MS" in hub.PAGE, "the Slack theme's autosave left the page"


COPIES_ROW = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.",
              "projects": [{"id": "alpha", "label": "alpha", "file": "slack.yaml"},
                           {"id": "beta", "label": "beta", "file": "slack.yaml"}],
              "copies": [{"project": "alpha", "label": "alpha", "name": "Blue", "voice": "Kore", "tone": "Calm.",
                          "file": "slack.yaml"},
                         {"project": "beta", "label": "beta", "name": "Blue9", "voice": "Sulafat", "tone": "Warm.",
                          "file": "slack.yaml"}],
              "default_of": [], "drift": True, "read_only": False}


def test_a_drifted_row_draws_each_copy_with_its_project_and_a_choice(hub):
    """#3015 acceptance 1 and 2: the drifted row draws each copy with the project that declares it, and each copy is a
    control the owner presses to choose it."""
    html = _node(_src(hub, f"console.log(prRow({json.dumps(COPIES_ROW)},'acme'))"))
    assert 'data-prcopy="alpha"' in html and 'data-prcopy="beta"' in html, html
    assert 'data-prclient="acme"' in html, html
    assert "Blue9" in html and "Sulafat" in html and "Warm." in html, html


def test_choosing_a_copy_posts_the_client_and_the_copy_and_writes_nothing_before_the_confirm(hub):
    """#3015 acceptance 2 and 3: the choice starts an edit that names the client and the copy, and its preview body and
    its confirm body carry the same choice, so the hub alone decides the targets."""
    answer = {"err": None, "groups": [{"id": "acme", "name": "Acme", "rows": [COPIES_ROW]}],
              "voices": ["Kore", "Sulafat"], "tone_max": 300}
    tail = (f"prS={json.dumps(answer)};prCopyStart('acme','beta');"
            "console.log(JSON.stringify({prev:JSON.parse(prBody(false)),conf:JSON.parse(prBody(true)),"
            "name:prEd.name,voice:prEd.voice,editor:prEditor()}))")
    got = json.loads(_node(_src(hub, tail)))
    assert got["prev"] == {"persona": "blue", "client": "acme", "copy": "beta", "confirm": False}, got
    assert got["conf"] == {"persona": "blue", "client": "acme", "copy": "beta", "confirm": True}, got
    assert (got["name"], got["voice"]) == ("Blue9", "Sulafat"), got
    editor = got["editor"]
    assert 'data-prcopy="alpha"' in editor and 'data-prcopy="beta"' in editor, editor
    assert re.search(r'data-prcopy="beta"[^>]*checked', editor), editor
    assert "setTimeout" not in _block(hub.PAGE), "the Personas theme schedules a write of its own"


def test_the_write_route_answers_over_loopback(hub, monkeypatch):
    """The page posts to `/api/personas`, and the route answers the edit's own answer as JSON."""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(hub, "personas_edit", lambda body: {"seen": body.get("persona")})   # a success carries no err key
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/personas",
                                     data=json.dumps({"persona": "blue"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Origin": f"http://127.0.0.1:{srv.server_address[1]}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            got = json.loads(r.read())
    finally:
        srv.shutdown()
        srv.server_close()
    assert got == {"seen": "blue"}, got


# ---------------------------------------------------------------- the removal: the preview and the save (#3014)

def test_a_removal_with_no_holder_passes_the_preview_and_writes_none(hub, tmp_path, monkeypatch):
    """#3014 acceptance 8: a removal preview names each project's file, and no file changes."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT})
    before = {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()}
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha", "beta"]})
    assert out.get("err") is None, out
    assert {r["project"]: r["result"] for r in out["rows"]} == {"alpha": "removes Amber", "beta": "removes Amber"}, out
    assert {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()} == before, "the preview wrote a file"


def test_a_removal_a_place_names_is_refused_and_the_refusal_names_each_holder(hub, tmp_path, monkeypatch):
    """#3014 acceptance 2, 4 and 6: the project row holds the persona, and the refusal names the place and its file."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    out = hub.personas_remove({"persona": "blue", "projects": ["alpha"]})
    assert out["rows"][0]["result"].startswith("refused: held by "), out["rows"]
    assert "project in slack.yaml" in out["rows"][0]["result"], out["rows"]
    assert out["saved"] == [], out["saved"]


def test_a_client_default_blocks_only_the_project_it_holds(hub, tmp_path, monkeypatch):
    """#3014 acceptance 5 and 7: a client whose default is the persona and that holds alpha refuses alpha alone, and
    beta, which the client does not hold, keeps its removal."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT})
    (prof / "clients.local.yaml").write_text(yaml.safe_dump(
        {"clients": [{"id": "acme", "name": "Acme", "default_persona": {"id": "amber", "name": "Amber"},
                      "projects": ["alpha"]}]}, sort_keys=False))
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha", "beta"]})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows["alpha"].startswith("refused: held by "), rows
    assert 'client "acme" default' in rows["alpha"], rows
    assert rows["beta"] == "removes Amber", rows


def local_file(prof, slug, doc):
    """Write one project's local Slack file into the profile folder the roster reads."""
    (prof / f"{slug}.slack.local.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_channel_of_the_local_file_blocks_the_removal_and_names_its_file(hub, tmp_path, monkeypatch):
    """#3014 acceptance 2 and 6: a channel of slack.local.yaml that names the persona refuses the removal, and the
    refusal names the channel, its workspace and the local file."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    local_file(prof, "alpha", {"version": 1, "workspaces": [
        {"id": "T1", "label": "team",
         "channels": [{"id": "C1", "label": "support", "persona": "amber"}]}]})
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha"]})
    result = out["rows"][0]["result"]
    assert result == 'refused: held by channel "support" of workspace "team" in alpha.slack.local.yaml', out["rows"]
    assert out["saved"] == [], out["saved"]


def test_a_workspace_of_the_local_file_blocks_the_removal_and_names_its_file(hub, tmp_path, monkeypatch):
    """#3014 acceptance 3 and 6: a workspace of slack.local.yaml that names the persona refuses the removal, and the
    refusal names the workspace and the local file."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    local_file(prof, "alpha", {"version": 1, "workspaces": [{"id": "T1", "label": "team", "persona": "amber"}]})
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha"]})
    result = out["rows"][0]["result"]
    assert result == 'refused: held by workspace "team" in alpha.slack.local.yaml', out["rows"]
    assert out["saved"] == [], out["saved"]


def test_a_project_row_of_the_local_file_blocks_the_removal_and_names_its_file(hub, tmp_path, monkeypatch):
    """#3014 acceptance 4 and 6: the local file's project row that names the persona refuses the removal, and the
    refusal names the project row and the local file."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    local_file(prof, "alpha", {"version": 1, "project": {"persona": "amber"}})
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha"]})
    result = out["rows"][0]["result"]
    assert result == "refused: held by project in alpha.slack.local.yaml", out["rows"]
    assert out["saved"] == [], out["saved"]


def test_a_raw_client_map_with_string_projects_still_answers_its_holders(hub, tmp_path, monkeypatch):
    """#3014 acceptance 5 and 7: the holder read accepts a project entry written as a bare id, so a raw map blocks the
    project it holds and never another one."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT})
    monkeypatch.setattr(hub, "_clients_map_read", lambda: ([
        {"id": "acme", "name": "Acme", "default_persona": {"id": "amber", "name": "Amber"},
         "projects": ["alpha"]}], None))
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha", "beta"]})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows["alpha"] == 'refused: held by client "acme" default', rows
    assert rows["beta"] == "removes Amber", rows


@pytest.mark.heavy   # a real repository, its worktree and a push
def test_a_confirmed_removal_drops_the_row_from_the_committed_file(hub, tmp_path, monkeypatch):
    """#3014 acceptance 8: the confirmed save lands the committed file without the persona's row, and no other row
    moves."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT}, git=True)
    out = hub.personas_remove({"persona": "amber", "projects": ["alpha"], "confirm": True})
    assert out.get("err") is None, out
    assert out["rows"][0]["result"].startswith("wrote and pushed commit "), out["rows"]
    origin = made["alpha"][2]
    landed = subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                            capture_output=True, text=True, check=True, timeout=60).stdout
    doc = yaml.safe_load(landed)
    assert [row["id"] for row in doc["personas"]] == ["blue"], doc
    assert doc["project"] == PROJECT["project"], doc
