"""GitHub issue #2922: create and delete clients, add and remove their projects (SPEC 2682).

The tree draws clients only: a project no client names waits in every client's Add project list, never under a synthetic
group. The client detail offers "Add project" and "Delete client", and the project detail offers "Remove from client"
behind one confirmation. Each change writes `clients.local.yaml` through the client map module and saves by itself; no
Slack file of a project is written.

The seams under test are the hub's reads `slack_tree` and `slack_overview`, its one write route `slack_clients_post`, and
the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A neutral project that declares the personas a client default may take.
FOLLOWER = {
    "version": 1,
    "project": {"language": "en-US", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                 {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned",
         "channels": [{"id": "C0PROJ0001", "name": "project", "mode": "listen+speak"}]},
    ],
}
OTHER = {
    "version": 1,
    "project": {"language": "en-US", "persona": "amber"},
    "personas": [{"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "workspaces": [{"id": "T0TEAM0002", "label": "own", "class": "owned", "channels": []}],
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
    spec = importlib.util.spec_from_file_location("hub_slack_client_admin", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def seat(tmp_path, name, committed):
    """One fixture project: its checkout holds the committed file, and the profile folder holds the local file."""
    co = tmp_path / "checkouts" / name
    c = co / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    if committed is not None:
        c.write_text(yaml.safe_dump(committed, sort_keys=False))
    return co, c


def proto(tmp_path, slug, project, checkout):
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    (prof / f"{slug}.sh").write_text(
        f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={project}}}"; '
        f': "${{SG_CHECKOUT:={checkout}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
    loc = prof / f"{project}.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    return prof, loc


def client_map(prof, clients):
    (prof / "clients.local.yaml").write_text(yaml.safe_dump({"version": 1, "clients": clients}, sort_keys=False))


def wire(tmp_path, monkeypatch, hub, clients, seats):
    """Every project the hub knows, its checkout and its profile file, and the client map."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, committed in seats.items():
        co, c = seat(tmp_path, slug, committed)
        proto(tmp_path, slug, slug, co)
        made[slug] = (co, c, prof / f"{slug}.slack.local.yaml")
    client_map(prof, clients)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    return prof, made


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def clients_tree(out):
    return (out.get("tree") or out)["clients"]


def by_name(out, name):
    for client in clients_tree(out):
        for node in walk(client):
            if node["name"] == name:
                return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for c in clients_tree(out) for n in walk(c)]}")


def add_list(client):
    return [p["id"] for p in client.get("add_projects") or []]


# ---------------------------------------------------------------- the tree draws clients only

def test_the_tree_draws_no_unassigned_client(hub, tmp_path, monkeypatch):
    """Acceptance 1: a project no client names is drawn nowhere, and it waits in the client's Add project list."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_tree()
    assert [c["name"] for c in clients_tree(out)] == ["One"], clients_tree(out)
    assert [n["name"] for c in clients_tree(out) for n in walk(c)] == ["One", "alpha", "team", "#project"], \
        "the unnamed project is not drawn"
    assert add_list(clients_tree(out)[0]) == ["beta"], "the unnamed project waits in the Add project list"


def test_the_add_project_list_names_only_projects_no_client_names(hub, tmp_path, monkeypatch):
    """Acceptance 3: the list holds the hub's projects no client names, by name, and it reads the same for each client."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "projects": ["alpha"]}, {"id": "two", "name": "Two", "projects": ["beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER, "gamma": FOLLOWER})
    clients = clients_tree(hub.slack_tree())
    assert [c["name"] for c in clients] == ["One", "Two"], clients
    for client in clients:
        assert [(p["id"], p["name"]) for p in client["add_projects"]] == [("gamma", "gamma")], client["add_projects"]


# ---------------------------------------------------------------- add and remove a project

def test_adding_a_project_writes_the_map_and_the_tree_shows_it_as_a_follower(hub, tmp_path, monkeypatch):
    """Acceptance 4 and 8: the picked project joins the client, follows its default persona, and the save is the route's."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                     "projects": ["alpha"]}],
                   {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_clients_post({"op": "add_project", "client": "one", "project": "beta", "confirm": True})
    assert out.get("err") is None and out.get("saved") is True, out
    doc = yaml.safe_load((prof / "clients.local.yaml").read_text())
    assert [p["id"] for p in hub.slack_clients_module().normalize(doc)[0]["projects"]] == ["alpha", "beta"], doc
    tree = hub.slack_tree()
    beta = by_name(tree, "beta")
    assert "inherits from client" in [b["text"] for b in beta["badges"]], beta["badges"]
    assert add_list(clients_tree(tree)[0]) == [], "a named project leaves the Add project list"


def test_removing_a_project_takes_it_from_the_tree_and_keeps_its_slack_files(hub, tmp_path, monkeypatch):
    """Acceptance 5 to 8: the project leaves the tree and its files stay as they are; it returns to the Add list."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
                      {"alpha": FOLLOWER, "beta": OTHER})
    committed, local = made["beta"][1].read_text(), made["beta"][2].read_text()
    out = hub.slack_clients_post({"op": "remove_project", "client": "one", "project": "beta", "confirm": True})
    assert out.get("err") is None and out.get("saved") is True, out
    names = [n["name"] for c in clients_tree(hub.slack_tree()) for n in walk(c)]
    assert "beta" not in names and "alpha" in names, names
    assert add_list(clients_tree(hub.slack_tree())[0]) == ["beta"], "the removed project returns to the Add list"
    assert made["beta"][1].read_text() == committed and made["beta"][2].read_text() == local, "a Slack file changed"


def test_the_remove_question_names_the_project_and_the_client(hub, tmp_path, monkeypatch):
    """Acceptance 6: one question, and it names both the project and the client."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    ask = by_name(hub.slack_tree(), "beta")["ask"]
    assert ask["op"] == "remove_project" and ask["client"] == "one" and ask["project"] == "beta", ask
    assert "beta" in ask["text"] and "One" in ask["text"], ask


def test_the_change_questions_name_the_project_and_the_client(hub, tmp_path, monkeypatch):
    """Acceptance 3, 6, 12 and 14: the hub answers its own question, and every target reads by its display name, never
    by its raw project id. The roster is the hub's; the stub gives one project a label apart from its id."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER, "gamma": FOLLOWER})
    seats, rerr = hub._slack_roster_checked()
    assert rerr is None
    named = [{**s, "label": {"beta": "Beta project", "gamma": "Gamma project"}.get(s["id"], s["label"])}
             for s in seats]
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: (named, None))
    add = hub.slack_clients_post({"op": "add_project", "client": "one", "project": "gamma"})
    assert add.get("saved") is False and "Gamma project" in add["ask"] and "One" in add["ask"], add
    assert "gamma" not in add["ask"], "the add question names the project, never its raw id"
    rem = hub.slack_clients_post({"op": "remove_project", "client": "one", "project": "beta"})
    assert rem.get("saved") is False and "Beta project" in rem["ask"] and "One" in rem["ask"], rem
    assert "beta" not in rem["ask"], "the remove question names the project, never its raw id"
    dele = hub.slack_clients_post({"op": "delete_client", "client": "one"})
    assert "One" in dele["ask"] and "Beta project" in dele["ask"] and "alpha" in dele["ask"], dele


def test_an_add_client_mints_its_id_over_the_map_it_writes(hub, tmp_path, monkeypatch):
    """Acceptance 8: the id comes from the map the write reads, so a name taken between the read and the write takes the
    next free number instead of answering a success for a client the map did not take."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "projects": []},
                    {"id": "two", "name": "Two", "projects": []}],
                   {"alpha": FOLLOWER})
    stale = [{"id": "one", "name": "One", "default_persona": None, "projects": []}]
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (stale, None))
    out = hub.slack_clients_post({"op": "add_client", "name": "Two", "persona": "", "confirm": True})
    assert out.get("err") is None and out.get("saved") is True, out
    assert out["client"] == "two-2", "the id the map took, not the id of the stale read"
    doc = yaml.safe_load((prof / "clients.local.yaml").read_text())
    assert [c["id"] for c in hub.slack_clients_module().normalize(doc)] == ["one", "two", "two-2"], doc


def test_a_post_without_confirm_answers_the_question_and_writes_nothing(hub, tmp_path, monkeypatch):
    """Acceptance 6 and 12: a question comes before the write, so a body without `confirm` changes no file."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
                   {"alpha": FOLLOWER, "beta": OTHER, "gamma": FOLLOWER})
    before = (prof / "clients.local.yaml").read_text()
    for body in ({"op": "add_project", "client": "one", "project": "gamma"},
                 {"op": "remove_project", "client": "one", "project": "beta"},
                 {"op": "add_client", "name": "Two", "persona": "blue"},
                 {"op": "delete_client", "client": "one"}):
        out = hub.slack_clients_post(body)
        assert out.get("err") is None and out.get("saved") is False, (body, out)
    assert (prof / "clients.local.yaml").read_text() == before, "a question wrote a file"
    assert hub.slack_clients_post({"op": "remove_project", "client": "one", "project": "beta"})["ask"].count("beta") == 1
    assert "One" in hub.slack_clients_post({"op": "delete_client", "client": "one"})["ask"]


# ---------------------------------------------------------------- add and delete a client

def test_adding_a_client_shows_it_in_the_tree_with_no_project(hub, tmp_path, monkeypatch):
    """Acceptance 9 and 10: a name and a default persona make a client, and the tree draws it with no project."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
                   {"alpha": FOLLOWER})
    out = hub.slack_clients_post({"op": "add_client", "name": "Two", "persona": "amber",
                                  "persona_name": "Amber", "confirm": True})
    assert out.get("err") is None and out.get("saved") is True, out
    tree = hub.slack_tree()
    assert [c["name"] for c in clients_tree(tree)] == ["One", "Two"], clients_tree(tree)
    two = clients_tree(tree)[1]
    assert two["children"] == [] and two["state"] == "empty", two
    assert two["default_persona"] == "amber", two
    assert add_list(two) == [], "the only project is one the map already names"


def test_deleting_a_client_returns_its_projects_to_the_add_project_list(hub, tmp_path, monkeypatch):
    """Acceptance 11 to 13: the client leaves the map, its projects leave the tree and return to the Add list, and
    their Slack files stay as they are."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]},
                       {"id": "two", "name": "Two", "default_persona": "amber", "projects": ["beta"]}],
                      {"alpha": FOLLOWER, "beta": OTHER})
    before = {k: (made[k][1].read_text(), made[k][2].read_text()) for k in ("alpha", "beta")}
    out = hub.slack_clients_post({"op": "delete_client", "client": "one", "confirm": True})
    assert out.get("err") is None and out.get("saved") is True, out
    doc = yaml.safe_load((prof / "clients.local.yaml").read_text())
    assert [c["id"] for c in hub.slack_clients_module().normalize(doc)] == ["two"], doc
    tree = hub.slack_tree()
    assert [c["name"] for c in clients_tree(tree)] == ["Two"], clients_tree(tree)
    assert "alpha" not in [n["name"] for c in clients_tree(tree) for n in walk(c)], "the project left the tree"
    assert add_list(clients_tree(tree)[0]) == ["alpha"], "the project returns to the Add list"
    for k, (committed, local) in before.items():
        assert (made[k][1].read_text(), made[k][2].read_text()) == (committed, local), f"a Slack file of {k} changed"


def test_the_delete_question_names_the_client_and_the_projects_it_holds(hub, tmp_path, monkeypatch):
    """Acceptance 12: one question, and it names the client and each project it holds."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]},
          {"id": "two", "name": "Two", "projects": []}],
         {"alpha": FOLLOWER, "beta": OTHER})
    ask = clients_tree(hub.slack_tree())[0]["ask"]
    assert ask["op"] == "delete_client" and ask["client"] == "one", ask
    assert "One" in ask["text"] and "alpha" in ask["text"] and "beta" in ask["text"], ask


# ---------------------------------------------------------------- a write the map refuses

def test_a_project_or_client_the_map_does_not_hold_is_refused(hub, tmp_path, monkeypatch):
    """The route refuses a target the map or the hub does not hold, so a typo writes nothing."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
                   {"alpha": FOLLOWER, "beta": OTHER})
    before = (prof / "clients.local.yaml").read_text()
    for body in ({"op": "add_project", "client": "nope", "project": "beta", "confirm": True},
                 {"op": "add_project", "client": "one", "project": "ghost", "confirm": True},
                 {"op": "add_project", "client": "one", "project": "alpha", "confirm": True},
                 {"op": "remove_project", "client": "one", "project": "beta", "confirm": True},
                 {"op": "add_client", "name": " ", "confirm": True},
                 {"op": "add_client", "name": "Two", "persona": "red", "confirm": True},
                 {"op": "delete_client", "client": "nope", "confirm": True}):
        out = hub.slack_clients_post(body)
        assert out.get("err"), (body, out)
    assert (prof / "clients.local.yaml").read_text() == before, "a refused write changed the map"


# ---------------------------------------------------------------- the renderer

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, sett, sl])


def render(hub, out, sel=None):
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};" + (f"slTsel={json.dumps(sel)};" if sel else "") + "slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def _fetched(hub, out, sel, script):
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel={json.dumps(sel)};\n" + script)
    return json.loads(_node(body))


def test_the_client_pane_shows_an_add_project_control_and_a_delete_client_button(hub, tmp_path, monkeypatch):
    """Acceptance 2, 3, 9 and 11: the client detail draws both controls, and the Add list names the free project."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    html = render(hub, hub.slack_overview(), sel="c0")
    assert "Add project" in html, html
    assert re.search(r'<option value="beta"[^>]*>beta</option>', html), html
    assert "Delete client" in html, html


def test_the_tree_shows_an_add_client_control_with_a_name_and_a_default_persona(hub, tmp_path, monkeypatch):
    """Acceptance 9: the tree draws a name field and a default-persona select, and the personas are the hub's."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER})
    html = render(hub, hub.slack_overview())
    assert "Add client" in html, html
    assert re.search(r'aria-label="Client name"', html), html
    assert re.search(r'aria-label="Default persona"[^>]*>.*"blue".*"amber"', html, re.S), html


def test_the_project_pane_shows_remove_from_client_and_the_one_question(hub, tmp_path, monkeypatch):
    """Acceptance 5 and 6: the project detail offers the button, and the click draws the hub's question; a confirm
    posts the confirmed change, and nothing was written before it."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_overview()
    assert "Remove from client" in render(hub, out, sel="c0.p1"), "the project pane offers the button"
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{BODIES.push(JSON.parse(o.body));'
              'return Promise.resolve({status:200,json:()=>Promise.resolve({saved:true})})};'
              '(async()=>{slRender();const button="Remove from client";slMapAsk("c0.p1");'
              'const h=STORE["#sbody"].innerHTML;slMapConfirm();await new Promise(r=>setTimeout(r,20));'
              "console.log(JSON.stringify({html:h,bodies:BODIES}))})()")
    got = _fetched(hub, out, "c0.p1", script)
    assert "beta" in got["html"] and "One" in got["html"], got["html"][-800:]
    assert "Confirm" in got["html"], got["html"][-800:]
    assert len(got["bodies"]) == 1, "the question posts nothing; only the confirm writes"
    assert got["bodies"][0]["op"] == "remove_project", got["bodies"]
    assert got["bodies"][0]["confirm"] is True and got["bodies"][0]["project"] == "beta", got["bodies"]


def test_the_add_client_control_asks_the_hub_before_it_writes(hub, tmp_path, monkeypatch):
    """Acceptance 9: the click posts the typed name and the default persona without `confirm`, the hub's question is
    drawn, and the confirm posts the same body with the flag."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER})
    out = hub.slack_overview()
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'return Promise.resolve({status:200,json:()=>Promise.resolve('
              'b.confirm===true?{saved:true}:{saved:false,ask:"Add the client Two?"})})};'
              'const EL={slcname:{value:"Two"},slcpersona:{value:"amber",options:[{text:"Amber"}],selectedIndex:0}};'
              'globalThis.document={getElementById:id=>EL[id]||null};'
              '(async()=>{slRender();slMapAddClient();await new Promise(r=>setTimeout(r,20));'
              'const h=STORE["#sbody"].innerHTML;slMapConfirm();await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({html:h,bodies:BODIES}))})()')
    got = _fetched(hub, out, "c0", script)
    assert [b["op"] for b in got["bodies"]] == ["add_client", "add_client"], got["bodies"]
    first, second = got["bodies"]
    assert first.get("confirm") is not True, "the first click writes nothing before the question"
    assert first["name"] == "Two" and first["persona"] == "amber" and first["persona_name"] == "Amber", first
    assert "Add the client Two?" in got["html"], got["html"][-800:]
    assert second["confirm"] is True and second["name"] == "Two", second


def test_the_add_project_control_asks_the_hub_before_it_writes(hub, tmp_path, monkeypatch):
    """Acceptance 2, 3 and 8: the click posts the picked project without `confirm`, the hub's question is drawn, and the
    confirm posts the same body with the flag."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_overview()
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'return Promise.resolve({status:200,json:()=>Promise.resolve('
              'b.confirm===true?{saved:true}:{saved:false,ask:"Add beta to One?"})})};'
              'const EL={slclproject:{value:"beta"}};globalThis.document={getElementById:id=>EL[id]||null};'
              '(async()=>{slRender();slMapAddProject("c0");await new Promise(r=>setTimeout(r,20));'
              'const h=STORE["#sbody"].innerHTML;slMapConfirm();await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({html:h,bodies:BODIES}))})()')
    got = _fetched(hub, out, "c0", script)
    assert [b["op"] for b in got["bodies"]] == ["add_project", "add_project"], got["bodies"]
    first, second = got["bodies"]
    assert first.get("confirm") is not True, "the first click writes nothing before the question"
    assert first["client"] == "one" and first["project"] == "beta", first
    assert "Add beta to One?" in got["html"], got["html"][-800:]
    assert second["confirm"] is True and second["project"] == "beta", second


def test_a_new_client_lists_the_hub_personas_as_its_default(hub, tmp_path, monkeypatch):
    """Acceptance 10: a client with no project still draws the hub's default-persona select, so its own default can change.
    The options used to come from its projects alone, which left the select empty for a client just created (#2922)."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "two", "name": "Two", "default_persona": "amber", "projects": []}],
         {"alpha": FOLLOWER})
    html = render(hub, hub.slack_overview(), sel="c0")
    sel = re.search(r"<select id=slclpersona[^>]*>.*?</select>", html, re.S).group(0)
    assert 'value="blue"' in sel and 'value="amber" selected' in sel, sel


def test_an_empty_tree_keeps_the_add_client_control_in_view(hub, tmp_path, monkeypatch):
    """Acceptance 9 and 13: a map with no client leaves the tree column drawn, so Add client stays reachable after the
    last delete. A folded panel used to hide the column and its control behind a detail it cannot reopen (#2922)."""
    wire(tmp_path, monkeypatch, hub, [], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    script = ('slS={};slR=' + json.dumps(out) + ';slTshow=true;slRender();'
              'console.log(JSON.stringify(STORE["#sbody"].innerHTML))')
    html = json.loads(_node(_slices(hub.PAGE) + "\n" + script))
    top = html[:html.index("sltdetail")]
    assert 'class="slttop sltshow"' not in top, top[-400:]
    assert 'aria-label="Client name"' in top, top[-400:]


# ---------------------------------------------------------------- one question belongs to one pane

def test_an_add_client_question_reaches_the_empty_tree_pane(hub, tmp_path, monkeypatch):
    """Acceptance 9 and 10: a map with no client has no node to carry the hub's question, so the pane still draws it and
    its Confirm. Without it the first client could never be added from the UI (#2922)."""
    wire(tmp_path, monkeypatch, hub, [], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    assert not clients_tree(out), "the fixture map names no client"
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'return Promise.resolve({status:200,json:()=>Promise.resolve('
              'b.confirm===true?{saved:true}:{saved:false,ask:"Add the client Two?"})})};'
              'const EL={slcname:{value:"Two"},slcpersona:{value:"amber",options:[{text:"Amber"}],selectedIndex:0}};'
              'globalThis.document={getElementById:id=>EL[id]||null};'
              '(async()=>{slRender();slMapAddClient();await new Promise(r=>setTimeout(r,20));'
              'const h=STORE["#sbody"].innerHTML;slMapConfirm();await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({html:h,bodies:BODIES}))})()')
    got = _fetched(hub, out, "", script)
    assert "Add the client Two?" in got["html"], got["html"][-800:]
    assert "Confirm" in got["html"], "the empty pane draws the question's Confirm button"
    assert [b["op"] for b in got["bodies"]] == ["add_client", "add_client"], got["bodies"]
    assert got["bodies"][1]["confirm"] is True, got["bodies"]


def test_a_click_while_a_save_is_in_flight_is_ignored(hub, tmp_path, monkeypatch):
    """Acceptance 6, 8 and 12: a second click before the first answer returns is dropped, so its request never overwrites
    the pending question and every issued change keeps its own confirmation (#2922)."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_overview()
    script = ('const BODIES=[];let resolveFirst;'
              'globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'if(BODIES.length===1)return new Promise(r=>{resolveFirst=r});'
              'return Promise.resolve({status:200,json:()=>Promise.resolve({saved:false,ask:"Add the client Two?"})})};'
              'const EL={slclproject:{value:"beta"},slcname:{value:"Two"},'
              'slcpersona:{value:"amber",options:[{text:"Amber"}],selectedIndex:0}};'
              'globalThis.document={getElementById:id=>EL[id]||null};'
              '(async()=>{slRender();slMapAddProject("c0");slMapAddClient();'
              'resolveFirst({status:200,json:()=>Promise.resolve({saved:false,ask:"Add beta to One?"})});'
              'await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({bodies:BODIES,html:STORE["#sbody"].innerHTML}))})()')
    got = _fetched(hub, out, "c0", script)
    assert [b["op"] for b in got["bodies"]] == ["add_project"], "a click during the save is ignored"
    assert "Add beta to One?" in got["html"], got["html"][-800:]
    assert "Add the client Two?" not in got["html"], "the dropped click's question is never drawn"


def test_a_route_question_stays_on_the_node_it_was_asked_from(hub, tmp_path, monkeypatch):
    """Acceptance 2 and 6: the Add project question belongs to the client pane the control sits in, never to whatever node
    the selection names, so a stale selection cannot draw it on another pane (#2922)."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_overview()
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'return Promise.resolve({status:200,json:()=>Promise.resolve('
              'b.confirm===true?{saved:true}:{saved:false,ask:"Add gamma to One?"})})};'
              'const EL={slclproject:{value:"gamma"}};globalThis.document={getElementById:id=>EL[id]||null};'
              '(async()=>{slRender();slTsel="c0.p1";slMapAddProject("c0");await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({html:STORE["#sbody"].innerHTML,bodies:BODIES,sel:slTsel}))})()')
    got = _fetched(hub, out, "c0", script)
    assert "Add gamma to One?" in got["html"], "the question shows on the client pane it was asked from"
    assert got["sel"] == "c0", "the pane moves from the stale selection back to the client the control sits in"
    assert got["bodies"][0]["client"] == "one", got["bodies"]


def test_the_remove_route_asks_its_question_before_it_writes(hub, tmp_path, monkeypatch):
    """Acceptance 5 to 8: a body without `confirm` gets the hub's question, and the confirm posts the same body with the
    flag. The test drives the route's own question, never only the node's, so a forced flag on the first click is red
    (#2922)."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    out = hub.slack_overview()
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);BODIES.push(b);'
              'return Promise.resolve({status:200,json:()=>Promise.resolve('
              'b.confirm===true?{saved:true}:{saved:false,ask:"Remove beta from One?"})})};'
              '(async()=>{slRender();slMapPost({op:"remove_project",client:"one",project:"beta"});'
              'await new Promise(r=>setTimeout(r,20));const h=STORE["#sbody"].innerHTML;'
              'slMapConfirm();await new Promise(r=>setTimeout(r,20));'
              'console.log(JSON.stringify({html:h,bodies:BODIES}))})()')
    got = _fetched(hub, out, "c0.p1", script)
    assert [b["op"] for b in got["bodies"]] == ["remove_project", "remove_project"], got["bodies"]
    first, second = got["bodies"]
    assert first.get("confirm") is not True, "the first click writes nothing before the question"
    assert first["client"] == "one" and first["project"] == "beta", first
    assert "Remove beta from One?" in got["html"], got["html"][-800:]
    assert second["confirm"] is True and second["project"] == "beta", second


# ---------------------------------------------------------------- the hub's own node spaces

def test_the_failed_tree_node_sits_outside_the_client_and_project_id_spaces(hub):
    """A read that fails draws one error node with an id no client or project uses, so it never collides with a real
    project's selection or breadcrumb (#2922)."""
    failed = hub._slack_tree_failed("the rules module did not resolve")
    assert failed["root"]["id"] == "error", failed["root"]
    assert [n["id"] for n in failed["clients"]] == ["error"], failed["clients"]
    assert failed["clients"][0]["kind"] == "project", failed["clients"][0]


def test_a_question_that_names_projects_needs_the_roster(hub, tmp_path, monkeypatch):
    """Acceptance 12 and 14: the hub names each project the client holds, so a roster that cannot resolve refuses the
    question instead of falling back to raw ids; no policy names a project two ways (#2922)."""
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
         {"alpha": FOLLOWER, "beta": OTHER})
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: ([], "the profile read timed out after 20 s"))
    out = hub.slack_clients_post({"op": "delete_client", "client": "one"})
    assert out.get("err") == "the profile read timed out after 20 s", out
    assert not out.get("ask"), "a roster that cannot resolve draws no question with raw ids"
    rem = hub.slack_clients_post({"op": "remove_project", "client": "one", "project": "beta"})
    assert rem.get("err") == "the profile read timed out after 20 s", rem
    assert not rem.get("ask"), rem
