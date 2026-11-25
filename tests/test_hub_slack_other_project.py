"""GitHub issue #2919: the Slack tree never edits another project as the hub's own project (SPEC 2682).

The tree shows the projects of every profile the hub knows. The page edits the files of its own project only, so a node
of another project shows that project's own values, read-only, and names the page that edits them. A staged edit keeps
every project of the tree.

The seams are the hub's tree over two seats, `_slack_clients_tree_for`, the staged-edit tree, `_slack_tree_docs`, and the
theme's renderer run in node. Every id and name is a neutral placeholder.
"""
import json

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


def _tree(hub, tmp_path):
    here = _seat(tmp_path, "alpha", COMMITTED, LOCAL)
    other = _seat(tmp_path, "beta", OTHER, {"version": 1})
    P = hub.slack_policy()
    return hub._slack_clients_tree_for(P, [here, other], here["committed_path"]), here, other


def _project(tree, name):
    return next(n for c in tree["clients"] for n in walk(c) if n["kind"] == "project" and n["name"] == name)


def _controls(node):
    return [c for r in (node.get("settings") or {}).get("rows") or [] for c in r.get("controls") or []]


def test_another_project_shows_its_own_values_read_only(hub, tmp_path):
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    controls = {c["field"]: c for c in _controls(beta)}
    assert controls["persona"] == {"field": "persona", "kind": "read", "value": "Nova"}, controls
    assert controls["language"] == {"field": "language", "kind": "read", "value": "es-ES"}, controls
    alpha = _project(tree, "alpha")
    assert {c["kind"] for c in _controls(alpha)} == {"select"}, _controls(alpha)


def test_no_node_of_another_project_carries_an_edit(hub, tmp_path):
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    for node in walk(beta):
        assert {c["kind"] for c in _controls(node)} <= {"read"}, (node["name"], _controls(node))
        assert not node.get("channel_add") and not node.get("channel_remove"), node["name"]
        assert node.get("elsewhere") == "/beta/#settings", (node["name"], node.get("elsewhere"))
    alpha = _project(tree, "alpha")
    assert not any(n.get("elsewhere") for n in walk(alpha))


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


def test_the_page_draws_the_read_values_and_the_link(hub, tmp_path):
    tree, _, _ = _tree(hub, tmp_path)
    channel = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "channel")
    out = {"tree": tree}
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slT={json.dumps(tree)};slTsel={json.dumps(channel['id'])};"
            + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    assert 'href="/beta/#settings"' in html, html
    assert "<select" not in html.split("sltdetail", 1)[1], "a node of another project draws no select"
    assert "data-sltrmch" not in html
