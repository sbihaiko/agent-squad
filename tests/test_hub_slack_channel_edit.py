"""GitHub issue #2918: add and remove a Slack channel from its own tree node, and open it in Slack (SPEC 2682).

The owner looks for "Add channel" on the workspace node and "Remove channel" on the channel node. The workspace node
lists the workspace's channels by name, read through the workspace's own Slack connection; a host that cannot read
them offers the raw Slack-id field under a disclosure. The channel node links to the channel in the Slack web client.
The hub names every edit target and the channel list; the view draws them (ADR-0137).

The seams are the hub's read, `slack_overview`, the hub's channels route over a stubbed Slack connection, and the
theme's renderer run in node. No test calls Slack. Every id and name is a neutral placeholder.
"""
import json
import re

import pytest

from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
LOCAL, COMMITTED, by_name, read, render, _slices, _node = T.LOCAL, T.COMMITTED, T.by_name, T.read, T.render, T._slices, T._node

TEAM = "T0TEAM0001"
CHANNELS = [
    {"id": "C0PROJ0001", "name": "proj-agent-squad", "is_archived": False},
    {"id": "C0NEWCH001", "name": "release-notes", "is_archived": False},
    {"id": "C0NEWCH002", "name": "design", "is_archived": False},
]


@pytest.fixture
def slack(hub, monkeypatch):
    """A stubbed Slack connection to one workspace. `scope` decides whether the channel list may be read."""
    state = {"calls": [], "scope": True}

    def api(token, method):
        state["calls"].append(method)
        name = method.split("?")[0]
        if name == "auth.test":
            return {"ok": True, "team_id": TEAM}
        if name == "conversations.list":
            if not state["scope"]:
                return {"ok": False, "error": "missing_scope", "needed": "channels:read"}
            return {"ok": True, "channels": CHANNELS, "response_metadata": {"next_cursor": ""}}
        return {"ok": False, "error": "unknown_method"}

    monkeypatch.setattr(hub, "KEYCHAIN", True)
    monkeypatch.setattr(hub, "slack_token", lambda profile=None: "stub-token")
    monkeypatch.setattr(hub, "slack_api", api)
    hub._SLACK_CHANNELS.clear()
    return state


# ---------------------------------------------------------------- the hub's read

def test_the_workspace_node_names_its_add_target(hub, tmp_path):
    ws = by_name(read(hub, tmp_path)["tree"]["root"], "team")
    assert ws["channel_add"] == {"doc": "committed", "ws": 0, "workspace": TEAM,
                                 "named": ["C0PROJ0001", "C0ALERT001"]}, ws.get("channel_add")
    guest = by_name(read(hub, tmp_path)["tree"]["root"], "client-a")
    assert guest["channel_add"]["doc"] == "local", guest.get("channel_add")


def test_the_channel_node_names_its_remove_target_and_its_slack_link(hub, tmp_path):
    root = read(hub, tmp_path)["tree"]["root"]
    ch = by_name(root, "#squad-alerts")
    assert ch["channel_remove"] == {"doc": "committed", "ws": 0, "index": 1, "name": "#squad-alerts"}, ch.get("channel_remove")
    assert ch["open"] == "https://app.slack.com/client/T0TEAM0001/C0ALERT001", ch.get("open")
    assert by_name(root, "#general")["channel_remove"]["doc"] == "local"


def test_a_channel_with_no_slack_id_has_no_link(hub, tmp_path):
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"].append({"name": "unnamed-room", "mode": "listen"})
    ch = by_name(read(hub, tmp_path, committed=committed)["tree"]["root"], "#unnamed-room")
    assert not ch.get("open"), ch.get("open")


# ---------------------------------------------------------------- the channels route

def test_the_route_lists_the_channels_the_files_do_not_name(hub, slack):
    out = hub.slack_channels_post({"workspace": TEAM, "named": ["C0PROJ0001"]})
    assert out["err"] is None, out
    assert out["channels"] == [{"id": "C0NEWCH002", "name": "design"}, {"id": "C0NEWCH001", "name": "release-notes"}], out


def test_the_route_names_the_channels_the_files_map_and_slack_does_not_list(hub, slack):
    """#2955: the hub's own Slack read names each channel the files map that Slack does not list, with the probable
    cause, so the tree marks it unreachable instead of drawing it as a live channel."""
    out = hub.slack_channels_post({"workspace": TEAM, "named": ["C0PROJ0001", "C0GONE001"]})
    assert out["err"] is None, out
    assert out["unreachable"] == [{"id": "C0GONE001", "text": hub.SLACK_NOT_LISTED}], out
    assert [c["id"] for c in out["channels"]] == ["C0NEWCH002", "C0NEWCH001"], out


def test_the_route_marks_nothing_when_the_listing_errors(hub, slack):
    """#2955: a listing Slack refuses measures nothing, so no channel is called unreachable on a read that failed."""
    slack["scope"] = False
    out = hub.slack_channels_post({"workspace": TEAM, "named": ["C0PROJ0001", "C0GONE001"]})
    assert out["manual"] is True and out["err"], out
    assert out.get("unreachable", []) == [], out


def test_the_route_names_the_missing_scope(hub, slack):
    slack["scope"] = False
    out = hub.slack_channels_post({"workspace": TEAM, "named": []})
    assert out["err"] and "channels:read" in out["err"], out
    assert out["manual"] is True, out


def test_a_workspace_this_host_cannot_reach_asks_for_the_manual_field(hub, slack):
    out = hub.slack_channels_post({"workspace": "T0OTHER001", "named": []})
    assert out["manual"] is True and "no Slack connection" in out["err"], out


def test_the_channels_route_takes_a_post():
    src = (T.ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    assert '"/api/slack/channels"' in src
    assert re.search(r'self\.path == "/api/slack/channels": out = slack_channels_post\(body\)', src)


# ---------------------------------------------------------------- the page

def _detail(hub, tmp_path, sel, seed=""):
    out = read(hub, tmp_path)
    node = next(n for n in T.walk(out["tree"]["root"]) if n["name"] == sel)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel={json.dumps(node['id'])};"
            + "globalThis.fetch=()=>new Promise(()=>{});" + seed + "slRender();"
            + "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_channel_detail_links_to_slack_and_offers_remove(hub, tmp_path):
    html = _detail(hub, tmp_path, "#squad-alerts")
    assert re.search(r'<a [^>]*href="https://app\.slack\.com/client/T0TEAM0001/C0ALERT001"[^>]*target=_blank', html), html
    assert "Open in Slack" in html
    assert re.search(r'data-sltrmch="committed:0:1"[^>]*>Remove channel<', html), html


def _tree_with(hub, tmp_path, seed):
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};" + seed + "slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_tree_marks_a_mapped_channel_slack_does_not_list(hub, tmp_path):
    """#2955: a channel node whose id the hub's Slack read does not find carries the hub's own sentence, so the owner
    reads a channel Slack cannot reach and never a live-looking one."""
    seed = ('slChList={"T0TEAM0001":{"err":null,"manual":false,"channels":[],"unreachable":'
            + json.dumps([{"id": "C0ALERT001", "text": hub.SLACK_NOT_LISTED}]) + "}};")
    html = _tree_with(hub, tmp_path, seed)
    item = next(i for i in re.findall(r"<div[^>]*role=treeitem[^>]*>", html) if "squad-alerts" in i)
    assert hub.SLACK_NOT_LISTED in item, item


def test_the_tree_marks_no_channel_the_hub_lists(hub, tmp_path):
    """#2955: the mark follows the hub's read alone: a channel the read lists carries no unreachable sentence."""
    seed = ('slChList={"T0TEAM0001":{"err":null,"manual":false,"channels":[],"unreachable":[]}};')
    html = _tree_with(hub, tmp_path, seed)
    assert hub.SLACK_NOT_LISTED not in html, html


def test_the_workspace_detail_offers_the_listed_channels_by_name(hub, tmp_path):
    seed = ('slChList={"T0TEAM0001":{"err":null,"manual":false,'
            '"channels":[{"id":"C0NEWCH002","name":"design"},{"id":"C0NEWCH001","name":"release-notes"}]}};')
    html = _detail(hub, tmp_path, "team", seed)
    assert re.search(r'<option value="C0NEWCH002">#design</option>', html), html
    assert re.search(r'data-sltaddch="committed:0"[^>]*>Add channel<', html), html
    assert "C0NEWCH002</option>" not in html   # the owner picks by name, never by id


def test_a_workspace_the_host_cannot_read_shows_the_reason_and_the_id_field(hub, tmp_path):
    seed = ('slChList={"T0TEAM0001":{"err":"the Slack connection lacks the scope channels:read","manual":true,"channels":[]}};')
    html = _detail(hub, tmp_path, "team", seed)
    assert "channels:read" in html, html
    assert re.search(r"<details[^>]*><summary>Slack ids</summary>.*data-slci=\"committed:0\"", html, re.S), html
