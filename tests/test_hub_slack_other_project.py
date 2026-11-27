"""GitHub issue #2919: the Slack tree never edits another project as the hub's own project (SPEC 2682).

The tree shows the projects of every profile the hub knows. The page edits the files of its own project only, so a node
of another project shows that project's own values, read-only, and names the page that edits them. A staged edit keeps
every project of the tree.

The seams are the hub's tree over two seats, `_slack_clients_tree_for`, the staged-edit tree, `_slack_tree_docs`, and the
theme's renderer run in node. Every id and name is a neutral placeholder.
"""
import json
import re

import pytest
import yaml

from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
COMMITTED, LOCAL, walk, _slices, _node = T.COMMITTED, T.LOCAL, T.walk, T._slices, T._node

OTHER = {"version": 1, "project": {"language": "es-ES", "persona": "nova"},
         "personas": [{"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Short."}],
         "roles": [{"id": "manager", "actions": ["status"]}],
         "workspaces": [{"id": "T0OTHER001", "label": "other-team", "class": "owned", "persona": "nova",
                         "channels": [{"id": "C0OTHER001", "name": "other-room", "mode": "listen"}]}]}


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


@pytest.fixture(autouse=True)
def _client_map(hub, monkeypatch):
    """Re-anchored (#2922): a project no client names is drawn nowhere, so the map names both projects."""
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (json.loads(json.dumps(MAP)), None))


def _tree(hub, tmp_path):
    here = _seat(tmp_path, "alpha", COMMITTED, LOCAL)
    other = _seat(tmp_path, "beta", OTHER, {"version": 1})
    P = hub.slack_policy()
    return hub._slack_clients_tree_for(P, [here, other], here["committed_path"]), here, other


def _project(tree, name):
    return next(n for c in tree["clients"] for n in walk(c) if n["kind"] == "project" and n["name"] == name)


def _controls(node):
    return [c for r in (node.get("settings") or {}).get("rows") or [] for c in r.get("controls") or []]


def test_another_projects_node_offers_the_persona_and_language_controls(hub, tmp_path):
    """#3033 acceptance 1: the project node of another profile shows the Persona and Language controls, not plain
    values, so the owner changes them from any page."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    controls = {c["field"]: c for c in _controls(beta)}
    assert controls["persona"]["kind"] == "select", controls
    assert controls["persona"]["value"] == "nova", controls
    assert [o["value"] for o in controls["persona"]["options"]] == ["", "nova"], controls
    assert controls["language"]["kind"] == "select" and controls["language"]["value"] == "es-ES", controls
    assert {c["scope"] for c in controls.values()} == {"project"}, controls


def test_the_project_node_names_its_target_and_keeps_other_settings_read_only(hub, tmp_path):
    """#3033 acceptance 10 and 11, re-anchored (#3034): the node names the project its edit writes and shows no 'Edited
    on ...'s page' sentence; below it the Persona and Language rows are edited here, every other setting stays
    read-only, and the channel keeps the link to the page that edits those other settings."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    assert beta["edit"] == {"project": "beta"}, beta.get("edit")
    assert "elsewhere" not in beta, beta.get("elsewhere")
    for node in walk(beta):
        if node is beta:
            continue
        controls = _controls(node)
        live = [c for c in controls if c["kind"] != "read"]
        assert live and {c["kind"] for c in live} == {"select"}, (node["name"], controls)
        assert not node.get("channel_add") and not node.get("channel_remove"), node["name"]
        if node["kind"] == "channel":
            assert node.get("elsewhere") == "/beta/#settings", (node["name"], node.get("elsewhere"))
        else:
            assert "elsewhere" not in node, (node["name"], node.get("elsewhere"))


def test_the_page_draws_selects_and_no_edited_elsewhere_sentence_for_the_project(hub, tmp_path):
    """#3033 acceptance 1 and 10 in the renderer: the project node's two controls are selects that carry the project they
    write, and the node states no sentence about an edit made on another page."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={{}};slT={json.dumps(tree)};slTsel={json.dumps(beta['id'])};"
            + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    detail = html[html.index("id=sltdetail"):]
    assert 'data-slproj="beta"' in detail, detail
    assert detail.count("<select") == 2, detail
    assert "Edited on " not in detail, detail


def test_a_staged_edit_keeps_every_project(hub, tmp_path, monkeypatch):
    here = _seat(tmp_path, "alpha", COMMITTED, LOCAL)
    other = _seat(tmp_path, "beta", OTHER, {"version": 1})
    monkeypatch.setattr(hub, "_slack_roster_checked", lambda cache=0.0: ([here, other], None))
    staged = json.loads(json.dumps(COMMITTED))
    staged["project"]["language"] = "es-ES"
    P = hub.slack_policy()
    tree = hub._slack_tree_docs(P, staged, LOCAL, here["committed_path"], here["local_path"], here=here["committed_path"])
    names = [n["name"] for c in tree["clients"] for n in walk(c) if n["kind"] == "project"]
    assert sorted(names) == ["alpha", "beta"], names


def test_the_project_node_points_each_edit_at_its_own_place(hub, tmp_path):
    """#3016 acceptance 2 and 3: the sentence of another profile's project names the Personas theme for personas and the
    project page for roles and events, so each link names the place that edits it (SPEC 3009)."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps({'tree': tree})};slT={json.dumps(tree)};"
            f"slTsel={json.dumps(beta['id'])};"
            + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    detail = html[html.index("id=sltdetail"):]
    para = next(p for p in re.findall(r"<p class=slttext>(.*?)</p>", detail, re.S) if "personas" in p)
    theme = re.search(r'<a [^>]*data-sp=prs[^>]*>(.*?)</a>', para)
    assert theme, para
    assert 'href="#set-prs"' in theme.group(0), theme.group(0)
    assert "Personas theme" in theme.group(1), theme.group(1)
    page = re.search(r'<a [^>]*href="/beta/#settings"[^>]*>(.*?)</a>', para)
    assert page, para
    assert para.index("roles and events") < para.index('href="/beta/#settings"'), para


def test_the_page_draws_the_read_values_and_the_link(hub, tmp_path):
    """Re-anchored (#3034): the channel of another profile's project draws its Persona and Language selects, bound to
    that project, names the page that edits its other settings, and offers no Remove channel."""
    tree, _, _ = _tree(hub, tmp_path)
    channel = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "channel")
    out = {"tree": tree}
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slT={json.dumps(tree)};slTsel={json.dumps(channel['id'])};"
            + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    detail = html.split("sltdetail", 1)[1]
    assert 'href="/beta/#settings"' in html, html
    assert detail.count("<select") == 2 and 'data-slproj="beta"' in detail, detail
    assert "data-sltrmch" not in html
