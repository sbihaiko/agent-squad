import pytest

from tests.gui_test_helpers import script, step
from squad.gui_test import ScriptError, validate


def reasons(doc):
    with pytest.raises(ScriptError) as e:
        validate(doc)
    return " | ".join(e.value.problems)


def test_well_formed_script_is_accepted():
    validate(script(step("b1.one"), step("b1.two")))


def test_unknown_format_is_refused_not_guessed():
    assert "unknown format" in reasons(script(format="gui-test/2"))


def test_missing_format_is_refused():
    d = script()
    del d["format"]
    assert "unknown format" in reasons(d)


def test_duplicate_step_id():
    assert "duplicate step id b1.one" in reasons(script(step("b1.one"), step("b1.one")))


def test_duplicate_step_id_across_batches():
    d = script(step("x.a"))
    d["batches"].append({"id": "b2", "setup": [], "teardown": [], "steps": [step("x.a")]})
    assert "duplicate step id x.a" in reasons(d)


@pytest.mark.parametrize("field", ["id", "role", "precondition", "action", "wait",
                                   "expect", "severity", "mode"])
def test_missing_step_field(field):
    s = step("b1.one")
    del s[field]
    assert f"missing field {field}" in reasons(script(s))


def test_missing_top_level_field():
    d = script()
    del d["target"]
    assert "missing field target" in reasons(d)


def test_automated_step_without_wait_condition():
    s = step("b1.one")
    del s["wait"]
    assert "b1.one: missing field wait" in reasons(script(s))


def test_wait_without_timeout():
    s = step("b1.one", wait={"check": "ui.focused == a"})
    assert "b1.one: wait needs timeout_frames or timeout_ticks" in reasons(script(s))


def test_wait_with_both_timeouts():
    s = step("b1.one", wait={"timeout_ticks": 3, "timeout_frames": 3})
    assert "exactly one" in reasons(script(s))


def test_wait_timeout_must_be_positive_int():
    s = step("b1.one", wait={"timeout_ticks": 0})
    assert "positive integer" in reasons(script(s))


def test_manual_step_needs_no_action_or_wait():
    s = {"id": "b1.m", "role": "under-test", "precondition": "p", "expect": "look",
         "severity": "minor", "mode": "manual"}
    validate(script(s))


def test_undeclared_variant_value():
    s = step("b1.one", variants={"window.mode": "tiny"})
    assert "variant window.mode=tiny is not declared" in reasons(script(s))


def test_undeclared_variant_axis():
    s = step("b1.one", variants={"pad.model": "xbox"})
    assert "variant axis pad.model is not declared" in reasons(script(s))


def test_nav_goal_as_action_under_test():
    s = step("b1.one", action={"name": "nav.goal", "args": {"goal": "x"}})
    assert "nav.goal is not allowed on a step under test" in reasons(script(s))


def test_nav_goal_allowed_in_setup():
    d = script()
    d["batches"][0]["setup"] = [step("b1.setup", role="setup",
                                     action={"name": "nav.goal", "args": {"goal": "x"}})]
    validate(d)


def test_closed_namespaces():
    s = step("b1.one", action={"name": "gamepad.tap", "args": {}})
    assert "unknown action namespace gamepad" in reasons(script(s))
    s = step("b1.one", check={"name": "emu.ram", "args": {}})
    assert "emu.ram is reserved" in reasons(script(s))


def test_names_must_be_declared_in_requires():
    s = step("b1.one", check={"name": "fs.exists", "args": {"path": "x"}})
    assert "check fs.exists is not in requires" in reasons(script(s))


def test_adapter_capabilities_are_compared():
    caps = {"actions": ["pad.press"], "checks": ["ui.screen"], "variants": {"window.mode": ["windowed"]}}
    msg = reasons_caps(script(), caps)
    assert "adapter does not advertise check ui.focused" in msg
    assert "adapter does not advertise variant window.mode=fullscreen" in msg


def reasons_caps(doc, caps):
    with pytest.raises(ScriptError) as e:
        validate(doc, capabilities=caps)
    return " | ".join(e.value.problems)


def test_unit_must_match_one_unit_per_step():
    s = step("b1.one", action={"name": "pad.press", "args": {"button": "A", "ticks": 1, "frames": 1}})
    assert "ticks and frames" in reasons(script(s))


@pytest.mark.parametrize("args,wait_key,family", [
    ({"frames": 4}, "timeout_ticks", "frames"),
    ({"ticks": 4}, "timeout_frames", "ticks"),
])
def test_action_family_must_match_wait_family(args, wait_key, family):
    s = step("b1.one", action={"name": "pad.press", "args": {"button": "A", **args}},
             wait={"check": "ui.focused == a", wait_key: 4})
    assert f"b1.one: action counts {family} but wait uses {wait_key}" in reasons(script(s))


def test_matching_families_are_accepted():
    validate(script(step("b1.one", action={"name": "pad.press", "args": {"button": "A", "frames": 4}},
                         wait={"check": "ui.focused == a", "timeout_frames": 4})))


def test_action_without_a_family_is_not_compared():
    validate(script(step("b1.one", action={"name": "nav.goal", "args": {"goal": "ui.screen == play.home"}},
                         role="setup", wait={"check": "ui.screen == play.home", "timeout_frames": 600})))
