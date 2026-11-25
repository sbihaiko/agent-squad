"""GitHub issue #2929: a Slack role that ignores every message of its members (SPEC 2682).

A member with no role already gets no answer: the message becomes data. A role with no action still reaches the inbox,
so the owner needs an explicit flag. A role row may carry `ignore: true`; a user who holds an accepted ignore role on a
channel gets the decision `ignore` for every message there, and the flag wins over the user's other roles.

The seams are the policy's `validate` and `decide`, and the page's roles editor and its field op, run in node. Every id
is a neutral placeholder.
"""
import copy
import json

from squad import slackpolicy as S
from tests import test_hub_slack_clients as C
from tests.test_slack_policy import COMMITTED, LOCAL, keys

_node, _slices = C._node, C._slices
hub = C.hub   # the shared module fixture, registered here under its own name

WS, CH, USER = "T0TEAM0001", "C0PROJ0001", "U0OWNER001"
IGNORED = {"id": "Ignored", "ignore": True}


def _pair(roles=("manager", "Ignored"), member_roles=("Ignored",)):
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    committed["roles"] = committed["roles"] + [dict(IGNORED)]
    committed["workspaces"][0]["channels"][0]["roles"] = list(roles)
    local["members"] = [{"workspace": WS, "channel": CH, "role": r, "users": [USER]} for r in member_roles]
    return committed, local


def _decide(committed, local, text="status?"):
    return S.decide(committed, local, {"workspace": WS, "channel": CH, "user": USER, "text": text}, None)


# ---------------------------------------------------------------- the validator

def test_an_ignore_role_with_no_action_is_valid():
    committed, local = _pair()
    assert S.validate(committed, local) == []


def test_an_ignore_role_that_lists_an_action_is_refused():
    committed, local = _pair()
    committed["roles"][-1]["actions"] = ["status"]
    defects = S.validate(committed, local)
    assert "roles[1].actions" in keys(defects), defects


def test_an_ignore_role_that_lists_a_deploy_target_is_refused():
    committed, local = _pair()
    committed["roles"][-1]["deploy"] = ["stg"]
    defects = S.validate(committed, local)
    assert "roles[1].deploy" in keys(defects), defects


def test_an_ignore_flag_that_is_not_a_boolean_is_refused():
    committed, local = _pair()
    committed["roles"][-1]["ignore"] = "yes"
    assert "roles[1].ignore" in keys(S.validate(committed, local))


# ---------------------------------------------------------------- the decision

def test_a_member_of_an_ignore_role_is_ignored():
    committed, local = _pair()
    assert _decide(committed, local).kind == S.IGNORE


def test_a_message_that_addresses_a_persona_is_still_ignored():
    committed, local = _pair()
    assert _decide(committed, local, text="@HAL9K status?").kind == S.IGNORE


def test_the_ignore_role_wins_over_another_role_of_the_same_user():
    for order in (("manager", "Ignored"), ("Ignored", "manager")):
        committed, local = _pair(member_roles=order)
        assert _decide(committed, local).kind == S.IGNORE, order


def test_an_ignore_role_the_channel_does_not_accept_ignores_nothing():
    committed, local = _pair(roles=("manager",), member_roles=("manager", "Ignored"))
    decision = _decide(committed, local)
    assert decision.kind == S.INBOX and decision.role == "manager", decision


def test_a_member_of_another_role_is_not_ignored():
    committed, local = _pair(member_roles=("manager",))
    assert _decide(committed, local).kind == S.INBOX


# ---------------------------------------------------------------- the roles editor

DOCS = {"committed": {"version": 1, "roles": [{"id": "QA", "actions": ["status"], "deploy": ["stg"]}, dict(IGNORED)],
                      "workspaces": [{"id": WS, "label": "team", "class": "owned",
                                      "channels": [{"id": CH, "name": "project"}]}]},
        "local": {"version": 1, "members": []}}


def _page(hub, tail):
    body = (_slices(hub.PAGE) + f"\nslDocs={json.dumps(DOCS)};slSavedDocs=slClone(slDocs);"
            + "globalThis.fetch=()=>new Promise(()=>{});" + tail)
    return json.loads(_node(body))


def test_each_role_row_offers_the_ignore_checkbox(hub):
    html = _page(hub, 'console.log(JSON.stringify(slRolesEditHtml({files:{committed:"slack.yaml",local:"x.slack.local.yaml"}})))')
    assert 'data-sle="role:0:ignore"' in html and "Ignore all messages" in html, html
    assert 'data-sle="role:1:ignore" checked' in html, html


def test_checking_ignore_writes_the_flag_and_clears_actions_and_deploy(hub):
    ops = _page(hub, 'console.log(JSON.stringify(slDocOp("role:0:ignore",{type:"checkbox",checked:true})))')
    assert [(o["kind"], o["doc"], o["path"], o["value"]) for o in ops] == [
        ("set", "committed", ["roles", 0, "actions"], []), ("set", "committed", ["roles", 0, "deploy"], []),
        ("set", "committed", ["roles", 0, "ignore"], True)], ops
    off = _page(hub, 'console.log(JSON.stringify(slDocOp("role:1:ignore",{type:"checkbox",checked:false})))')
    assert off["path"] == ["roles", 1, "ignore"] and off["value"] is False, off


def test_the_hub_applies_the_ignore_ops_in_order(hub):
    docs = copy.deepcopy(DOCS)
    for field, value in (("actions", []), ("deploy", []), ("ignore", True)):
        op = {"kind": "set", "doc": "committed", "path": ["roles", 0, field], "value": value}
        out = hub.slack_docs_edit({**docs, "local_name": "x.slack.local.yaml", "op": op})
        assert not out.get("err"), (field, out)
        docs = {"committed": out["committed"], "local": out["local"]}
    assert docs["committed"]["roles"][0] == {"id": "QA", "actions": [], "deploy": [], "ignore": True}, docs


def test_the_hub_refuses_the_ignore_flag_while_the_role_lists_actions(hub):
    op = {"kind": "set", "doc": "committed", "path": ["roles", 0, "ignore"], "value": True}
    out = hub.slack_docs_edit({**copy.deepcopy(DOCS), "local_name": "x.slack.local.yaml", "op": op})
    assert out.get("err") and "ignore role lists no action" in out["err"], out


def test_the_hub_refuses_an_ignore_value_that_is_not_a_boolean(hub):
    op = {"kind": "set", "doc": "committed", "path": ["roles", 0, "ignore"], "value": "yes"}
    out = hub.slack_docs_edit({**copy.deepcopy(DOCS), "local_name": "x.slack.local.yaml", "op": op})
    assert out.get("err") and "ignore is true or false" in out["err"], out
