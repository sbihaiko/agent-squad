"""GitHub issue #2941: a channel's name is its first setting, and the owner edits it there (SPEC 2682).

The channel node's Settings block opens with a Name row: it shows the channel row's `name` without the leading `#`, and
its chip names the file that holds the row. An edit writes the new name into that one row; the file keeps the name with
no leading `#`, and an empty name is refused with a message that names the row. On a node of another project the row
reads only, and each control of a read-only row keeps its own label. The hub decides the file and the format; the view
draws them and holds no rule (ADR-0137, ADR-0177).

The seams are the hub's editor, `slack_docs_edit`, the hub's read, `slack_overview`, whose `tree` key is the tree, and
the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import copy
import json
import re

import pytest
import yaml

from tests import test_hub_slack_field_file as F

hub = F.hub   # the shared module fixture, registered here under its own name
COMMITTED, LOCAL, channel_row, read, walk, by_name = F.COMMITTED, F.LOCAL, F.channel_row, F.read, F.walk, F.by_name


def edit(hub, tmp_path, channel, value, committed=None, local=None):
    """One tree-pane edit of the channel's name, addressed by its Slack id."""
    c, loc = F.paths(tmp_path, committed, local)
    body = {"committed": copy.deepcopy(COMMITTED if committed is None else committed),
            "local": copy.deepcopy(LOCAL if local is None else local),
            "op": {"kind": "field", "channel": channel, "field": "name", "value": value}}
    return hub.slack_docs_edit(body, committed=c, local=loc)


def render(hub, out, sel, docs):
    body = (F._slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs)};"
            f"slTsel={json.dumps(sel)};slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(F._node(body))


def rows_of(node):
    return {row["key"]: row for row in node["settings"]["rows"]}


# ---------------------------------------------------------------- the read names the name row first

def test_the_name_row_opens_the_channel_settings(hub, tmp_path):
    """Criteria 1, 2 and 3: the first row is the Name row, it shows the row's `name` with no leading `#`, and its chip
    names the committed file that holds the channel row."""
    node = by_name(read(hub, tmp_path)["tree"]["root"], "#squad-alerts")
    first = node["settings"]["rows"][0]
    assert first["key"] == "name" and first["label"] == "Name", first
    assert first["doc"] == "committed" and first["file"] == "slack.yaml", first
    assert [(c["field"], c["kind"], c["value"]) for c in first["controls"]] == [("name", "text", "squad-alerts")], first


def test_the_name_row_names_the_local_file_of_a_local_channel(hub, tmp_path):
    """Criterion 3: a channel the local file alone holds names that file in its chip."""
    node = by_name(read(hub, tmp_path)["tree"]["root"], "#support")
    first = node["settings"]["rows"][0]
    assert first["key"] == "name" and first["doc"] == "local", first
    assert first["file"] == "4.slack.local.yaml", first
    assert first["controls"][0]["value"] == "support", first


# ---------------------------------------------------------------- the edit writes that one row

def test_a_name_edit_writes_that_channel_row_and_no_other(hub, tmp_path):
    """Criterion 4: the new name lands in the row that carries the id, and the other rows keep every value."""
    committed = copy.deepcopy(COMMITTED)
    out = edit(hub, tmp_path, "C0ALERT001", "release-notes", committed=committed)
    assert out["err"] is None, out
    assert channel_row(out["committed"], "T0TEAM0001", "C0ALERT001")["name"] == "release-notes", out["committed"]
    want = copy.deepcopy(committed)
    channel_row(want, "T0TEAM0001", "C0ALERT001")["name"] = "release-notes"
    assert out["committed"] == want, out["committed"]
    assert out["local"] == LOCAL, out["local"]


def test_a_name_edit_names_the_row_it_writes_when_the_id_is_unknown(hub, tmp_path):
    """Criterion 4: an id neither file names is refused, and both documents keep their values."""
    out = edit(hub, tmp_path, "C0NOSUCH01", "release-notes")
    assert out["err"] and "C0NOSUCH01" in out["err"], out
    assert out["committed"] == COMMITTED and out["local"] == LOCAL, out


def test_a_hash_and_the_surrounding_space_leave_the_stored_name(hub, tmp_path):
    """Criterion 7: a leading `#` and the space around the text are not part of the stored name."""
    out = edit(hub, tmp_path, "C0ALERT001", " #release notes ")
    assert out["err"] is None, out
    assert channel_row(out["committed"], "T0TEAM0001", "C0ALERT001")["name"] == "release notes", out["committed"]


def test_an_empty_name_is_refused_and_the_refusal_names_the_row(hub, tmp_path):
    """Criterion 6: an empty name, one that is the `#` alone, and one of blanks are each refused; the message names the
    row's key in the file, and neither document changes."""
    for value in ("", "#", "   ", "#  "):
        out = edit(hub, tmp_path, "C0ALERT001", value)
        assert out["err"], (value, out)
        assert "workspaces[0].channels[1].name" in out["err"] and "name" in out["err"], (value, out["err"])
        assert out["committed"] == COMMITTED and out["local"] == LOCAL, (value, out)


def test_the_saved_name_reaches_the_title_and_the_breadcrumb(hub, tmp_path):
    """Criterion 5: after the save, the tree node title and the breadcrumb show the new name."""
    out = edit(hub, tmp_path, "C0ALERT001", "release-notes")
    assert out["err"] is None, out
    tree = read(hub, tmp_path, committed=out["committed"], local=out["local"])["tree"]
    node = by_name(tree["root"], "#release-notes")
    html = render(hub, {"tree": tree}, node["id"], {"committed": out["committed"], "local": out["local"]})
    assert re.search(r"<h3 class=slth>#release-notes\b", html), html[:1200]
    crumb = re.search(r"<nav class=sltcrumb[^>]*>(.*?)</nav>", html, re.S).group(1)
    assert "release-notes" in crumb, crumb
    assert "squad-alerts" not in crumb, crumb


# ---------------------------------------------------------------- the row on the page

def test_the_page_draws_the_name_row_first_with_its_file_chip(hub, tmp_path):
    """Criteria 1 and 3 on the page: the settings block opens with the Name row, and its chip names the file."""
    out = read(hub, tmp_path)
    node = by_name(out["tree"]["root"], "#support")
    html = render(hub, out, node["id"], {"committed": COMMITTED, "local": LOCAL})
    block = html[html.index("Settings"):]
    first = re.search(r"<div class=slsetrow[^>]*>(.*?)</div>", block, re.S).group(1)
    assert "Name" in first and "4.slack.local.yaml" in first, first
    assert 'data-slf="C0CLISUP01|name"' in first, first
    assert 'value="support"' in first, first


# ---------------------------------------------------------------- another project reads only

OTHER = {"version": 1, "project": {"language": "es-ES", "persona": "nova"},
         "personas": [{"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Short."}],
         "roles": [{"id": "manager", "actions": ["status"]}],
         "workspaces": [{"id": "T0OTHER001", "label": "other-team", "class": "owned", "persona": "nova",
                         "channels": [{"id": "C0OTHER002", "name": "other-room", "class": "guest",
                                       "mode": "listen+speak", "speak": "draft"}]}]}

MAP = [{"id": "acme", "name": "Acme", "default_persona": None,
        "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": True}]}]


@pytest.fixture
def client_map(hub, monkeypatch):
    """A project no client names is drawn nowhere, so the map names both projects."""
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (json.loads(json.dumps(MAP)), None))


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
    return hub._slack_clients_tree_for(P, [here, other], here["committed_path"])


def _other_channel(hub, tmp_path):
    tree = _tree(hub, tmp_path)
    beta = next(n for c in tree["clients"] for n in walk(c) if n["kind"] == "project" and n["name"] == "beta")
    return tree, beta, next(n for n in walk(beta) if n["kind"] == "channel")


def test_a_name_row_of_another_project_reads_its_name(hub, tmp_path, client_map):
    """Criterion 8: on another project's node the Name row is read-only and shows the name, with no leading `#`."""
    _tree_, _beta, channel = _other_channel(hub, tmp_path)
    first = channel["settings"]["rows"][0]
    assert first["key"] == "name", first
    assert [(c["field"], c["kind"]) for c in first["controls"]] == [("name", "read")], first
    assert first["controls"][0]["value"] == "other-room", first


def test_a_read_only_two_control_row_shows_each_control_label(hub, tmp_path, client_map):
    """Criterion 9: on a read-only node, each control of the two-control Delivery row carries its own label."""
    tree, _beta, channel = _other_channel(hub, tmp_path)
    delivery = rows_of(channel)["delivery"]
    assert [(c["field"], c["kind"], c.get("label")) for c in delivery["controls"]] == [
        ("speak", "read", "Answer as"), ("send_grant", "read", "Send grant")], delivery
    html = render(hub, {"tree": tree}, channel["id"], {"committed": OTHER, "local": {"version": 1}})
    assert "Answer as" in html and "Send grant" in html, html[:2000]
    assert "<span class=slsetk>Delivery</span>" not in html, "the row label is never drawn twice"
