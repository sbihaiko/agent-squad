"""The `session` field of a role and the decision it yields (ADR-0180).

Guards: a role names `full` or `bounded`, and a role with no field is `bounded`; any other value is one defect that names
its key; `decide` returns the session mode, `full` only for the workspace owner the hub registry names in a `full` role
of a project channel with an active revision, and `bounded` for every other member, a direct message and every other kind
of decision.
"""
import copy
from datetime import datetime

import pytest

from squad import slackpolicy as S

NOW = datetime(2026, 11, 25, 12, 0, 0)

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "language": "en-US", "persona": "hal",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "persona": "hal",
                       "roles": ["manager"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "members": [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]}],
}

OWNER_EVENT = {"kind": "message", "workspace": "T0TEAM0001", "channel": "C0PROJ0001", "user": "U0OWNER001",
               "text": "status please", "bot": False, "external": False, "own": False, "owner": True,
               "active": True, "dm": False}
OWNERS = {"T0TEAM0001": "U0OWNER001"}   # the owner id the hub registry names for the workspace (#2966)


def files(role=None):
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    if role is not None:
        committed["roles"] = [role]
    return committed, local


def keys(defects):
    return [d.key for d in defects]


@pytest.mark.parametrize("mode", ["full", "bounded"])
def test_a_role_names_either_session_mode(mode):
    committed, local = files({"id": "manager", "actions": ["status"], "session": mode})
    assert S.validate(committed, local, owners=OWNERS) == []


def test_a_role_session_outside_the_two_modes_is_one_defect_naming_its_key():
    committed, local = files({"id": "manager", "actions": ["status"], "session": "partial"})
    defects = S.validate(committed, local)
    assert keys(defects) == ["roles[0].session"], defects


def test_the_decision_returns_full_for_the_owner_of_a_full_role():
    committed, local = files({"id": "manager", "actions": ["status"], "session": "full"})
    decision = S.decide(committed, local, OWNER_EVENT, NOW)
    assert decision.kind == S.INBOX
    assert decision.session == "full"


def test_a_role_with_no_session_field_is_bounded_for_the_owner():
    committed, local = files({"id": "manager", "actions": ["status"]})
    decision = S.decide(committed, local, OWNER_EVENT, NOW)
    assert decision.kind == S.INBOX
    assert decision.session == "bounded"


def test_an_owner_on_a_bounded_role_gets_bounded():
    committed, local = files({"id": "manager", "actions": ["status"], "session": "bounded"})
    assert S.decide(committed, local, OWNER_EVENT, NOW).session == "bounded"


def test_a_member_that_is_not_the_owner_gets_bounded_whatever_the_role_says():
    committed, local = files({"id": "manager", "actions": ["status"], "session": "full"})
    event = {**OWNER_EVENT, "owner": False}
    assert S.decide(committed, local, event, NOW).session == "bounded"


def test_a_decision_that_runs_no_turn_is_bounded():
    committed, local = files({"id": "manager", "actions": ["status"], "session": "full"})
    event = {**OWNER_EVENT, "bot": True}
    decision = S.decide(committed, local, event, NOW)
    assert decision.kind == S.DATA
    assert decision.session == "bounded"
