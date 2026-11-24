"""GitHub issue #2827: the Communication tree gains a Client level over Project (SPEC 2682).

A client groups the projects of one customer, across profiles. The client map is a local file in the profile folder,
`clients.local.yaml`, so no client name or id ever enters a committed file. A client has a default persona, and the map
records for each project whether the project follows that default. A change of the default shows a "Proposed writes"
table, and the save writes `project.persona` only in the rows that read "writes X".

The seams under test are the hub's own reads, `slack_tree` and `slack_clients_preview`, its save `slack_clients_save`,
and the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import copy
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

# A neutral project that follows the client default and declares the client's persona.
FOLLOWER = {
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
# A neutral project with its own persona: the map records that it does not follow the client default.
OWN = {
    "version": 1,
    "project": {"language": "en-US", "persona": "amber"},
    "personas": [{"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "workspaces": [
        {"id": "T0TEAM0002", "label": "own", "class": "owned",
         "channels": [{"id": "C0OWN00001", "name": "own", "mode": "listen"}]},
    ],
}
# A neutral project that names no Slack file at all.
EMPTY = {}
LOCAL = {"version": 1}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module resolves from this tree."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_clients", HUB_PY)
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
    """Every project the hub knows, its checkout and its profile file, and the client map. `seats` maps a slug to its
    committed document."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, committed in seats.items():
        co, c = seat(tmp_path, slug, committed)
        proto(tmp_path, slug, slug, co)
        made[slug] = (co, c, prof / f"{slug}.slack.local.yaml")
    client_map(prof, clients)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    return prof, made


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def clients_tree(out):
    """The clients of the tree, from `slack_tree` itself or from an overview that carries it."""
    return (out.get("tree") or out)["clients"]


def by_name(out, name):
    for client in clients_tree(out):
        for node in walk(client):
            if node["name"] == name:
                return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for c in clients_tree(out) for n in walk(c)]}")


def tiles(node):
    return {t["key"]: t for t in node.get("now") or []}


# ---------------------------------------------------------------- the client map and the tree

def test_the_tree_shows_a_client_over_a_project_a_workspace_and_a_channel(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub, [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                                                "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    out = hub.slack_tree()
    clients = clients_tree(out)
    assert [c["name"] for c in clients] == ["One"], clients
    client = clients[0]
    assert (client["kind"], client["level"]) == ("client", 1)
    project = by_name(out, "alpha")
    assert (project["kind"], project["level"]) == ("project", 2)
    ws = by_name(out, "team")
    assert (ws["kind"], ws["level"]) == ("workspace", 3)
    ch = by_name(out, "#project")
    assert (ch["kind"], ch["level"]) == ("channel", 4)
    assert ch["children"] == []


def test_a_project_in_no_client_appears_under_one_unassigned_node(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}],
                   {"alpha": FOLLOWER, "beta": OWN})
    out = hub.slack_tree()
    names = [c["name"] for c in clients_tree(out)]
    assert names == ["One", "Unassigned"], names
    unassigned = clients_tree(out)[1]
    assert [p["name"] for p in unassigned["children"]] == ["beta"], unassigned["children"]


def test_the_client_map_lives_in_the_profile_folder_and_never_in_a_checkout(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    assert (prof / "clients.local.yaml").is_file()
    assert not (tmp_path / "checkouts" / "alpha" / "docs" / "squad" / "clients.local.yaml").exists()


def test_the_client_now_tiles_count_projects_and_name_the_default_persona(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                     "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": False}]}],
                   {"alpha": FOLLOWER, "beta": EMPTY})
    client = clients_tree(hub.slack_tree())[0]
    now = tiles(client)
    assert now["Projects"]["value"] == "2" and now["Projects"]["sub"] == "1 with Slack", now["Projects"]
    assert now["Default persona"]["value"] == "Blue" and now["Default persona"]["sub"] == "client map"


# ---------------------------------------------------------------- the proposed writes

def test_a_project_with_its_own_persona_keeps_it_after_a_client_default_change(hub, tmp_path, monkeypatch):
    """A project the map records as not following the default keeps its `project.persona`; its row reads kept."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                        "projects": [{"id": "owners", "follows": False}]}], {"owners": OWN})
    before = made["owners"][1].read_text()
    out = hub.slack_clients_save({"client": "one", "persona": "blue", "confirm": True})
    assert out.get("err") is None, out
    row = next(r for r in out["rows"] if r["project"] == "owners")
    assert row["result"] == "kept: the project set its own persona", row
    assert made["owners"][1].read_text() == before, "the committed file changed"


def test_a_project_that_does_not_declare_the_persona_refuses_the_write(hub, tmp_path, monkeypatch):
    """A follower that does not declare the new persona is refused, and no file changes."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "red", "name": "Red"},
                        "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    before = made["alpha"][1].read_text()
    out = hub.slack_clients_save({"client": "one", "persona": "red", "confirm": True})
    assert out.get("err") is None, out
    row = next(r for r in out["rows"] if r["project"] == "alpha")
    assert row["result"] == "refused: Red is not declared in this project", row
    assert made["alpha"][1].read_text() == before, "the committed file changed"


def test_a_row_that_writes_names_the_new_persona_and_no_other_row_writes(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": [{"id": "alpha", "follows": True}, {"id": "owners", "follows": False},
                                     {"id": "empty", "follows": True}]}],
                      {"alpha": FOLLOWER, "owners": OWN, "empty": EMPTY})
    out = hub.slack_clients_preview({"client": "one", "persona": "amber"})
    rows = {r["project"]: r for r in out["rows"]}
    assert rows["alpha"]["result"] == "writes Amber", rows["alpha"]
    assert rows["owners"]["result"] == "kept: the project set its own persona", rows["owners"]
    assert rows["empty"]["result"] == "nothing to write", rows["empty"]
    assert [w["project"] for w in out["writes"]] == ["alpha"], out["writes"]
    # The proposed committed document changes project.persona alone.
    after = out["writes"][0]["committed"]
    assert after["project"]["persona"] == "amber"
    assert {k: v for k, v in after.items() if k != "project"} == \
           {k: v for k, v in FOLLOWER.items() if k != "project"}


def test_no_client_name_or_id_enters_a_committed_file(hub, tmp_path, monkeypatch):
    """The client map is local, and a save that writes a project.persona carries no client string into the file."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "secret-client", "name": "Secret Client", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    out = hub.slack_clients_preview({"client": "secret-client", "persona": "amber"})
    text = yaml.safe_dump(out["writes"][0]["committed"], sort_keys=False)
    assert "secret-client" not in text and "Secret Client" not in text, text
    assert out["writes"][0]["committed"]["project"]["persona"] == "amber"


def test_a_client_the_map_does_not_name_is_refused(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_clients_preview({"client": "nope", "persona": "blue"})
    assert out["err"] and "client" in out["err"].lower(), out


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


def test_the_renderer_draws_the_client_tree(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    html = render(hub, out)
    assert 'role=tree aria-label="Communication tree"' in html
    assert re.search(r'aria-level="1"[^>]*aria-label="One', html)
    assert re.search(r'aria-level="2"[^>]*aria-label="alpha', html)
    assert re.search(r'aria-level="4"[^>]*aria-label="#project', html)


def test_the_renderer_draws_the_proposed_writes_table(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                     "projects": [{"id": "alpha", "follows": True}, {"id": "owners", "follows": False}]}],
                   {"alpha": FOLLOWER, "owners": OWN})
    out = hub.slack_overview()
    out["tree"]["clients"][0]["proposal"] = hub.slack_clients_preview({"client": "one", "persona": "amber"})["rows"]
    html = render(hub, out, sel="c0")
    assert "Proposed writes" in html, html[:500]
    assert "writes Amber" in html and "kept: the project set its own persona" in html
    assert re.search(r"<th[^>]*>Project", html) and re.search(r"<th[^>]*>Result", html)
