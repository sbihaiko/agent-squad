"""The hub route writes each grant of `full` into the hub registry (#2971, ADR-0180).

A grant lets a person other than the workspace owner run a full session, so the grant lives in the hub registry beside
the owner id. This file guards the route that writes a grant: one `{workspace, user}` pair per grant, a Slack id on each
side, and no duplicate row. No test calls Slack.
"""
import json

import pytest

from tests import test_hub_slack_clients as C

hub = C.hub   # the shared module fixture, registered here under its own name

TEAM = "T0TEAM0001"
OWNER = "U0OWNER001"
GUEST = "U0GUEST001"


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


def test_a_hub_write_records_a_full_grant_in_the_registry(hub, state):
    out = hub.slack_grant_post({"workspace": TEAM, "user": GUEST})
    assert out["err"] is None, out
    assert registry(hub)["grants"] == [{"workspace": TEAM, "user": GUEST}]


def test_a_grant_keeps_the_owner_the_registry_holds(hub, state):
    hub.slack_owner_post({"workspace": TEAM, "user": OWNER})
    hub.slack_grant_post({"workspace": TEAM, "user": GUEST})
    doc = registry(hub)
    assert doc["owners"] == {TEAM: OWNER}, "the grant write dropped the owner id"
    assert doc["grants"] == [{"workspace": TEAM, "user": GUEST}]


def test_a_repeated_grant_is_kept_once(hub, state):
    hub.slack_grant_post({"workspace": TEAM, "user": GUEST})
    hub.slack_grant_post({"workspace": TEAM, "user": GUEST})
    assert registry(hub)["grants"] == [{"workspace": TEAM, "user": GUEST}]


def test_a_grant_for_a_value_that_is_not_a_slack_user_id_is_refused_and_writes_nothing(hub, state):
    out = hub.slack_grant_post({"workspace": TEAM, "user": "bob"})
    assert out["err"], out
    assert not exists(hub)


def test_a_grant_for_a_value_that_is_not_a_slack_workspace_id_is_refused_and_writes_nothing(hub, state):
    out = hub.slack_grant_post({"workspace": "acme", "user": GUEST})
    assert out["err"], out
    assert not exists(hub)
