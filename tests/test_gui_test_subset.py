"""The runnable-subset derivation (MesenAI #1242): a script narrowed to what an adapter advertises.

The full pad-only pilot script names `pad.release`, `pad.chord`, `nav.goal`, `nav.sweep` and the `fullscreen` window
mode, none of which the `mesen-gui` adapter advertises, so the run command refuses it before launch. The derivation
never drops a step: what the adapter cannot be asked to run becomes `manual`, and its `expect` text names the
capability that is missing.
"""
import copy

from squad.gui_test import validate
from squad.gui_test_subset import missing_capabilities, runnable
from tests.gui_test_helpers import script, step

CAPS = {"actions": ["pad.press"], "checks": ["ui.focused", "ui.screen"],
        "input_families": ["pad"], "variants": {"window.mode": ["windowed"]}}


def _chord(sid="b1.chord"):
    return step(sid, action={"name": "pad.chord", "args": {"buttons": ["Select", "Start"], "ticks": 4}},
                wait={"timeout_ticks": 4}, check={"name": "ui.focused", "args": {"is": "play.home.open-rom"}})


def test_a_step_naming_an_unadvertised_action_becomes_manual_and_the_result_validates():
    d = script(step("b1.keep"), _chord())
    out = runnable(d, CAPS)
    validate(out["script"], CAPS)  # no rejection: the run command would now accept it
    assert [s["mode"] for s in out["script"]["batches"][0]["steps"]] == ["automated", "manual"]


def test_a_converted_step_keeps_its_case_and_names_the_capability_it_is_missing():
    out = runnable(script(step("b1.keep"), _chord()), CAPS)
    converted = out["script"]["batches"][0]["steps"][1]
    assert converted["id"] == "b1.chord" and "action" not in converted and "wait" not in converted
    assert "pad.chord" in converted["expect"]
    assert out["manual"] == [{"batch": "b1", "part": "steps", "id": "b1.chord", "missing": ["action pad.chord"]}]


def test_the_derivation_drops_no_step_and_leaves_an_advertised_step_untouched():
    d = script(step("b1.keep"), _chord(), step("b1.also-keep", role="setup"))
    out = runnable(d, CAPS)
    assert [s["id"] for s in out["script"]["batches"][0]["steps"]] == ["b1.keep", "b1.chord", "b1.also-keep"]
    assert out["script"]["batches"][0]["steps"][0] == d["batches"][0]["steps"][0]
    assert d["batches"][0]["steps"][1]["action"]["name"] == "pad.chord"  # the input script is not mutated


def test_requires_narrows_to_what_the_surviving_automated_steps_use():
    out = runnable(script(step("b1.keep"), _chord()), CAPS)
    assert out["script"]["requires"] == {"actions": ["pad.press"], "checks": ["ui.focused"]}


def test_an_unadvertised_variant_value_narrows_the_axis_and_makes_its_steps_manual():
    d = script(step("b1.fs", variants={"window.mode": "fullscreen"}),
               step("b1.w", variants={"window.mode": "windowed"}))
    out = runnable(d, CAPS)
    assert out["script"]["variants"] == {"window.mode": ["windowed"]}
    assert out["script"]["batches"][0]["steps"][0]["mode"] == "manual"
    assert out["manual"][0]["missing"] == ["variant window.mode=fullscreen"]
    assert out["script"]["batches"][0]["steps"][1]["variants"] == {"window.mode": "windowed"}


def test_an_axis_with_no_advertised_value_left_is_dropped_so_the_run_still_runs_once():
    caps = {"actions": ["pad.press"], "checks": ["ui.focused", "ui.screen"], "variants": {}}
    out = runnable(script(step("b1.fs", variants={"window.mode": "fullscreen"})), caps)
    assert out["script"]["variants"] == {}


def test_a_check_the_adapter_does_not_advertise_is_named_as_the_missing_capability():
    d = script(step("b1.log", check={"name": "log.contains", "args": {"is": "x"}}, wait={"timeout_ticks": 2}))
    assert missing_capabilities(copy.deepcopy(d["batches"][0]["steps"][0]), CAPS) == ["check log.contains"]


# ------------------------------------------------------------------------------------------------
# MesenAI #1242: no case id of the Markdown scenario may be lost when a step becomes manual.

from squad.gui_test_coverage import coverage, scenario_case_ids  # noqa: E402

SCENARIO = """| ID | Precondition | Steps | Expected | [W] | [F] | Notes |
|---|---|---|---|---|---|---|
| HOME-01 | fresh | look | home | | — | |
| P4-04 **(pass 3)** | W-P4 | switch pad | footer re-reads | | | |
"""


def test_the_scenario_ids_are_read_even_when_the_case_carries_a_tag_in_its_cell():
    assert scenario_case_ids(SCENARIO) == ["HOME-01", "P4-04"]


def test_a_case_no_step_references_is_a_gap_and_a_step_naming_an_undefined_case_is_a_typo():
    d = script(step("b1.look", precondition="HOME-01", expect="HOME-01 is up"))
    assert coverage(SCENARIO, d) == {"missing": ["P4-04"], "unknown": []}
    typo = script(step("b1.look", precondition="HOME-01", expect="HOME-09 and P4-04"))
    assert coverage(SCENARIO, typo) == {"missing": [], "unknown": ["HOME-09"]}


def test_an_automated_step_and_the_manual_step_it_became_reference_the_same_case():
    d = script(_chord())
    narrowed = runnable(d, CAPS)["script"]
    assert coverage(SCENARIO, narrowed)["missing"] == ["HOME-01", "P4-04"]  # nothing added, nothing dropped


def test_a_check_of_an_id_the_application_does_not_report_is_a_missing_capability_too():
    # The home's own ids are real; a library tile's is not (the app reports play.home, play.home.continue,
    # play.home.open-rom and play.library today), and a check of it can only ever be `failed (unknown id)`.
    caps = dict(CAPS, controls=["play.home", "play.home.continue", "play.home.open-rom", "play.library"])
    real = step("b1.real", check={"name": "ui.screen", "args": {"is": "play.home"}},
                wait={"check": "ui.screen == play.home", "timeout_ticks": 4})
    unreal = step("b1.unreal", check={"name": "ui.focused", "args": {"is": "play.library.tile"}},
                  wait={"check": "ui.focused == play.library.tile", "timeout_ticks": 4})
    assert missing_capabilities(real, caps) == []
    assert missing_capabilities(unreal, caps) == ["control play.library.tile"]
    assert missing_capabilities(unreal, CAPS) == []  # no `controls` advertised: the ids are not this module's claim
    out = runnable(script(real, unreal), caps)
    validate(out["script"], caps)
    assert out["script"]["batches"][0]["steps"][1]["mode"] == "manual"
    assert "play.library.tile" in out["script"]["batches"][0]["steps"][1]["expect"]


def test_a_precondition_naming_an_id_nothing_reports_makes_the_step_manual():
    # The runner checks a precondition before the step and fails the batch when it does not hold, so a precondition
    # on a sub-surface nothing reports (`play.library.search`) is not a step this adapter can be asked to run.
    caps = dict(CAPS, controls=["play.home", "play.library"])
    inside = step("b1.inside", precondition="ui.focused == play.library.search",
                  check={"name": "ui.screen", "args": {"is": "play.library"}},
                  wait={"check": "ui.screen == play.library", "timeout_ticks": 4})
    assert missing_capabilities(inside, caps) == ["control play.library.search"]
    out = runnable(script(inside), caps)
    assert out["script"]["batches"][0]["steps"][0]["mode"] == "manual"
    assert out["script"]["batches"][0]["steps"][0]["precondition"] == "ui.focused == play.library.search"


def test_a_batch_whose_setup_the_adapter_cannot_run_takes_its_automated_steps_with_it():
    d = script(step("b1.assert"))
    d["batches"][0]["setup"] = [step("b1.entry", action={"name": "nav.goal", "args": {"goal": "play.library"}},
                                     wait={"timeout_ticks": 4})]
    out = runnable(d, CAPS)
    converted = out["script"]["batches"][0]["steps"][0]
    assert converted["mode"] == "manual"
    assert out["manual"][-1] == {"batch": "b1", "part": "steps", "id": "b1.assert",
                                 "missing": ["batch setup b1.entry is manual, so nothing established the state "
                                             "this step asserts"]}
    assert "batch setup b1.entry is manual" in converted["expect"]
    assert out["script"]["requires"] == {"actions": [], "checks": []}
    validate(out["script"], CAPS)


def test_a_dialog_id_and_a_button_name_are_read_apart():
    caps = dict(CAPS, checks=CAPS["checks"] + ["ui.dialogs"], controls=["play.home"])
    named = step("b1.chord", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
                 check={"name": "ui.dialogs", "args": {"is": ["play.controller-sheet"]}}, wait={"timeout_ticks": 4})
    assert missing_capabilities(named, caps) == ["control play.controller-sheet"]
