"""GitHub issue #2901: the project and workspace Language selects stage their edit instead of reverting.

The Communication tree draws a project node and a workspace node, each with a Persona and a Language select. A select of
a channel node stages its edit and keeps the value after the page redraws. The same select above a channel did not: the
control carried no target, so the change event staged nothing, and the next redraw put the file's value back.

The seams under test are the hub's own editor, `slack_docs_edit`, the hub's read, `slack_overview`, whose `tree` key is
the tree, and the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
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

COMMITTED = {
    "version": 1,
    "project": {"language": "pt-BR", "persona": "hal"},
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
         "channels": [{"id": "C0PROJ0001", "name": "proj", "mode": "listen+speak", "roles": ["manager"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, the same pin the tree suites use, so the policy module resolves from
    this tree and never from a loop checkout the caller exported."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_default_rows", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def paths(tmp_path):
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    return c, loc


def docs():
    return {"committed": copy.deepcopy(COMMITTED), "local": copy.deepcopy(LOCAL)}


def read(hub, tmp_path):
    c, loc = paths(tmp_path)
    return hub.slack_overview(c, loc)


def edit(hub, tmp_path, op):
    c, loc = paths(tmp_path)
    return hub.slack_docs_edit({**docs(), "op": op}, committed=c, local=loc)


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_id(root, node_id):
    for node in walk(root):
        if node["id"] == node_id:
            return node
    raise AssertionError(f"the tree holds no node {node_id!r}: {[n['id'] for n in walk(root)]}")


def row(node, key):
    return next(r for r in node["settings"]["rows"] if r["key"] == key)


# ---------------------------------------------------------------- the hub hands the view a target

def test_the_project_rows_name_the_project_as_their_target(hub, tmp_path):
    """The project's Language and Persona controls name the project as the row they write, so the view can hand the
    intent back to the hub without deciding the file (ADR-0137)."""
    node = by_id(read(hub, tmp_path)["tree"]["root"], "c0.p0")
    for key in ("language", "persona"):
        assert row(node, key)["controls"][0].get("scope") == "project", row(node, key)


def test_the_workspace_rows_name_the_workspace_as_their_target(hub, tmp_path):
    """A workspace's Language control names that workspace by its team id, never its place in the list."""
    node = by_id(read(hub, tmp_path)["tree"]["root"], "c0.p0.w0")
    assert row(node, "language")["controls"][0].get("scope") == "ws:T0TEAM0001", row(node, "language")


# ---------------------------------------------------------------- the hub stages the project edit

def test_a_project_language_edit_writes_the_committed_project_key(hub, tmp_path):
    """Criterion 1 of the hub: the project's language stages into `project.language` of the committed file, so the save
    shows that file's diff, and the staged tree answers the new value."""
    out = edit(hub, tmp_path, {"kind": "field", "scope": "project", "field": "language", "value": "en-US"})
    assert out["err"] is None, out
    assert out["committed"]["project"]["language"] == "en-US", out["committed"].get("project")
    assert by_id(out["tree"]["root"], "c0.p0")["settings"]["rows"][1]["controls"][0]["value"] == "en-US", \
        by_id(out["tree"]["root"], "c0.p0")["settings"]["rows"]


def test_a_cleared_project_language_leaves_the_key_out(hub, tmp_path):
    """The empty choice inherits, so it removes the key instead of writing an empty string."""
    out = edit(hub, tmp_path, {"kind": "field", "scope": "project", "field": "language", "value": ""})
    assert out["err"] is None, out
    assert "language" not in (out["committed"].get("project") or {}), out["committed"].get("project")


def test_a_workspace_language_edit_writes_the_file_that_holds_the_row(hub, tmp_path):
    """The own workspace row lives in the committed file and the guest row in the local one, so the hub picks the file
    that holds the row, as it does for a channel."""
    out = edit(hub, tmp_path, {"kind": "field", "scope": "workspace", "workspace": "T0TEAM0001",
                               "field": "language", "value": "en-US"})
    assert out["err"] is None, out
    assert out["committed"]["workspaces"][0]["language"] == "en-US", out["committed"]["workspaces"][0]
    out = edit(hub, tmp_path, {"kind": "field", "scope": "workspace", "workspace": "T0CLIENT01",
                               "field": "persona", "value": "hal"})
    assert out["err"] is None, out
    assert out["local"]["workspaces"][0]["persona"] == "hal", out["local"]["workspaces"][0]


def test_a_project_language_outside_the_policy_is_refused(hub, tmp_path):
    """The policy owns the language list, so the hub refuses a tag it does not support and names the key."""
    out = edit(hub, tmp_path, {"kind": "field", "scope": "project", "field": "language", "value": "xx-XX"})
    assert out["err"] and "project.language" in out["err"], out


# ---------------------------------------------------------------- the renderer, run in node

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


def render(hub, out, sel, docs_in=None, saved=None):
    docs_in = docs() if docs_in is None else docs_in
    saved = docs() if saved is None else saved
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs_in)};"
            f"slSavedDocs={json.dumps(saved)};slTsel={json.dumps(sel)};"
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_project_language_select_carries_the_project_target(hub, tmp_path):
    """Criterion 1 on the page: the project's Language select carries the target the hub named, so its change reaches
    the hub instead of falling to no handler."""
    html = render(hub, read(hub, tmp_path), "c0.p0")
    assert 'data-slf="project|language"' in html, html[html.index("slsetrow"):][:900]


def test_the_edit_handler_names_the_project_and_its_field(hub):
    """The handler builds one operation that names the project and the field, never the row's place in a list."""
    body = (_slices(hub.PAGE)
            + "\nconsole.log(JSON.stringify(slFieldOp('project','language',{type:'select-one',value:'en-US'})))")
    assert json.loads(_node(body)) == {"kind": "field", "scope": "project", "field": "language", "value": "en-US"}


def test_the_edit_handler_names_the_workspace_and_its_id(hub):
    """The handler builds one operation that names the workspace by its team id, never its place in the list."""
    body = (_slices(hub.PAGE)
            + "\nconsole.log(JSON.stringify(slFieldOp('ws:T0TEAM0001','language',{type:'select-one',value:'en-US'})))")
    assert json.loads(_node(body)) == {"kind": "field", "scope": "workspace", "workspace": "T0TEAM0001",
                                       "field": "language", "value": "en-US"}


def test_a_redraw_keeps_the_staged_project_language_and_names_the_change(hub, tmp_path):
    """Criterion 2: after the staged edit the redraw reads the value from the staged document, so en-US stays selected,
    and the page's dirty test names the Communication theme for the footer."""
    out = read(hub, tmp_path)
    staged = docs()
    staged["committed"]["project"]["language"] = "en-US"
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(staged)};"
            f"slSavedDocs={json.dumps(docs())};slTsel=\"c0.p0\";"
            "slRender();console.log(JSON.stringify({html:STORE['#sbody'].innerHTML,dirty:slDirty()}))")
    page = json.loads(_node(body))
    segment = page["html"][page["html"].index('data-slf="project|language"'):][:400]
    assert 'value="en-US" selected' in segment, segment
    assert page["dirty"] is True, page["dirty"]


def test_a_redraw_keeps_the_staged_project_persona(hub, tmp_path):
    """The Persona select of the same row shares the cause and the fix, so it keeps its staged value too."""
    out = read(hub, tmp_path)
    staged = docs()
    staged["committed"]["project"]["persona"] = "iara"
    html = render(hub, out, "c0.p0", docs_in=staged, saved=docs())
    segment = html[html.index('data-slf="project|persona"'):][:400]
    assert 'value="iara" selected' in segment, segment


def test_a_redraw_keeps_the_staged_workspace_language(hub, tmp_path):
    """The same read for a workspace row: the staged value wins over the value the tree read carried."""
    out = read(hub, tmp_path)
    staged = docs()
    staged["committed"]["workspaces"][0]["language"] = "en-US"
    html = render(hub, out, "c0.p0.w0", docs_in=staged, saved=docs())
    segment = html[html.index('data-slf="ws:T0TEAM0001|language"'):][:400]
    assert 'value="en-US" selected' in segment, segment
