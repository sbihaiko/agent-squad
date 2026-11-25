"""GitHub issue #2827: the Communication tree gains a Client level over Project (SPEC 2682).

A client groups the projects of one customer, across profiles. The client map is a local file in the profile folder,
`clients.local.yaml`, so no client name or id ever enters a committed file. A client has a default persona, and the map
records for each project whether the project follows that default. A change of the default shows a "Proposed writes"
table, and the save writes `project.persona` only in the rows that read "writes X".

The seams under test are the hub's own reads, `slack_tree` and `slack_clients_preview`, its save `slack_clients_save`,
and the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
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
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))   # the hub pin, so no ambient checkout leaks
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
    assert out["counts"]["projects"] == 1, out["counts"]   # the roster holds one project, never the ambient checkout
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
    """A project the map records as not following the default keeps its `project.persona`; its row reads kept. The
    follow flag is pinned both ways: the explicit false keeps, and a project with the flag omitted follows."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": [{"id": "owners", "follows": False}, "alpha"]}],
                      {"owners": OWN, "alpha": FOLLOWER})
    before = made["owners"][1].read_text()
    rows = {r["project"]: r["result"] for r in hub.slack_clients_preview({"client": "one", "persona": "amber"})["rows"]}
    assert rows["owners"] == "kept: the project set its own persona", rows
    assert rows["alpha"] == "writes Amber", rows   # the omitted flag on the shorthand follows the default
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


# ---------------------------------------------------------------- the review's findings (#2827)

def stub_write(hub, monkeypatch):
    """A stub of the project save path: it writes the committed document to its own file and answers a sha. The route's
    answer is the seam here, so no test of it needs a git repository."""
    def fake(body, committed=None, local=None, loop=None, name=None):
        pathlib.Path(committed).write_text(yaml.safe_dump(body["committed"], sort_keys=False))
        return {"sha": "stub"}
    monkeypatch.setattr(hub, "slack_files_set", fake)


def test_a_confirmed_save_answers_saved_and_a_refused_row_makes_it_partial(hub, tmp_path, monkeypatch):
    """A save that writes every planned row answers `proposal_saved`; a save with one refused row is partial and keeps
    the refusal text, so the page never shows a green saved state over a row that wrote nothing."""
    blue_only = {"version": 1, "project": {"language": "en-US", "persona": "blue"},
                 "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."}], "workspaces": []}
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": [{"id": "alpha", "follows": True}, {"id": "gamma", "follows": True}]}],
                      {"alpha": FOLLOWER, "gamma": blue_only})
    stub_write(hub, monkeypatch)
    preview = hub.slack_clients_preview({"client": "one", "persona": "amber"})
    assert preview["proposal_saved"] is False, preview
    assert preview["partial"] is True and [r["project"] for r in preview["refused"]] == ["gamma"], preview
    saved = hub.slack_clients_save({"client": "one", "persona": "amber", "confirm": True})
    assert saved.get("err") is None, saved
    assert [s["project"] for s in saved["saved"]] == ["alpha"], saved
    assert saved["proposal_saved"] is False, saved
    assert saved["partial"] is True, saved
    refused = {r["project"]: r["result"] for r in saved["refused"]}
    assert refused == {"gamma": "refused: Amber is not declared in this project"}, saved.get("refused")


def test_a_confirmed_save_with_no_refused_row_answers_proposal_saved(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    stub_write(hub, monkeypatch)
    out = hub.slack_clients_save({"client": "one", "persona": "amber", "confirm": True})
    assert out.get("err") is None, out
    assert out["proposal_saved"] is True and out["partial"] is False, out
    assert out["refused"] == [], out.get("refused")


def test_a_shorthand_project_follows_the_default_and_the_tree_shows_the_flag(hub, tmp_path, monkeypatch):
    """A project written as a plain string follows the client default: its row writes, and both follow states show a
    badge in the settings pane, so a hand-edited map never follows silently."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                     "projects": ["alpha", {"id": "owners", "follows": False}]}],
                   {"alpha": FOLLOWER, "owners": OWN})
    out = hub.slack_clients_preview({"client": "one", "persona": "amber"})
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows == {"alpha": "writes Amber", "owners": "kept: the project set its own persona"}, rows
    tree = hub.slack_tree()
    alpha = by_name(tree, "alpha")
    owners = by_name(tree, "owners")
    assert "follows default" in [b["text"] for b in alpha["badges"]], alpha["badges"]
    assert "own persona" in [b["text"] for b in owners["badges"]], owners["badges"]


def test_a_burst_of_previews_shares_one_roster_read(hub, tmp_path, monkeypatch):
    """The preview route caches the roster for a short window, so a change of the default never fans out one profile
    read per request; a save reads the roster fresh."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                     "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    calls = []
    orig = hub._profiles_read

    def counted():
        calls.append(1)
        return orig()

    monkeypatch.setattr(hub, "_profiles_read", counted)
    stub_write(hub, monkeypatch)
    for _ in range(3):
        hub.slack_clients_preview({"client": "one", "persona": "amber"})
    assert len(calls) == 1, calls
    hub.slack_clients_save({"client": "one", "persona": "amber", "confirm": True})
    assert len(calls) == 2, calls


def test_a_guest_slot_line_and_a_second_guest_banner_sit_beside_their_project(hub, tmp_path, monkeypatch):
    """A tree over two projects banners the second guest beside the project that carries it, never across the whole
    tree, and the guest-slot line is not built for a scope of more than one project."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": "blue",
                        "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": True}]}],
                      {"alpha": FOLLOWER, "beta": OWN})
    guests = {"version": 1, "workspaces": [
        {"id": "T0GUEST001", "label": "g1", "class": "guest", "channels": []},
        {"id": "T0GUEST002", "label": "g2", "class": "guest", "channels": []}]}
    made["beta"][2].write_text(yaml.safe_dump(guests, sort_keys=False))
    out = hub.slack_tree()
    assert out["notice"] is None, out.get("notice")
    assert out["guest_slot"] is None, out.get("guest_slot")
    beta = by_name(out, "beta")
    assert beta["notice"] and beta["notice"]["title"] == "2 guest workspaces", beta.get("notice")
    assert "a second guest workspace is refused" in beta["notice"]["text"]
    assert "2 guest workspaces" in [b["text"] for b in beta["badges"]], beta["badges"]
    assert not by_name(out, "alpha").get("notice"), "a project with no second guest carries no banner"


def _fetched(hub, out, sel, script):
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel={json.dumps(sel)};\n" + script)
    return json.loads(_node(body))


def test_the_bootstrap_proposes_once_and_a_missing_route_reads_not_available(hub, tmp_path, monkeypatch):
    """The page proposes a client's writes once; a hub without the route answers 404, the pane reads 'not available',
    and no later render asks again."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    script = ('let CALLS=0;globalThis.fetch=()=>{CALLS++;return Promise.resolve({status:404,json:()=>Promise.resolve({})})};'
              '(async()=>{slRender();await new Promise(r=>setTimeout(r,20));slRender();await new Promise(r=>setTimeout(r,20));'
              "console.log(JSON.stringify({calls:CALLS,html:STORE['#sbody'].innerHTML}))})()")
    got = _fetched(hub, out, "c0", script)
    assert got["calls"] == 1, got["calls"]
    assert "not available" in got["html"], got["html"][:600]


def test_the_page_shows_saved_after_a_full_save_and_partly_saved_after_a_partial_one(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    rows = [{"project": "alpha", "label": "alpha", "now": "Blue", "action": "write", "result": "writes Amber"}]
    script = (f"const ANSWER={json.dumps({'rows': rows, 'proposal_saved': True, 'partial': False, 'refused': []})};"
              'globalThis.fetch=()=>Promise.resolve({status:200,json:()=>Promise.resolve(ANSWER)});'
              '(async()=>{slRender();await new Promise(r=>setTimeout(r,20));slPropose("c0",true);'
              "await new Promise(r=>setTimeout(r,20));console.log(JSON.stringify(STORE['#sbody'].innerHTML))})()")
    html = _fetched(hub, out, "c0", script)
    assert re.search(r'sltb ok">saved', html), html[-600:]
    refused = {"rows": rows + [{"project": "gamma", "label": "gamma", "now": "none", "action": "refused",
                                "result": "refused: Amber is not declared in this project"}],
               "proposal_saved": False, "partial": True,
               "refused": [{"project": "gamma", "result": "refused: Amber is not declared in this project"}]}
    script = (f"const ANSWER={json.dumps(refused)};"
              'globalThis.fetch=()=>Promise.resolve({status:200,json:()=>Promise.resolve(ANSWER)});'
              '(async()=>{slRender();await new Promise(r=>setTimeout(r,20));slPropose("c0",true);'
              "await new Promise(r=>setTimeout(r,20));console.log(JSON.stringify(STORE['#sbody'].innerHTML))})()")
    html = _fetched(hub, out, "c0", script)
    assert "partly saved" in html and not re.search(r'sltb ok">saved', html), html[-600:]


# ---------------------------------------------------------------- the second review's findings (#2827)

def test_a_fetch_failure_reads_not_available_and_disables_the_save(hub, tmp_path, monkeypatch):
    """A preview that fails to reach the hub leaves no stale table: the pane reads 'not available', the error is shown,
    and the Save button is disabled until a preview succeeds."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    script = ('globalThis.fetch=()=>Promise.reject(new Error("down"));'
              '(async()=>{slRender();await new Promise(r=>setTimeout(r,20));slRender();'
              "await new Promise(r=>setTimeout(r,20));console.log(JSON.stringify(STORE['#sbody'].innerHTML))})()")
    html = _fetched(hub, out, "c0", script)
    assert "not available" in html, html[:800]
    assert re.search(r'<button[^>]*data-slt-write="c0"[^>]*disabled', html), html[:800]
    assert "could not be read" in html, html[:800]


def test_a_preview_posts_the_personas_display_name(hub, tmp_path, monkeypatch):
    """The POST names the persona id and the label the pane shows, so a non-default persona never renders as its raw
    id."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                     "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    script = ('const BODIES=[];globalThis.fetch=(u,o)=>{BODIES.push(JSON.parse(o.body));'
              'return Promise.resolve({status:200,json:()=>Promise.resolve({rows:[]})})};'
              '(async()=>{slRender();await new Promise(r=>setTimeout(r,20));'
              "console.log(JSON.stringify(BODIES))})()")
    bodies = _fetched(hub, out, "c0", script)
    assert bodies and bodies[0].get("persona") == "blue", bodies
    assert bodies[0].get("persona_name") == "Blue", bodies


def test_a_client_save_under_a_worktree_named_by_the_environment_lands_each_project_from_its_own_tree(
        hub, tmp_path, monkeypatch):
    """`SG_SLACK_WORKTREE` names the base, and each project of a client save gets its own worktree under it, so two
    projects of one client never share one tree and never interleave their commits."""
    prof = tmp_path / "profiles"
    prof.mkdir()
    second = {"version": 1, "project": {"language": "en-US", "persona": "blue"},
              "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                           {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
              "workspaces": [{"id": "T0TEAM0009", "label": "team-b", "class": "owned", "channels": []}]}
    repos = {}
    for slug, doc in (("alpha", FOLLOWER), ("beta", second)):
        co, origin = git_repo(tmp_path, slug, doc)
        proto(tmp_path, slug, slug, co)
        repos[slug] = (co, origin)
        (prof / f"{slug}.slack.local.yaml").write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    client_map(prof, [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                       "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": True}]}])
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "hubs"))
    monkeypatch.setattr(hub, "STATE", state)
    out = hub.slack_clients_save({"client": "one", "persona": "amber", "confirm": True})
    assert out.get("err") is None, out
    assert [s["project"] for s in out["saved"]] == ["alpha", "beta"], out.get("saved")
    for slug, (_co, origin) in repos.items():
        landed = tmp_path / "hubs" / slug / "docs" / "squad" / "slack.yaml"
        assert landed.is_file(), landed
        assert yaml.safe_load(landed.read_text())["project"]["persona"] == "amber"
        assert subprocess.run(["git", "-C", str(origin), "show", "dev:docs/squad/slack.yaml"],
                              capture_output=True, text=True, check=True, timeout=60).stdout.find("persona: amber") >= 0


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


def test_a_roster_read_that_does_not_finish_fails_the_save_loudly(hub, tmp_path, monkeypatch):
    """A profile read that times out is not the fresh machine's empty roster: the preview and the save both answer its
    error, so no save reports success over a roster it never read."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": {"id": "amber", "name": "Amber"},
                        "projects": ["alpha"]}], {"alpha": FOLLOWER})
    real = subprocess.run

    def slow(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "bash":
            raise subprocess.TimeoutExpired(cmd, 20)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(hub.subprocess, "run", slow)
    preview = hub.slack_clients_preview({"client": "one", "persona": "amber"})
    assert preview.get("err") and "profile read" in preview["err"], preview
    saved = hub.slack_clients_save({"client": "one", "persona": "amber", "confirm": True})
    assert saved.get("err") and "profile read" in saved["err"], saved
    assert made["alpha"][1].read_text() == yaml.safe_dump(FOLLOWER, sort_keys=False), "a failed read wrote a file"


def test_the_detail_pane_says_why_when_the_tree_has_no_root(hub, tmp_path, monkeypatch):
    """A tree of no client and no project is not a blank pane: the detail pane names the empty map."""
    prof, _ = wire(tmp_path, monkeypatch, hub, [], {})
    out = hub.slack_overview()
    assert clients_tree(out) == [], clients_tree(out)
    html = render(hub, out)
    assert "No project in the client map yet" in html, html[:800]


def test_a_project_the_client_names_but_the_hub_does_not_know_is_named_under_the_tree(hub, tmp_path, monkeypatch):
    """A project the map names and no profile holds is listed under the tree by its id, so a misspelled project id never
    leaves the tree silently short."""
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "ghost"]}],
                   {"alpha": FOLLOWER})
    out = hub.slack_overview()
    tree = out["tree"]
    assert tree["unknown_projects"] == ["ghost"], tree.get("unknown_projects")
    html = render(hub, out)
    assert re.search(r"(?i)not on this hub", html) and "ghost" in html, html[-800:]


def test_the_tree_reads_the_local_file_named_for_the_checkout_and_not_the_board_number(hub, tmp_path, monkeypatch):
    """#2899: a profile's SG_PROJECT holds the board number. The local file is named for the checkout folder, as
    slackpolicy.check and the guest runner name it, so a workspace the local file alone holds shows in the tree."""
    prof = tmp_path / "profiles"
    prof.mkdir()
    co, _ = seat(tmp_path, "alpha", FOLLOWER)
    (prof / "alpha.sh").write_text(
        f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; '
        f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
    local = {"version": 1, "workspaces": [{"id": "T0GUEST001", "label": "visit", "channels": [
        {"id": "C0GUEST001", "name": "visit-room", "class": "guest", "mode": "listen", "persona": "blue"}]}]}
    (prof / "alpha.slack.local.yaml").write_text(yaml.safe_dump(local, sort_keys=False))
    client_map(prof, [])
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    out = hub.slack_tree()
    assert by_name(out, "visit")["kind"] == "workspace"
    assert not (prof / "3.slack.local.yaml").exists()
