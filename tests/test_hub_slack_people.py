"""GitHub issue #2911: Slack members are named by email or @name, never by id (SPEC 2682).

A Slack user knows a person by email or by @name. The local file still stores user ids, because the policy reads ids,
but the members editor never asks for one and never shows one. The hub resolves a typed email or @name to a user id
through the workspace's own Slack connection, and answers display names for the ids a file holds.

The seams under test are the hub's people route, over a stubbed Slack connection, and the page's members editor, run
in node. No test calls Slack.
"""
import json
import re

import pytest

from tests import test_hub_slack_clients as C

_node, _slices = C._node, C._slices
hub = C.hub   # the shared module fixture, registered here under its own name

TEAM = "T0TEAM0001"
PEOPLE = [
    {"id": "U0PERSON01", "name": "ana", "profile": {"display_name": "Ana", "real_name": "Ana Example"}},
    {"id": "U0PERSON02", "name": "rafa", "profile": {"display_name": "", "real_name": "Rafa Example"}},
    {"id": "U0PERSON03", "name": "anabel", "profile": {"display_name": "Anabel", "real_name": "Anabel Example"}},
    {"id": "U0BOT00001", "name": "helper", "is_bot": True, "profile": {"display_name": "Helper"}},
]


@pytest.fixture
def slack(hub, monkeypatch):
    """A stubbed Slack connection to one workspace. `calls` records each method; `scopes` decides the email lookup."""
    state = {"calls": [], "email_scope": False}

    def api(token, method):
        state["calls"].append(method)
        name = method.split("?")[0]
        if name == "auth.test":
            return {"ok": True, "team_id": TEAM, "url": "https://team.slack.com/"}
        if name == "users.list":
            return {"ok": True, "members": PEOPLE, "response_metadata": {"next_cursor": ""}}
        if name == "users.lookupByEmail":
            if not state["email_scope"]:
                return {"ok": False, "error": "missing_scope", "needed": "users:read.email"}
            if "ana%40example.com" in method:
                return {"ok": True, "user": PEOPLE[0]}
            return {"ok": False, "error": "users_not_found"}
        return {"ok": False, "error": "unknown_method"}

    monkeypatch.setattr(hub, "KEYCHAIN", True)
    monkeypatch.setattr(hub, "slack_token", lambda profile=None: "stub-token")
    monkeypatch.setattr(hub, "slack_api", api)
    hub._SLACK_PEOPLE.clear()
    return state


def test_an_at_name_resolves_to_one_person(hub, slack):
    out = hub.slack_people_post({"workspace": TEAM, "q": "@rafa"})
    assert out["err"] is None, out
    assert out["matches"] == [{"id": "U0PERSON02", "handle": "rafa", "name": "Rafa Example"}], out


def test_a_display_name_resolves_and_two_matches_list_both(hub, slack):
    out = hub.slack_people_post({"workspace": TEAM, "q": "Ana"})
    assert [m["id"] for m in out["matches"]] == ["U0PERSON01"], out   # an exact name wins over a partial one
    out = hub.slack_people_post({"workspace": TEAM, "q": "an"})
    assert [m["name"] for m in out["matches"]] == ["Ana", "Anabel"], out


def test_no_match_says_so_and_a_bot_is_never_a_match(hub, slack):
    out = hub.slack_people_post({"workspace": TEAM, "q": "@helper"})
    assert out["err"] == "No Slack user matches", out
    assert out["matches"] == [], out


def test_an_email_names_the_missing_scope_and_resolves_once_the_scope_is_there(hub, slack):
    out = hub.slack_people_post({"workspace": TEAM, "q": "ana@example.com"})
    assert out["err"] and "users:read.email" in out["err"], out
    slack["email_scope"] = True
    out = hub.slack_people_post({"workspace": TEAM, "q": "ana@example.com"})
    assert out["err"] is None and out["matches"][0]["id"] == "U0PERSON01", out


def test_ids_answer_their_names_and_the_directory_is_read_once_per_window(hub, slack):
    a = hub.slack_people_post({"workspace": TEAM, "ids": ["U0PERSON01", "U0PERSON02", "U0GONE0001"]})
    b = hub.slack_people_post({"workspace": TEAM, "ids": ["U0PERSON03"]})
    assert a["people"] == {"U0PERSON01": {"id": "U0PERSON01", "handle": "ana", "name": "Ana"},
                           "U0PERSON02": {"id": "U0PERSON02", "handle": "rafa", "name": "Rafa Example"}}, a
    assert b["people"]["U0PERSON03"]["name"] == "Anabel", b
    assert sum(1 for c in slack["calls"] if c.startswith("users.list")) == 1, slack["calls"]


def test_a_workspace_this_host_cannot_reach_asks_for_the_manual_field(hub, slack):
    out = hub.slack_people_post({"workspace": "T0OTHER001", "q": "@ana"})
    assert out["manual"] is True and "no Slack connection" in out["err"], out


def test_the_people_route_takes_a_post():
    src = (C.ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    assert '"/api/slack/people"' in src
    assert re.search(r'self\.path == "/api/slack/people": out = slack_people_post\(body\)', src)


# ---------------------------------------------------------------- the members editor

DOCS = {"committed": {"version": 1, "roles": [{"id": "manager", "actions": ["status"]}],
                      "workspaces": [{"id": TEAM, "label": "team", "class": "owned",
                                      "channels": [{"id": "C0PROJ0001", "name": "project"}]}]},
        "local": {"version": 1, "members": [{"workspace": TEAM, "channel": "C0PROJ0001", "role": "manager",
                                             "users": ["U0PERSON01"], "alias": "ana"}]}}


def _editor(hub, people=None):
    seed = f"slPeople={json.dumps({TEAM: people})};" if people else ""
    body = (_slices(hub.PAGE) + f"\nslDocs={json.dumps(DOCS)};slSavedDocs=slClone(slDocs);globalThis.fetch=()=>new Promise(()=>{{}});"
            + seed + 'console.log(JSON.stringify(slRolesEditHtml({files:{committed:"slack.yaml",local:"x.slack.local.yaml"}})))')
    return json.loads(_node(body))


def test_the_members_editor_shows_people_and_no_slack_id(hub):
    html = _editor(hub, {"U0PERSON01": {"id": "U0PERSON01", "handle": "ana", "name": "Ana"}})
    assert "Ana" in html and "@ana" in html, html
    for raw in ("U0PERSON01", ">team · T0TEAM0001<", "#project · C0PROJ0001", "U0..."):
        assert raw not in html, raw
    assert re.search(r"<option[^>]*>team</option>", html), html
    assert re.search(r"<option[^>]*>#project</option>", html), html
    assert re.search(r'placeholder="email or @name"', html), html
