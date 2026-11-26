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


def test_a_project_that_does_not_validate_is_named_refused_and_the_others_are_written(hub, tmp_path, monkeypatch):
    """#3011 acceptance 8 and 9: the preview names the project whose file a save would not validate, and a confirmed save
    refuses it by name and still writes every other target."""
    prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": PROJECT, "beta": PROJECT}, git=True)
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    # A name the policy refuses makes the built document fail validation in that project alone.
    preview = hub.personas_edit({"persona": "blue", "fields": {"name": "Blue!"}, "projects": ["alpha"]})
    assert preview["rows"][0]["result"].startswith("refused: slack.yaml: personas[0].name:"), preview["rows"]
    out = hub.personas_edit({"persona": "blue", "fields": {"tone": "Direct."}, "projects": ["alpha", "ghost"],
                             "confirm": True})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert rows["alpha"].startswith("wrote and pushed commit "), rows
    assert out["partial"], out
    assert [s["project"] for s in out["saved"]] == ["alpha"], out["saved"]


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
