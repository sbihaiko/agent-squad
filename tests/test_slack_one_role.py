"""GitHub issue #2936: a Slack user holds one role per workspace (SPEC 2682).

Each role has one tone and one set of allowed actions, so a sender's tone and actions come from one role. The local
members file may name a user in several channels of one workspace, always with the same role.

The seams are the policy's `validate` and `decide`, and the hub's edit route. Every id is a neutral placeholder.
"""
import copy

from squad import slackpolicy as S
from tests import test_hub_slack_clients as C
from tests.test_slack_policy import COMMITTED, LOCAL, keys

hub = C.hub   # the shared module fixture, registered here under its own name

WS, CH, CH2, USER, OTHER_WS = "T0TEAM0001", "C0PROJ0001", "C0PROJ0002", "U0PERSON01", "T0TEAM0002"


def _pair(*members):
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    committed["roles"] = [{"id": "manager", "actions": ["status", "logs"], "tone": "Formal."},
                          {"id": "viewer", "actions": ["status"], "tone": "Brief."}]
    ws = committed["workspaces"][0]
    ws["channels"][0]["roles"] = ["manager", "viewer"]
    ws["channels"].append({"id": CH2, "name": "proj-other", "mode": "listen+speak", "roles": ["manager", "viewer"]})
    committed["workspaces"].append({"id": OTHER_WS, "label": "team-b", "class": "owned", "app": "hal9k",
                                    "channels": [{"id": "C0PROJB001", "name": "proj-b", "mode": "listen+speak",
                                                  "roles": ["manager", "viewer"]}]})
    local["members"] = [{"workspace": w, "channel": c, "role": r, "users": [USER]} for w, c, r in members]
    return committed, local


def test_two_roles_in_one_workspace_are_refused():
    committed, local = _pair((WS, CH, "manager"), (WS, CH2, "viewer"))
    defects = S.validate(committed, local)
    assert "members[1].role" in keys(defects), defects
    text = str([d for d in defects if d.key == "members[1].role"][0])
    assert USER in text and "manager" in text and "viewer" in text and "members[0]" in text, text


def test_two_roles_in_one_channel_are_refused():
    committed, local = _pair((WS, CH, "manager"), (WS, CH, "viewer"))
    assert "members[1].role" in keys(S.validate(committed, local))


def test_one_role_in_two_channels_of_a_workspace_is_valid():
    committed, local = _pair((WS, CH, "manager"), (WS, CH2, "manager"))
    assert S.validate(committed, local) == []


def test_different_roles_in_different_workspaces_are_valid():
    committed, local = _pair((WS, CH, "manager"), (OTHER_WS, "C0PROJB001", "viewer"))
    assert S.validate(committed, local) == []


def test_the_sender_gets_the_persona_tone_then_the_one_role_tone():
    committed, local = _pair((WS, CH, "viewer"))
    decision = S.decide(committed, local, {"workspace": WS, "channel": CH, "user": USER, "text": "status?"}, None)
    assert decision.role == "viewer", decision
    assert decision.tone.endswith("Brief.") and "Formal." not in decision.tone, decision.tone


def test_the_hub_refuses_an_add_that_gives_a_second_role(hub):
    committed, local = _pair((WS, CH, "manager"))
    op = {"kind": "add", "doc": "local", "path": ["members"],
          "value": {"workspace": WS, "channel": CH2, "role": "viewer", "users": [USER]}}
    out = hub.slack_docs_edit({"committed": committed, "local": local, "local_name": "x.slack.local.yaml", "op": op})
    assert out.get("err") and "manager" in out["err"], out
