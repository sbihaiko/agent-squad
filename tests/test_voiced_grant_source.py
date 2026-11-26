"""voiced reads the grants of `full` from the hub registry and from no other source (#2971, ADR-0180).

The grants come from one file, the hub registry the path names. A grant written anywhere else, such as a `grants` key
in a local Slack file, is never read as a grant.
"""
import json

from tests.test_voiced import TEAM
from tests.test_voiced import voiced as load_voiced

GUEST = "U0GUEST001"


def test_voiced_reads_a_full_grant_from_the_hub_registry(tmp_path):
    registry = tmp_path / "slack-saved.json"
    registry.write_text(json.dumps({"grants": [{"workspace": TEAM, "user": GUEST}]}))
    assert load_voiced().registry_grants(str(registry)) == [{"workspace": TEAM, "user": GUEST}]


def test_voiced_reads_no_grant_when_no_registry_is_named(tmp_path):
    mod = load_voiced()
    assert mod.registry_grants("") == []
    assert mod.registry_grants(str(tmp_path / "missing.json")) == []


def test_a_grants_key_beside_the_registry_is_not_a_grant_source(tmp_path):
    local = tmp_path / "slack.local.yaml"
    local.write_text(f"grants:\n  - workspace: {TEAM}\n    user: {GUEST}\n")
    registry = tmp_path / "slack-saved.json"
    registry.write_text(json.dumps({"owners": {TEAM: "U0OWNER001"}}))
    assert load_voiced().registry_grants(str(registry)) == []
