"""GitHub issue #2846: a staged edit in the Communication tree shows its effect before the save (SPEC 2682).

The tree's channel pane stages one field over the two raw documents. Before the owner saves, the Now block follows the
staged documents, the save shows the real diff of each changed file, an edit keeps the keyboard on the control that
changed, and Discard restores the values the page read last.

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
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"],
              "announce": ["stg_release"]},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen", "persona": "hal"},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft", "roles": ["manager"]},
         ]},
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
    spec = importlib.util.spec_from_file_location("hub_slack_staged_edit", HUB_PY)
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


def stage(hub, tmp_path, channel, name, value):
    """One tree-pane edit: the channel by its Slack id, the field and its value."""
    c, loc = paths(tmp_path)
    body = dict(docs(), op={"kind": "field", "channel": channel, "field": name, "value": value})
    return hub.slack_docs_edit(body, committed=c, local=loc)


def read(hub, tmp_path):
    c, loc = paths(tmp_path)
    return hub.slack_overview(c, loc)


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_name(root, name):
    for node in walk(root):
        if node["name"] == name:
            return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for n in walk(root)]}")


def tiles(node):
    return {t["key"]: t["value"] for t in node.get("now") or []}


def now_persona(html):
    """The Persona tile of the selected node's Now block, read from the rendered page. The tile is the first one the
    page draws, because the detail pane draws the selected node alone."""
    hit = re.search(r"<span class=tk>Persona</span><span class=tv>([^<]*)</span>", html)
    assert hit, html[:1200]
    return hit.group(1)


# ---------------------------------------------------------------- the hub answers the staged tree

def test_a_staged_edit_answers_the_tree_of_the_staged_documents(hub, tmp_path):
    """Criterion 1: the edit's answer carries a tree whose Now block reads the staged documents through the policy, so
    the page can redraw the Now block before the save. The read of the files still names the value before the edit."""
    out = stage(hub, tmp_path, "C0PROJ0001", "persona", "iara")
    assert out["err"] is None, out
    assert "tree" in out, sorted(out)
    assert tiles(by_name(out["tree"]["root"], "#proj-agent-squad"))["Persona"] == "Iara"
    assert tiles(by_name(read(hub, tmp_path)["tree"]["root"], "#proj-agent-squad"))["Persona"] == "HAL9K"


def test_the_staged_tree_names_the_same_files_as_the_read(hub, tmp_path):
    """The staged tree names the same files as the read, so the page's chips never move under the owner's eyes."""
    out = stage(hub, tmp_path, "C0PROJ0001", "persona", "iara")
    assert out["tree"]["files"] == read(hub, tmp_path)["tree"]["files"], out["tree"]["files"]


def test_a_refused_edit_carries_no_staged_tree(hub, tmp_path):
    """A refused edit changes nothing, so its answer carries no tree: the page keeps the read it has."""
    out = stage(hub, tmp_path, "C0NOSUCH01", "persona", "iara")
    assert out["err"] and "tree" not in out, out


def test_the_persona_control_stages_a_persona_id_so_its_edit_reaches_the_policy(hub, tmp_path):
    """A control stages the value the policy names, never a counter: the persona options carry `hal` and `iara`, so the
    edit the page posts names a declared persona and the hub accepts it."""
    node = by_name(read(hub, tmp_path)["tree"]["root"], "#proj-agent-squad")
    row = next(r for r in node["settings"]["rows"] if r["key"] == "persona")
    values = [o["value"] for o in row["controls"][0]["options"]]
    assert "hal" in values and "iara" in values, values
    out = stage(hub, tmp_path, "C0PROJ0001", "persona", values[-1])
    assert out["err"] is None, out


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


def render(hub, out, sel, docs_in=None):
    docs_in = docs() if docs_in is None else docs_in
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs_in)};"
            f"slSavedDocs=JSON.parse(JSON.stringify(slDocs));slTsel={json.dumps(sel)};"
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_channel_settings_controls_carry_a_stable_id(hub, tmp_path):
    """Criterion 3: each settings control carries an id built from the channel and the field, so a redraw can put the
    keyboard back on the very control the owner changed."""
    html = render(hub, read(hub, tmp_path), "c0.p0.w0.c0")
    assert 'id="slf_C0PROJ0001_persona"' in html, html[html.index("data-slf"):][:400]


def test_a_rerender_keeps_the_keyboard_on_the_control_that_changed(hub, tmp_path):
    """Criterion 3: a render replaces the pane's markup, and it puts the focus back on the control that had it."""
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(read(hub, tmp_path))};slDocs={json.dumps(docs())};"
            f"slSavedDocs=JSON.parse(JSON.stringify(slDocs));slTsel=\"c0.p0.w0.c0\";"
            'globalThis.document={activeElement:{id:"slf_C0PROJ0001_persona"},'
            'getElementById:id=>id==="slf_C0PROJ0001_persona"?{focus:()=>console.log("FOCUS:"+id)}:null};'
            "slRender();")
    assert "FOCUS:slf_C0PROJ0001_persona" in _node(body)


def test_a_staged_edit_redraws_the_now_block_before_the_save(hub, tmp_path):
    """Criterion 1 on the page: `slOp` stages the edit, and the Now block of the selected channel follows the tree the
    hub answered, before any save."""
    out = read(hub, tmp_path)
    edited = stage(hub, tmp_path, "C0PROJ0001", "persona", "iara")
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs())};"
            "slSavedDocs=JSON.parse(JSON.stringify(slDocs));slTsel=\"c0.p0.w0.c0\";"
            f"const ANSWER={json.dumps(edited)};"
            "globalThis.fetch=async()=>({json:async()=>ANSWER});"
            "slOp({kind:'field',channel:'C0PROJ0001',field:'persona',value:'iara'})"
            ".then(()=>console.log(JSON.stringify(STORE['#sbody'].innerHTML)));")
    html = json.loads(_node(body))
    assert now_persona(html) == "Iara", html[:1200]


def test_discard_restores_the_values_the_page_read_last(hub, tmp_path):
    """Criterion 4: after a staged edit, Discard puts the documents and the Now block back to the read's values."""
    out = read(hub, tmp_path)
    edited = stage(hub, tmp_path, "C0PROJ0001", "persona", "iara")
    staged = {"committed": edited["committed"], "local": edited["local"]}
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(staged)};"
            f"slSavedDocs={json.dumps(docs())};slT={json.dumps(edited['tree'])};slTsel=\"c0.p0.w0.c0\";"
            "slDiscard();console.log(JSON.stringify({docs:slDocs,html:STORE['#sbody'].innerHTML}));")
    out_page = json.loads(_node(body))
    assert out_page["docs"] == docs(), out_page["docs"]
    assert now_persona(out_page["html"]) == "HAL9K", out_page["html"][:1200]


def test_the_save_shows_the_diff_of_each_changed_file(hub, tmp_path):
    """Criterion 2: the Save asks the hub for the diff of the staged documents, and it draws one group per changed
    file, through the existing save path."""
    out = read(hub, tmp_path)
    c = copy.deepcopy(COMMITTED)
    c["workspaces"][0]["channels"][0]["persona"] = "iara"
    answer = {"diff": {"committed": ["-      persona: hal", "+      persona: iara"], "local": ["-  x: 1", "+  x: 2"]},
              "files": {"committed": "slack.yaml", "local": "4.slack.local.yaml"}}
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps({'committed': c, 'local': copy.deepcopy(LOCAL)})};"
            f"slSavedDocs={json.dumps(docs())};slTsel=\"c0.p0.w0.c0\";const ANSWER={json.dumps(answer)};"
            "globalThis.themeLine=(t,l)=>t+' · '+l;"   # the page defines it above the slice under test
            "globalThis.fetch=async()=>({json:async()=>ANSWER});"
            "slAsk().then(g=>console.log(JSON.stringify(g.map(x=>x.file))));")
    files = json.loads(_node(body))
    assert files == ["slack.yaml", "4.slack.local.yaml"], files
