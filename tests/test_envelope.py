"""Ticket #98: the spawn request field `task` names the work item a child advances.

The task key is content, not identity: a model may supply it, and it narrows nothing, so it has no limit rule.
"""
from squad.envelope import validate_request
from tests.helpers import route


def disp():
    return route()


def test_a_task_key_normalizes_and_survives():
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "task": "14"}, 1, None, 10.0)
    assert errs == [] and req["task"] == "14"


def test_a_task_key_is_optional_and_none_when_absent():
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == [] and req["task"] is None


def test_a_request_with_no_effort_takes_the_engine_default_not_the_first_allowed():
    """The allow list is an order, never a default. Its first entry here is `low`, so a router that left `effort` out
    got the weakest level while the engine's own default is medium (the owner's ruling)."""
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0, default_effort="medium")
    assert errs == [] and req["effort"] == "medium"


def test_the_first_allowed_entry_leads_when_the_default_is_not_allowed():
    """A default the envelope does not allow narrows nothing: the list's first entry still leads."""
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0, default_effort="max")
    assert errs == [] and req["effort"] == "low"


def test_an_effort_above_the_allow_narrows_to_the_highest_allowed():
    """#960: the router named `high` under an envelope that allows low and medium, and the whole run died `no_dispatch`
    (two runs on 2026-10-31). A thinking depth a model guesses narrows to the envelope's highest allowed level; it never
    costs the run."""
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "effort": "high"}, 1, None, 10.0)
    assert errs == [] and req["effort"] == "medium"


def test_a_named_level_the_ladder_does_not_know_narrows_to_the_highest_allowed():
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "effort": "maximum"}, 1, None, 10.0)
    assert errs == [] and req["effort"] == "medium"


def test_an_effort_inside_the_allow_stays_as_named():
    req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "effort": "low"}, 1, None, 10.0)
    assert errs == [] and req["effort"] == "low"


def test_a_task_key_carries_no_limit_rule_of_its_own():
    # a task key narrows nothing, so a request is never rejected for the field itself, whatever its value
    for task in ("14", "issues/14-slug.md", "a" * 200):
        req, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "task": task}, 1, None, 10.0)
        assert errs == [] and req["task"] == task


# --- a dispatch skill version is a hash ---
HASH = "a" * 64
OTHER = "b" * 64


def pinned_disp(monkeypatch, pin, current=HASH):
    monkeypatch.setattr("squad.envelope.skills.hash_of", lambda name, vendor=None: current)
    d = disp()
    d["allow"]["skills"] = [{"name": "tdd", **({"version": pin} if pin else {})}]
    return d


def test_a_pin_that_matches_the_current_hash_passes(monkeypatch):
    req, errs = validate_request(pinned_disp(monkeypatch, HASH), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == [] and req["skill"] == {"name": "tdd", "version": HASH}


def test_a_pin_that_differs_from_the_current_hash_is_rejected(monkeypatch):
    _, errs = validate_request(pinned_disp(monkeypatch, OTHER), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == ["skill_pin_mismatch:tdd"]
    _, errs = validate_request(pinned_disp(monkeypatch, HASH), {"prompt": "go", "skill": {"name": "tdd", "version": OTHER}}, 1, None, 10.0)
    assert "skill_outside_allow" in errs


def test_a_missing_pin_floats_to_the_current_hash(monkeypatch):
    d = pinned_disp(monkeypatch, None)
    for raw in ("tdd", {"name": "tdd"}):
        req, errs = validate_request(d, {"prompt": "go", "skill": raw}, 1, None, 10.0)
        assert errs == [] and req["skill"]["version"] == HASH


# --- #1607: an omitted or unknown skill is a guess, never a lost run ---


def test_a_request_with_no_skill_takes_the_envelopes_first_allowed_skill():
    """A router that left `skill` out died the whole run `no_dispatch` with nothing delivered (#1607). Every other
    optional field falls back to the envelope, and `workspace` takes `allow.workspaces[0]`; skill takes the first
    allowed skill the same way."""
    req, errs = validate_request(disp(), {"prompt": "go"}, 1, None, 10.0)
    assert errs == [] and req["skill"]["name"] == "tdd"


def test_a_named_skill_outside_a_one_entry_allow_narrows_to_that_entry():
    """A named skill the allow list does not carry narrows to the envelope's first allowed skill when the list holds
    exactly one entry: the list proves the single intent. Today it fails `skill_outside_allow`."""
    d = disp()
    d["allow"]["skills"] = [{"name": "tdd"}]
    req, errs = validate_request(d, {"prompt": "go", "skill": "evil"}, 1, None, 10.0)
    assert errs == [] and req["skill"]["name"] == "tdd"


def test_the_pin_check_follows_the_given_vendor_not_the_default(tmp_path):
    import json

    from squad import skills
    d = tmp_path / "skills" / "tdd"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("custom")
    h = skills.tree_hash(d)
    (tmp_path / "VERSIONS.json").write_text(json.dumps({"tdd": {"sha256": h, "source": "skill:tdd"}}))
    assert h != skills.hash_of("tdd")
    dp = disp()
    dp["allow"]["skills"] = [{"name": "tdd", "version": h}]
    _, errs = validate_request(dp, {"prompt": "go", "skill": "tdd"}, 1, None, 10.0, vendor=tmp_path)
    assert errs == []
