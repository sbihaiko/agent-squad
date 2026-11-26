"""The hub registry holds one owner user id per workspace, and the hub route writes it (#2966, ADR-0180).

The owner of a Slack workspace is the one person a `session: full` role admits, so the owner id lives in the hub
registry beside the saved shas. This file guards the route that writes that id: one id per workspace, an id that is a
Slack user id, and a read that names the id the registry holds. No test calls Slack.
"""
import json

import pytest

from tests import test_hub_slack_clients as C

hub = C.hub   # the shared module fixture, registered here under its own name

TEAM = "T0TEAM0001"
OTHER = "T0TEAM0002"
OWNER = "U0OWNER001"


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A state directory in force for the registry the route writes."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


def registry(hub):
    return json.loads(hub.slack_registry_path().read_text())


def exists(hub):
    return hub.slack_registry_path().exists()


def test_a_hub_write_records_one_owner_user_id_for_a_workspace(hub, state):
    out = hub.slack_owner_post({"workspace": TEAM, "user": OWNER})
    assert out["err"] is None, out
    assert registry(hub)["owners"] == {TEAM: OWNER}


def test_each_workspace_keeps_its_own_owner(hub, state):
    hub.slack_owner_post({"workspace": TEAM, "user": OWNER})
    out = hub.slack_owner_post({"workspace": OTHER, "user": "U0OWNER002"})
    assert out["err"] is None, out
    assert registry(hub)["owners"] == {TEAM: OWNER, OTHER: "U0OWNER002"}


def test_a_second_write_for_a_workspace_replaces_its_owner(hub, state):
    hub.slack_owner_post({"workspace": TEAM, "user": OWNER})
    hub.slack_owner_post({"workspace": TEAM, "user": "U0OWNER002"})
    assert registry(hub)["owners"] == {TEAM: "U0OWNER002"}


def test_a_value_that_is_not_a_slack_user_id_is_refused_and_writes_nothing(hub, state):
    out = hub.slack_owner_post({"workspace": TEAM, "user": "bob"})
    assert "err" in out, out
    assert not exists(hub)


def test_a_value_that_is_not_a_slack_workspace_id_is_refused_and_writes_nothing(hub, state):
    """A registry key no event can match is a silent failure, so the route refuses a workspace that is not an id."""
    out = hub.slack_owner_post({"workspace": "acme", "user": OWNER})
    assert "err" in out, out
    assert not exists(hub)


def test_a_read_names_the_owner_the_registry_holds(hub, state):
    hub.slack_owner_post({"workspace": TEAM, "user": OWNER})
    out = hub.slack_owner_post({"workspace": TEAM})
    assert out["err"] is None, out
    assert out["owner"] == OWNER
