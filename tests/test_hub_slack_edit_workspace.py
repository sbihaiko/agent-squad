"""GitHub issue #3034: another profile's workspace and channel nodes are edited from any hub page (SPEC 3031).

The tree draws the workspaces and the channels of every project the hub knows. The owner edits the Persona and the
Language of a workspace node and of a channel node that belong to another profile's project, through the same
project-bound save the project node uses. Each edit writes the file that holds the row: the committed file or the
local file, as on the project's own page. Every other setting of those nodes stays read-only.

The seams are the hub's tree, the edit route `slack_docs_edit`, the save `slack_files_set`, and the page's own
functions run in node over stubbed timers and a stubbed fetch. Every id and name is a neutral placeholder, and every
repository is a temporary fixture.
"""
import json
import pathlib
import re
import subprocess

import pytest
import yaml

from tests import test_hub_slack_clients as C
from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
_node, _slices = C._node, C._slices

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALPHA = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": "T0ALPHA001", "label": "alpha-team", "class": "owned",
                    "channels": [{"id": "C0ALPHA001", "name": "alpha-room", "mode": "listen"}]}],
}
BETA = {
    "version": 1,
    "project": {"language": "es-ES", "persona": "nova"},
    "personas": [{"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Short."},
                 {"id": "atlas", "name": "Atlas", "voice": "Kore", "tone": "Plain."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": "T0BETA0001", "label": "beta-team", "class": "owned", "persona": "nova",
                    "channels": [{"id": "C0BETA0001", "name": "beta-room", "mode": "listen"}]}],
}
LOCAL = {"version": 1}


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def repo(tmp_path, name, doc, local=None):
    """A project checkout that is a git repository at origin/dev: `doc` is its committed Slack file, and `local` is the
    profile folder's local file. The bare origin answers the commits the save lands."""
    origin = tmp_path / f"origin-{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/dev")
    co = tmp_path / "checkouts" / name
    co.mkdir(parents=True)
    git(co, "init", "-q", "-b", "dev")
    git(co, "config", "user.name", "fixture")
    git(co, "config", "user.email", "fixture@localhost")
    c = co / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    c.write_text(yaml.safe_dump(doc, sort_keys=False))
    git(co, "add", "--", "docs/squad/slack.yaml")
    git(co, "commit", "-q", "-m", "seed the Slack file")
    git(co, "remote", "add", "origin", str(origin))
    git(co, "push", "-q", "origin", "dev")
    git(co, "fetch", "-q", "origin")
    return co, origin


def wire(tmp_path, monkeypatch, hub, projects, locals=None):
    """The hub's own seam: one git checkout and one profile file per project, the client map, and its state directory.
    `projects` maps a slug to its committed document; `locals` maps a slug to its local document."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, doc in projects.items():
        local_doc = (locals or {}).get(slug, LOCAL)
        co, origin = repo(tmp_path, slug, doc, local_doc)
        (prof / f"{slug}.sh").write_text(
            f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={slug}}}"; '
            f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
        loc = prof / f"{slug}.slack.local.yaml"
        loc.write_text(yaml.safe_dump(local_doc, sort_keys=False))
        made[slug] = {"checkout": co, "origin": origin, "committed": co / "docs" / "squad" / "slack.yaml", "local": loc}
    (prof / "clients.local.yaml").write_text(yaml.safe_dump(
        {"version": 1, "clients": [{"id": "acme", "name": "Acme", "projects": sorted(projects)}]}, sort_keys=False))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))   # the page's own project, never a seat
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)   # each fixture owns its own derived worktree per project
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    return prof, made


def origin_file(made, slug):
    """The committed document at origin/dev of one project."""
    text = git(made[slug]["origin"], "show", "dev:docs/squad/slack.yaml")
    return yaml.safe_load(text)


def origin_commits(made, slug):
    return int(git(made[slug]["origin"], "rev-list", "--count", "dev"))


# ---------------------------------------------------------------- the tree edits a workspace and a channel of another project

def _seat(tmp_path, name, committed, local):
    root = tmp_path / name
    root.mkdir()
    c, loc = root / "slack.yaml", root / f"{name}.slack.local.yaml"
    c.write_text(yaml.safe_dump(committed))
    loc.write_text(yaml.safe_dump(local))
    return {"id": name, "label": name, "checkout": str(root), "committed": committed, "local": local,
            "committed_path": str(c), "local_path": str(loc), "read_defects": [],
            "cf": "slack.yaml", "lf": loc.name}


MAP = [{"id": "acme", "name": "Acme", "default_persona": None,
        "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": True}]}]


@pytest.fixture
def client_map(hub, monkeypatch):
    """The client map of the fixture tree: a project no client names is drawn nowhere (#2922)."""
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (json.loads(json.dumps(MAP)), None))


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def _tree(hub, tmp_path, other=None, other_local=None):
    here = _seat(tmp_path, "alpha", ALPHA, LOCAL)
    beta = _seat(tmp_path, "beta", other or BETA, other_local or LOCAL)
    P = hub.slack_policy()
    return hub._slack_clients_tree_for(P, [here, beta], here["committed_path"]), here, beta


def _project(tree, name):
    return next(n for c in tree["clients"] for n in walk(c) if n["kind"] == "project" and n["name"] == name)


def _controls(node):
    return [c for r in (node.get("settings") or {}).get("rows") or [] for c in r.get("controls") or []]


def test_a_workspace_node_of_another_project_offers_the_persona_and_language_controls(hub, tmp_path, client_map):
    """#3034 acceptance 1: the workspace node of another profile's project shows the Persona and Language selects, and
    each carries the row's own value from that project's file."""
    tree, _, _ = _tree(hub, tmp_path)
    ws = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "workspace")
    controls = {c["field"]: c for c in _controls(ws)}
    assert controls["persona"]["kind"] == "select", controls
    assert controls["persona"]["value"] == "nova", controls
    assert controls["language"]["kind"] == "select" and controls["language"]["value"] == "", controls
    assert {c["scope"] for c in controls.values()} == {"ws:T0BETA0001"}, controls


def test_a_channel_node_of_another_project_offers_the_persona_and_language_controls(hub, tmp_path, client_map):
    """#3034 acceptance 2: the channel node of another profile's project shows the Persona and Language selects, and each
    carries the row's own value."""
    tree, _, _ = _tree(hub, tmp_path)
    channel = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "channel")
    controls = {c["field"]: c for c in _controls(channel)}
    assert controls["persona"]["kind"] == "select" and controls["persona"]["value"] == "", controls
    assert controls["language"]["kind"] == "select" and controls["language"]["value"] == "", controls


def test_every_other_setting_of_another_projects_workspace_and_channel_stays_read_only(hub, tmp_path, client_map):
    """#3034 acceptance 8: the other rows of a channel, and the add and remove operations of both nodes, stay read-only,
    so an operation of another profile's project never reaches this page."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    for node in walk(beta):
        if node is beta:
            continue
        assert not node.get("channel_add") and not node.get("channel_remove"), node["name"]
        for row in (node.get("settings") or {}).get("rows") or []:
            for c in row.get("controls") or []:
                if row.get("key") in ("persona", "language"):
                    assert c["kind"] == "select", (node["name"], row["key"], c)
                else:
                    assert c["kind"] == "read", (node["name"], row["key"], c)


def test_the_workspace_node_names_no_edited_elsewhere_sentence_and_a_channel_names_only_other_settings(
        hub, tmp_path, client_map):
    """#3034 acceptance 7: neither node claims that its Persona and Language are edited on the project's page. The
    workspace shows no sentence at all, and the channel's sentence stays for the settings that page still edits."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    ws = next(n for n in walk(beta) if n["kind"] == "workspace")
    channel = next(n for n in walk(beta) if n["kind"] == "channel")
    assert "elsewhere" not in ws, ws.get("elsewhere")
    for node, sentence in ((ws, False), (channel, True)):
        body = (_slices(hub.PAGE) + f"\nslS={{}};slR={{}};slT={json.dumps(tree)};slTsel={json.dumps(node['id'])};"
                + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
        html = json.loads(_node(body))
        detail = html[html.index("id=sltdetail"):]
        assert "Edited on " not in detail, detail
        assert ("other settings are edited on" in detail) is sentence, detail

# ---------------------------------------------------------------- each edit writes the file that holds the row

GUEST_LOCAL = {"version": 1, "workspaces": [
    {"id": "T0GUEST001", "label": "guest-team", "class": "guest",
     "channels": [{"id": "C0GUEST001", "name": "guest-room", "class": "guest", "mode": "listen"}]}]}


def _docs(hub, body):
    out = hub.slack_docs_edit(body)
    assert out.get("err") is None, out
    return out["documents"]


def _workspace(doc, ws_id):
    return next(w for w in doc.get("workspaces") or [] if w.get("id") == ws_id)


def _channel(doc, ch_id):
    return next(c for w in doc.get("workspaces") or [] for c in w.get("channels") or [] if c.get("id") == ch_id)


@pytest.mark.heavy
def test_an_edit_of_a_workspace_row_of_the_committed_file_writes_that_file(hub, tmp_path, monkeypatch):
    """#3034 acceptance 3: an edit of a workspace row the committed file holds lands one commit in the project's own
    repository, and its committed file carries the new persona."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": ALPHA, "beta": BETA})
    before = origin_commits(made, "beta")
    out = _docs(hub, {"project": "beta", "op": {"kind": "field", "scope": "workspace",
                                                "workspace": "T0BETA0001", "field": "persona", "value": "atlas"}})
    assert _workspace(out["committed"], "T0BETA0001")["persona"] == "atlas", out["committed"]
    saved = hub.slack_files_post({"project": "beta", "committed": out["committed"], "local": out["local"],
                                  "confirm": True})
    assert saved.get("err") is None and saved.get("sha"), saved
    assert origin_commits(made, "beta") == before + 1
    assert _workspace(origin_file(made, "beta"), "T0BETA0001")["persona"] == "atlas"
    assert origin_commits(made, "alpha") == 1, "the page's own project takes no commit"


@pytest.mark.heavy
def test_an_edit_of_a_guest_workspace_row_writes_the_local_file_outside_the_repository(hub, tmp_path, monkeypatch):
    """#3034 acceptance 4 and 5: a guest workspace row the local file holds writes that local file, atomically and
    outside every repository, and the save lands no commit."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA}, locals={"beta": GUEST_LOCAL})
    before = origin_commits(made, "beta")
    out = _docs(hub, {"project": "beta", "op": {"kind": "field", "scope": "workspace",
                                                "workspace": "T0GUEST001", "field": "persona", "value": "atlas"}})
    assert _workspace(out["local"], "T0GUEST001")["persona"] == "atlas", out["local"]
    assert _workspace(out["committed"], "T0BETA0001").get("persona") == "nova", "the committed row keeps its value"
    saved = hub.slack_files_post({"project": "beta", "committed": out["committed"], "local": out["local"],
                                  "confirm": True})
    assert saved.get("err") is None, saved
    assert not saved.get("sha"), saved
    local = made["beta"]["local"]
    assert _workspace(yaml.safe_load(local.read_text()), "T0GUEST001")["persona"] == "atlas"
    assert made["beta"]["checkout"] not in local.resolve().parents, "the local file stays outside the repository"
    assert not list(local.parent.glob(f".{local.name}.*.tmp")), "no temp file is left beside the local file"
    assert origin_commits(made, "beta") == before, "a local-only edit lands no commit"


@pytest.mark.heavy
def test_an_edit_of_a_channel_row_writes_the_file_that_holds_the_row(hub, tmp_path, monkeypatch):
    """#3034 acceptance 6: a channel row the committed file holds writes the committed file, and a guest channel row the
    local file holds writes the local file."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA}, locals={"beta": GUEST_LOCAL})
    committed = _docs(hub, {"project": "beta", "op": {"kind": "field", "channel": "C0BETA0001",
                                                     "field": "language", "value": "pt-BR"}})
    assert _channel(committed["committed"], "C0BETA0001")["language"] == "pt-BR", committed["committed"]
    assert _channel(committed["local"], "C0GUEST001").get("language") is None, "the local row takes no edit"
    local = _docs(hub, {"project": "beta", "op": {"kind": "field", "channel": "C0GUEST001",
                                                  "field": "language", "value": "pt-BR"}})
    assert _channel(local["local"], "C0GUEST001")["language"] == "pt-BR", local["local"]
    assert _channel(local["committed"], "C0BETA0001").get("language") is None, "the committed row takes no edit"


# ---------------------------------------------------------------- two projects keep their own edits

SAME = {"version": 1, "project": {"language": "es-ES", "persona": "nova"},
        "personas": [{"id": "nova", "name": "Nova"}, {"id": "atlas", "name": "Atlas"}],
        "roles": [{"id": "manager", "actions": ["status"]}],
        "workspaces": [{"id": "T0SAME0001", "label": "same-team", "class": "owned", "language": "es-ES",
                        "channels": [{"id": "C0SAME0001", "name": "same-room", "mode": "listen",
                                      "language": "es-ES"}]}]}


@pytest.mark.heavy
def test_two_projects_that_name_the_same_workspace_and_channel_keep_separate_edits(hub, tmp_path, monkeypatch):
    """#3034 acceptance 9: the edit names its project, so a workspace id and a channel id that two projects share keep
    one project's new value and the other project's old value."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": SAME, "gamma": SAME})
    beta = _docs(hub, {"project": "beta", "op": {"kind": "field", "scope": "workspace",
                                                 "workspace": "T0SAME0001", "field": "persona", "value": "atlas"}})
    assert _workspace(beta["committed"], "T0SAME0001")["persona"] == "atlas", beta["committed"]
    gamma = _docs(hub, {"project": "gamma", "op": {"kind": "field", "scope": "workspace",
                                                   "workspace": "T0SAME0001", "field": "persona", "value": "nova"}})
    assert _workspace(gamma["committed"], "T0SAME0001")["persona"] == "nova", gamma["committed"]
    hub.slack_files_post({"project": "beta", "committed": beta["committed"], "local": beta["local"], "confirm": True})
    hub.slack_files_post({"project": "gamma", "committed": gamma["committed"], "local": gamma["local"],
                          "confirm": True})
    assert _workspace(origin_file(made, "beta"), "T0SAME0001")["persona"] == "atlas"
    assert _workspace(origin_file(made, "gamma"), "T0SAME0001")["persona"] == "nova"


# ---------------------------------------------------------------- a channel follows its workspace's new value

@pytest.mark.heavy
def test_the_persona_that_a_channel_inherits_follows_its_workspace_edit(hub, tmp_path, monkeypatch):
    """#3034 acceptance 11: after the workspace's persona changes, the channel's empty Persona choice names the new
    value and the level it comes from, so the page redraws the inherited value it shows."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA})
    out = hub.slack_docs_edit({"project": "beta", "op": {"kind": "field", "scope": "workspace",
                                                         "workspace": "T0BETA0001", "field": "persona",
                                                         "value": "atlas"}})
    assert out.get("err") is None, out
    tree = out["tree"]
    beta = next(n for c in tree["clients"] for n in walk(c) if n.get("kind") == "project" and n["name"] == "beta")
    channel = next(n for n in walk(beta) if n["kind"] == "channel")
    persona = next(c for c in _controls(channel) if c["field"] == "persona")
    assert persona["options"][0]["label"] == "Inherit (Atlas · from workspace)", persona["options"][0]
    assert persona["kind"] == "select", "the redrawn channel keeps its editable control"

# ---------------------------------------------------------------- the page sends each edit to its own project

TIMERS = ('let TQ=[],TN=0;globalThis.setTimeout=(f,ms)=>{const id=++TN;TQ.push({id,f,ms});return id};'
          'globalThis.clearTimeout=id=>{TQ=TQ.filter(t=>t.id!==id)};'
          'const fire=()=>{const q=TQ;TQ=[];for(const t of q)t.f()};')


def _page_slices(page):
    """The page's own slices, with `$` stubbing a store whose nodes record their event handlers, so the view's own
    change listener is the seam the test drives."""
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = ('const STORE={},HS={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",'
            'addEventListener:(t,f)=>{(HS[s+":"+t]=HS[s+":"+t]||[]).push(f)}});')
    return "\n".join([stub, esc, sett, sl])


ALPHA_DOCS = {"committed": {"version": 1, "project": {"language": "en-US", "persona": "hal"}}, "local": {"version": 1}}
BETA_DOCS = {"committed": json.loads(json.dumps(BETA)), "local": {"version": 1}}


def _page_run(hub, tree, script, sel):
    """The page's functions run in node over stubbed timers, a stubbed fetch and the view's own change listener."""
    page = hub.PAGE
    tail = page[page.index("async function slLoad()"):page.index('$("#sbody").addEventListener("change"')]
    change = page[page.index('$("#sbody").addEventListener("change"'):page.index('$("#sbody").addEventListener("click"')]
    state = (f'slDocs=slClone({json.dumps(ALPHA_DOCS)});slSavedDocs=slClone(slDocs);'
             f'slT={json.dumps(tree)};slTsel={json.dumps(sel)};slRender=()=>{{}};'
             'const ticks=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};')
    body = _page_slices(page) + "\n" + tail + "\n" + change + "\n" + TIMERS + state + script
    return json.loads(_node(body))


def _change(dataset, value):
    """The view's change listener, called with the control's data attributes and its new value."""
    return (f'HS["#sbody:change"].forEach(f=>f({{target:{{dataset:{json.dumps(dataset)},value:{json.dumps(value)}}}}}));')


def test_a_workspace_edit_through_the_page_names_its_scope_and_its_project(hub, tmp_path, client_map):
    """#3034 acceptance 1 and 10: the view's own listener reads the row's target from the control and posts the edit
    with that scope and with the project the node belongs to."""
    tree, _, _ = _tree(hub, tmp_path)
    ws = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "workspace")
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(b.op?{committed:{},local:{}}:{})})};'
              '(async()=>{' + _change({"slf": "ws:T0BETA0001|persona", "slproj": "beta"}, "atlas") + 'await ticks();'
              'console.log(JSON.stringify({sent:SENT}))})()')
    got = _page_run(hub, tree, script, ws["id"])
    assert got["sent"] == [{"project": "beta", "op": {"kind": "field", "scope": "workspace",
                                                      "workspace": "T0BETA0001", "field": "persona",
                                                      "value": "atlas"}}], got["sent"]


def test_a_channel_edit_through_the_page_names_the_channel_and_its_project(hub, tmp_path, client_map):
    """#3034 acceptance 2 and 6: the listener posts a channel row's edit by the channel's Slack id and by the project
    the node belongs to."""
    tree, _, _ = _tree(hub, tmp_path)
    channel = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "channel")
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(b.op?{committed:{},local:{}}:{})})};'
              '(async()=>{' + _change({"slf": "C0BETA0001|language", "slproj": "beta"}, "pt-BR") + 'await ticks();'
              'console.log(JSON.stringify({sent:SENT}))})()')
    got = _page_run(hub, tree, script, channel["id"])
    assert got["sent"] == [{"project": "beta", "op": {"kind": "field", "channel": "C0BETA0001",
                                                      "field": "language", "value": "pt-BR"}}], got["sent"]


def test_a_switch_of_project_during_a_workspace_save_writes_the_edits_own_project(hub, tmp_path, client_map):
    """#3034 acceptance 10: after a workspace edit of another project, a switch of the selection before the debounce
    ends still posts the save with that project's own documents, and the page's own project keeps its files."""
    tree, _, _ = _tree(hub, tmp_path)
    ws = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "workspace")
    alpha_id = _project(tree, "alpha")["id"]
    beta_after = json.loads(json.dumps(BETA_DOCS))
    beta_after["committed"]["workspaces"][0]["persona"] = "atlas"
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(b.op'
              '?{committed:' + json.dumps(beta_after["committed"]) + ',local:{version:1}}:{sha:"abc1234"})})};'
              '(async()=>{' + _change({"slf": "ws:T0BETA0001|persona", "slproj": "beta"}, "atlas")
              + f'slTsel={json.dumps(alpha_id)};await slPoSt("beta").chain;await ticks();fire();await ticks();'
              'const save=SENT.find(x=>x.confirm);'
              'console.log(JSON.stringify({save,own:slDocs,beta:slPoSt("beta").docs}))})()')
    got = _page_run(hub, tree, script, ws["id"])
    save = got["save"]
    assert save["project"] == "beta" and save["confirm"] is True, save
    assert save["committed"]["workspaces"][0]["persona"] == "atlas", save
    assert got["own"] == ALPHA_DOCS, "the page's own project keeps its documents"
    assert got["beta"]["committed"]["workspaces"][0]["persona"] == "atlas", got["beta"]


def test_the_tree_follows_the_answer_so_a_channel_inherits_its_workspaces_new_value(hub, tmp_path, client_map):
    """#3034 acceptance 11 at the page: the edit's answer carries the redrawn tree, and the page applies it, so the
    channel's inherited Persona choice shows the workspace's new value without a reload."""
    tree, _, _ = _tree(hub, tmp_path)
    ws = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "workspace")
    redrawn = json.loads(json.dumps(tree))
    redrawn["marker"] = "the answer's tree"
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve({committed:{},local:{},tree:'
              + json.dumps(redrawn) + '})})};'
              '(async()=>{' + _change({"slf": "ws:T0BETA0001|persona", "slproj": "beta"}, "atlas")
              + 'await slPoSt("beta").chain;await ticks();'
              'console.log(JSON.stringify({marker:slT.marker}))})()')
    got = _page_run(hub, tree, script, ws["id"])
    assert got["marker"] == "the answer's tree", got
