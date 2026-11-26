"""GitHub issue #3013: the owner adds a persona in the Personas theme (SPEC 3009).

The owner creates a persona with a name, a voice and a tone, or adds an existing persona to another project, and chooses
the target projects. The hub builds each target project's committed document through the persona rules module and calls
that project's existing save path from the project's own worktree: a preview writes no file, and a confirmed save writes,
commits and lands each target project.

The seams under test are the hub's own add, `personas_add`, the rules module `squad/personas.py`, and the served page's
renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

from squad import personas as PS

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A neutral project that declares two personas and one workspace.
PROJECT = {
    "version": 1,
    "project": {"language": "en-US", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                 {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned",
         "channels": [{"id": "C0PROJ0001", "name": "project", "mode": "listen+speak", "roles": ["manager"]}]},
    ],
}
# A neutral project that declares one persona alone.
OTHER = {
    "version": 1,
    "project": {"language": "en-US", "persona": "amber"},
    "personas": [{"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
}
LOCAL = {"version": 1}
NIGHT = {"name": "Night Owl", "voice": "Puck", "tone": "Direct."}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module resolves from this tree."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_personas_add", HUB_PY)
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


# ---------------------------------------------------------------- the rules module: one new persona row

def test_the_id_of_a_new_persona_is_a_slug_of_its_name():
    """The theme derives the id from the name the owner writes, so a name with a space still names one id."""
    assert PS.persona_id("Night Owl") == "night-owl", PS.persona_id("Night Owl")
    assert PS.persona_id("  Blue  ") == "blue", PS.persona_id("  Blue  ")


def test_the_rules_add_one_row_and_every_other_key_keeps_its_form():
    """The new row is appended with its four fields, and the other personas and every other key keep their place."""
    doc = {"version": 1, "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."}]}
    got = PS.with_persona(doc, {"id": "night-owl", **NIGHT})
    assert got["personas"] == [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                               {"id": "night-owl", "name": "Night Owl", "voice": "Puck", "tone": "Direct."}], got
    assert got["version"] == 1, got
    assert len(doc["personas"]) == 1, "the input document changed"


def test_a_document_that_declares_the_id_answers_no_document():
    """The add refuses an id the document already declares, never a second row with the same id."""
    assert PS.with_persona({"personas": [{"id": "amber", "name": "Amber"}]},
                           {"id": "amber", **NIGHT}) is None


def test_the_add_plan_names_each_refused_project_and_carries_the_other_writes():
    """A target outside the roster, a project with no worktree, a file whose read answered a defect and a project that
    already declares the id are each refused by name; the other targets keep their write."""
    seats = [seat("alpha", PROJECT),
             seat("beta", PROJECT, checkout=""),
             seat("gamma", PROJECT, read_defects=["slack.yaml: not YAML"]),
             seat("delta", OTHER)]
    plan = PS.add(seats, {"id": "night-owl", **NIGHT}, ["alpha", "beta", "gamma", "delta", "ghost"])
    rows = {r["project"]: r["result"] for r in plan["rows"]}
    assert rows["alpha"] == "writes Night Owl", rows
    assert rows["beta"] == "refused: the project has no worktree", rows
    assert rows["gamma"] == "refused: the file does not read: slack.yaml: not YAML", rows
    assert rows["delta"] == "writes Night Owl", rows
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert [w["project"] for w in plan["writes"]] == ["alpha", "delta"], plan["writes"]
    assert plan["writes"][0]["committed"]["personas"][-1] == {"id": "night-owl", **NIGHT}, plan["writes"]
    # An id the target already declares is refused, and the refusal names the target project.
    again = PS.add(seats, {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}, ["alpha", "delta"])
    assert {r["project"]: r["result"] for r in again["rows"]} == \
        {"delta": "refused: delta already declares amber",
         "alpha": "refused: alpha already declares amber"}, again["rows"]
    assert again["writes"] == [], again


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


def wire(tmp_path, monkeypatch, hub, docs, git=False, clients=None):
    """One checkout and one profile file for each project, the profile folder the roster reads, and the client map."""
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
    if clients is not None:
        (prof / "clients.local.yaml").write_text(
            yaml.safe_dump({"version": 1, "clients": clients}, sort_keys=False))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(hub, "STATE", state)
    return prof, made


def add_body(**over):
    """The add's body: the persona, the target projects and whether the call confirms."""
    body = {"op": "add", "persona": dict(NIGHT), "projects": ["alpha"]}
    body.update(over)
    return body


def test_a_new_persona_needs_a_name_a_voice_and_a_tone(hub):
    """#3013 acceptance 1: the route refuses a body that names no name, a voice outside the Gemini list and a tone over
    the limit, and each refusal names the field."""
    assert hub.personas_add({"op": "add", "persona": {"voice": "Puck", "tone": "Direct."},
                             "projects": ["alpha"]})["err"] == \
        "persona.name: a body that names the persona's name"
    assert "is not a Gemini voice" in hub.personas_add(
        add_body(persona={"name": "Night Owl", "voice": "Nope", "tone": "Direct."}))["err"]
    assert hub.personas_add(add_body(persona={**NIGHT, "tone": "x" * 301}))["err"] == \
        "persona.tone: a tone holds at most 300 characters (301 given)"


def test_the_route_derives_the_id_from_the_name_the_owner_writes(hub, tmp_path, monkeypatch):
    """#3013 acceptance 1 and 2: a persona posted with no id takes the slug of its name, and the preview writes nothing."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT})
    out = hub.personas_add(add_body())
    assert out.get("err") is None, out
    assert out["persona"] == "night-owl", out


def test_the_preview_names_each_project_file_and_writes_none(hub, tmp_path, monkeypatch):
    """#3013 acceptance 2 and 4: the preview names each target project's file, and no file changes and no worktree
    appears."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT})
    before = {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()}
    out = hub.personas_add(add_body(projects=["alpha", "beta"]))
    assert out.get("err") is None, out
    files = {r["project"]: r["file"] for r in out["rows"]}
    assert files["alpha"].endswith("/alpha/docs/squad/slack.yaml"), files
    assert files["beta"].endswith("/beta/docs/squad/slack.yaml"), files
    assert {r["project"]: r["result"] for r in out["rows"]} == \
        {"alpha": "writes Night Owl", "beta": "writes Night Owl"}, out
    assert {slug: c.read_bytes() for slug, (_co, c, _o) in made.items()} == before, "the preview wrote a file"
    assert not (tmp_path / "state" / "slack-wt").exists(), "the preview made a worktree"


@pytest.mark.heavy   # two real repositories and their worktrees
def test_a_confirmed_add_writes_the_row_into_each_target_file_and_lands_one_commit(hub, tmp_path, monkeypatch):
    """#3013 acceptance 5: each target project's committed file carries the new row, and each project lands one commit
    in its own origin."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": OTHER}, git=True)
    out = hub.personas_add(add_body(projects=["alpha"], confirm=True))
    assert out.get("err") is None, out
    assert out["rows"][0]["result"].startswith("wrote and pushed commit "), out["rows"]
    landed = subprocess.run(["git", "-C", str(made["alpha"][2]), "show", "dev:docs/squad/slack.yaml"],
                            capture_output=True, text=True, check=True, timeout=60).stdout
    doc = yaml.safe_load(landed)
    assert doc["personas"][-1] == {"id": "night-owl", "name": "Night Owl", "voice": "Puck",
                                   "tone": "Direct."}, doc["personas"]
    assert [p["id"] for p in doc["personas"]] == ["blue", "amber", "night-owl"], doc["personas"]
    count = subprocess.run(["git", "-C", str(made["alpha"][2]), "rev-list", "--count", "dev"],
                           capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    assert count == "2", f"one commit above the seed: {count}"


@pytest.mark.heavy   # two real repositories and their worktrees
def test_an_existing_persona_is_added_to_another_project(hub, tmp_path, monkeypatch):
    """#3013 acceptance 3 and 5: a persona another project declares is added to a project that does not declare it."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": OTHER}, git=True)
    out = hub.personas_add(add_body(persona={"id": "blue", "name": "Blue", "voice": "Kore",
                                             "tone": "Calm."}, projects=["beta"], confirm=True))
    assert out.get("err") is None, out
    assert out["rows"][0]["result"].startswith("wrote and pushed commit "), out["rows"]
    landed = subprocess.run(["git", "-C", str(made["beta"][2]), "show", "dev:docs/squad/slack.yaml"],
                            capture_output=True, text=True, check=True, timeout=60).stdout
    assert {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."} in yaml.safe_load(landed)["personas"]


@pytest.mark.heavy   # one real repository and its worktree
def test_an_id_the_target_already_declares_is_refused_and_the_message_names_the_project(hub, tmp_path, monkeypatch):
    """#3013 acceptance 6: the add of an id the target project already declares is refused, the message names the
    project, and no commit lands."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT}, git=True)
    out = hub.personas_add(add_body(persona={"id": "blue", "name": "Blue", "voice": "Puck", "tone": "Direct."},
                                    projects=["alpha"], confirm=True))
    assert out["rows"][0]["result"] == "refused: alpha already declares blue", out["rows"]
    assert out["partial"] is True and out["saved"] == [], out
    count = subprocess.run(["git", "-C", str(made["alpha"][2]), "rev-list", "--count", "dev"],
                           capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    assert count == "1", f"no commit above the seed: {count}"


@pytest.mark.heavy   # two real repositories and their worktrees
def test_the_client_default_persona_select_offers_the_added_persona_after_the_save(hub, tmp_path, monkeypatch):
    """#3013 acceptance 7: after a confirmed add, the client's Default persona select offers the added persona, because
    the read merges the same theme rows a fresh roster answers."""
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    clients = [{"id": "one", "name": "One", "default_persona": "blue",
                "projects": [{"id": "alpha", "follows": True}]}]
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT}, git=True, clients=clients)
    # The project's checkout is the hub's own, so the read takes the file the save landed (issue #2920).
    monkeypatch.setenv("SG_CHECKOUT", str(made["alpha"][0]))
    monkeypatch.setattr(hub, "CHECKOUT", str(made["alpha"][0]))
    before = hub.slack_tree()
    options = {c["name"]: c for c in (before.get("tree") or before)["clients"]}["One"]["persona_options"]
    assert "night-owl" not in [o["id"] for o in options], options
    out = hub.personas_add(add_body(projects=["alpha"], confirm=True))
    assert out.get("err") is None, out
    after = hub.slack_tree()
    options = {c["name"]: c for c in (after.get("tree") or after)["clients"]}["One"]["persona_options"]
    assert {"id": "night-owl", "name": "Night Owl"} in options, options


def test_the_read_offers_every_roster_project_as_a_target(hub, tmp_path, monkeypatch):
    """#3013 acceptance 2: the theme's read names every roster project and its file, so the add offers them as targets."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": OTHER})
    got = hub.personas_overview()
    assert got.get("err") is None, got
    targets = {p["id"]: p for p in got["projects"]}
    assert set(targets) == {"alpha", "beta"}, targets
    assert targets["alpha"]["file"].endswith("/alpha/docs/squad/slack.yaml"), targets


# ---------------------------------------------------------------- the page: the new-persona form

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


TARGETS = [{"id": "alpha", "label": "alpha", "file": "docs/squad/slack.yaml"},
           {"id": "beta", "label": "beta", "file": "docs/squad/slack.yaml"}]


def test_the_new_persona_form_offers_the_gemini_voices_and_every_roster_project(hub):
    """#3013 acceptance 1 and 2: the new-persona form draws a name field, one option for each Gemini voice, the tone
    limit and one checkbox for each roster project."""
    state = json.dumps({"err": None, "groups": [], "voices": ["Kore", "Puck"], "tone_max": 300, "projects": TARGETS})
    form = json.dumps({"name": "", "voice": "Kore", "tone": "", "projects": []})
    tail = f"prS={state};prNew={form};console.log(prAddEditor())"
    html = _node(_src(hub, tail))
    assert 'id="prnewname"' in html, html
    opts = re.findall(r"<option[^>]*>([^<]*)</option>", html)
    assert opts == ["Kore", "Puck"], opts
    assert "at most 300 characters" in html, html
    assert 'data-praddproj="alpha"' in html and 'data-praddproj="beta"' in html, html


def test_the_add_preview_body_names_no_confirm_and_the_add_confirm_body_confirms(hub):
    """#3013 acceptance 4: the add's preview body carries `confirm: false` and its confirm body `confirm: true`, and the
    body names the new persona's fields, its derived id and the chosen projects."""
    form = json.dumps({"name": "Night Owl", "voice": "Puck", "tone": "Direct.", "projects": ["alpha"]})
    tail = ("prS={voices:['Kore','Puck'],tone_max:300,groups:[],projects:[]};"
            f"prNew={form};"
            "console.log(JSON.stringify([JSON.parse(prAddBody(false)),JSON.parse(prAddBody(true))]))")
    prev, conf = json.loads(_node(_src(hub, tail)))
    assert prev["confirm"] is False and conf["confirm"] is True, (prev, conf)
    for body in (prev, conf):
        assert body["op"] == "add", body
        assert body["persona"] == {"id": "night-owl", "name": "Night Owl", "voice": "Puck",
                                   "tone": "Direct."}, body
        assert body["projects"] == ["alpha"], body


def test_an_existing_row_opens_the_add_editor_with_its_projects(hub):
    """#3013 acceptance 3: a row's Add to project button opens the add editor with that persona's fields and the
    projects that do not declare it."""
    row = {"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.",
           "projects": [{"id": "alpha", "label": "alpha", "file": "docs/squad/slack.yaml"}],
           "default_of": [], "drift": False, "read_only": False}
    state = json.dumps({"err": None, "voices": ["Kore", "Puck"], "tone_max": 300, "projects": TARGETS,
                        "groups": [{"id": "one", "name": "One", "rows": [row]}]})
    tail = f"prS={state};prStartAdd('blue');console.log(JSON.stringify(prNew))"
    got = json.loads(_node(_src(hub, tail)))
    assert got["id"] == "blue" and got["name"] == "Blue" and got["voice"] == "Kore", got
    assert got["projects"] == ["beta"], got
