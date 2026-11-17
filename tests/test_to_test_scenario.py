"""The `/to-test-scenario` module (#1224): coverage of a manual scenario by a gui-test/1 script, and the issue plan.

Driven through the module's public contract only: a scenario path and a script path in, a coverage report and a plan
out. Nothing here touches GitHub (the publish path is a later slice and consumes the plan as data).
"""
import pytest

from squad.to_test_scenario import ScenarioError, build_plan, case_hash, coverage, read_scenario, read_script, record_hashes, script_steps
from tests.gui_test_helpers import script, step

SCENARIO = """\
# tiny

A tiny scenario.

| Field | Value |
|---|---|
| Tester | |

### 1.1 Launch

| ID | Precondition | Steps | Expected | [W] | [F] | Notes |
|---|---|---|---|---|---|---|
| launch.s0 | app not running | launch it with the pad | Home is on screen | | — | |
| launch.home | launch.s0 | press A | the library grid opens | | | |
| launch.return | launch.home | press B | Home is back, ring on the opener | | | |
"""

SECOND_BATCH = """
### 1.2 Pause

| ID | Precondition | Steps | Expected | [W] | [F] | Notes |
|---|---|---|---|---|---|---|
| pause.s0 | a game running | press the chord | the pause overlay opens | | — | |
| pause.ready | pause.s0 | press the chord again | the overlay closes and the game resumes | | — | |
| pause.resume | pause.ready | press the chord and then B | the overlay closes and the game resumes | | | |
| pause.t | pause.resume | quit the game from the overlay | Home is on screen with the ring on the opener | | — | |
"""


def scenario_file(tmp_path, text=SCENARIO, name="tiny.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def cases_of(text=SCENARIO, name="tiny.md"):
    import re
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and re.fullmatch(r"[a-z0-9.\-]+", cells[0] or ""):
            out[cells[0]] = cells
    return out


def hashed(sid, text=SCENARIO):
    cells = cases_of(text)[sid]
    return case_hash(cells[1], cells[2], cells[3])


def tiny_script(*, with_hashes=True, extra_step=False):
    """The script a conversion of SCENARIO would produce: one step per case, its content hash recorded."""
    setup = [step("launch.s0", role="setup", mode="manual", expect="Home is on screen")]
    steps = [step("launch.home", content_hash=hashed("launch.home")),
             step("launch.return", content_hash=hashed("launch.return"))]
    if extra_step:
        steps.append(step("launch.ghost", content_hash="0" * 64))
    if not with_hashes:
        for s in setup + steps:
            s.pop("content_hash", None)
    else:
        setup[0]["content_hash"] = hashed("launch.s0")
    return script() | {"batches": [{"id": "launch", "setup": setup, "teardown": [], "steps": steps}]}


# --- slice 1: coverage by content hash -----------------------------------------------------------

def test_a_scenario_and_its_script_cover_each_other(tmp_path):
    report = coverage(read_scenario(scenario_file(tmp_path)), tiny_script())
    assert report["reusable"] is True
    assert report["covered"] == ["launch.s0", "launch.home", "launch.return"]
    assert report["missing_steps"] == [] and report["steps_without_case"] == [] and report["stale"] == []
    assert (report["total_cases"], report["total_steps"]) == (3, 3)


def test_an_edited_case_with_an_unchanged_id_is_stale(tmp_path):
    edited = SCENARIO.replace("the library grid opens", "the library grid opens with cover tiles")
    report = coverage(read_scenario(scenario_file(tmp_path, edited)), tiny_script())
    assert report["stale"] == ["launch.home"]
    assert report["covered"] == ["launch.s0", "launch.home", "launch.return"]
    assert report["reusable"] is False


def test_a_case_with_no_step_and_a_step_with_no_case_are_both_reported(tmp_path):
    d = tiny_script(extra_step=True)
    d["batches"][0]["steps"] = [s for s in d["batches"][0]["steps"] if s["id"] != "launch.return"]
    report = coverage(read_scenario(scenario_file(tmp_path)), d)
    assert report["missing_steps"] == ["launch.return"]
    assert report["steps_without_case"] == ["launch.ghost"]
    assert report["reusable"] is False


def test_a_step_with_no_recorded_hash_is_not_reusable(tmp_path):
    report = coverage(read_scenario(scenario_file(tmp_path)), tiny_script(with_hashes=False))
    assert report["stale"] == ["launch.s0", "launch.home", "launch.return"]
    assert report["reusable"] is False


def test_recording_the_hashes_makes_an_otherwise_covered_script_reusable(tmp_path):
    scenario = read_scenario(scenario_file(tmp_path))
    recorded = record_hashes(scenario, tiny_script(with_hashes=False))
    assert coverage(scenario, recorded)["reusable"] is True
    assert tiny_script(with_hashes=False)["batches"][0]["steps"][0].get("content_hash") is None


def test_reading_a_scenario_keeps_only_the_case_tables(tmp_path):
    scenario = read_scenario(scenario_file(tmp_path))
    assert scenario["name"] == "tiny"
    assert [c["id"] for c in scenario["cases"]] == ["launch.s0", "launch.home", "launch.return"]
    assert scenario["cases"][0]["precondition"] == "app not running"
    assert scenario["cases"][0]["steps"] == "launch it with the pad"
    assert scenario["cases"][0]["expected"] == "Home is on screen"
    assert set(scenario["cases"][0]) == {"id", "precondition", "steps", "expected"}


def test_the_hash_ignores_surrounding_whitespace_and_line_breaks():
    assert case_hash(" a  b ", "c\nd", " e ") == case_hash("a b", "c d", "e")
    assert case_hash("a", "b", "c") != case_hash("a", "b", "c d")
    assert case_hash("a", "b", "c") != case_hash("a", "b", "C")


def test_reading_a_missing_script_says_there_is_none(tmp_path):
    assert read_script(tmp_path / "nowhere.gui-test.json") is None


def test_reading_a_script_checks_it_is_a_gui_test_script(tmp_path):
    p = tmp_path / "tiny.gui-test.json"
    p.write_text('{"format": "gui-test/9"}', encoding="utf-8")
    with pytest.raises(Exception) as err:
        read_script(p)
    assert "gui-test/9" in str(err.value)


def test_steps_are_listed_in_run_order_with_their_batch(tmp_path):
    d = tiny_script()
    d["batches"][0]["teardown"] = [step("launch.t", role="teardown", content_hash="x")]
    assert [(s["id"], s["batch"], s["part"]) for s in script_steps(d)] == [
        ("launch.s0", "launch", "setup"),
        ("launch.home", "launch", "steps"),
        ("launch.return", "launch", "steps"),
        ("launch.t", "launch", "teardown"),
    ]


# --- slice 2: the issue plan ---------------------------------------------------------------------

TWO_BATCHES = SCENARIO + SECOND_BATCH


def two_batch_script(*, with_hashes=True):
    """Two batches: launch (one setup case) and pause (two setup cases, to prove the chain between them)."""
    def s(sid, **over):
        if with_hashes:
            over["content_hash"] = hashed(sid, TWO_BATCHES)
        return step(sid, **over)

    return script() | {"batches": [
        {"id": "launch", "setup": [s("launch.s0", role="setup")], "teardown": [],
         "steps": [s("launch.home"), s("launch.return")]},
        {"id": "pause", "setup": [s("pause.s0", role="setup"), s("pause.ready", role="setup")],
         "teardown": [s("pause.t", role="teardown")], "steps": [s("pause.resume")]},
    ]}


def two_batch_scenario(tmp_path):
    return read_scenario(scenario_file(tmp_path, TWO_BATCHES))


def plan_of(tmp_path, **kw):
    return build_plan(two_batch_scenario(tmp_path), two_batch_script(), **kw)


def test_the_per_case_plan_has_one_parent_and_one_issue_per_step_in_run_order(tmp_path):
    plan = plan_of(tmp_path)
    assert plan["parent"]["title"] == "Test scenario: tiny"
    assert [i["id"] for i in plan["issues"]] == [
        "launch.s0", "launch.home", "launch.return", "pause.s0", "pause.ready", "pause.resume", "pause.t"]
    assert [i["title"] for i in plan["issues"]][:2] == ["Test case launch.s0", "Test case launch.home"]
    assert all(i["kind"] == "case" for i in plan["issues"])


def test_the_parent_is_ready_for_agent_and_no_issue_carries_a_wave(tmp_path):
    plan = plan_of(tmp_path)
    assert plan["parent"]["labels"] == ["ready-for-agent", "Test-Scenario"]
    for issue in plan["issues"]:
        assert issue["labels"] == ["Test-Case"]
        assert "ready-for-agent" not in issue["labels"]
        assert not any(label.startswith("wave:") for label in issue["labels"])
    assert all("wave" not in issue for issue in plan["issues"]), "the plan carries no wave dimension"


def test_a_case_names_its_setup_case_in_blocked_by(tmp_path):
    plan = plan_of(tmp_path)
    edges = {i["id"]: i["blocked_by"] for i in plan["issues"]}
    assert edges == {"launch.s0": None, "launch.home": "launch.s0", "launch.return": "launch.s0",
                     "pause.s0": None, "pause.ready": "pause.s0", "pause.t": "pause.ready",
                     "pause.resume": "pause.ready"}
    bodies = {i["id"]: i["body"] for i in plan["issues"]}
    assert "## Blocked by\n\n- Case `launch.s0`" in bodies["launch.home"]
    assert "## Blocked by\n\n- None (can start immediately)." in bodies["launch.s0"]


def test_a_case_issue_carries_the_case_text_a_reader_needs(tmp_path):
    body = {i["id"]: i["body"] for i in plan_of(tmp_path)["issues"]}["launch.home"]
    for expected in ("`launch.home`", "press A", "the library grid opens", "`launch`", "`under-test`", "`automated`"):
        assert expected in body


def test_the_per_batch_plan_has_one_issue_per_batch_in_script_order(tmp_path):
    plan = plan_of(tmp_path, granularity="batch")
    assert [i["id"] for i in plan["issues"]] == ["launch", "pause"]
    assert all(i["kind"] == "batch" and i["labels"] == ["Test-Case"] for i in plan["issues"])
    assert plan["issues"][0]["title"] == "Test case batch launch"
    for cid in ("launch.s0", "launch.home", "launch.return"):
        assert f"`{cid}`" in plan["issues"][0]["body"]
    assert "`pause.s0`" not in plan["issues"][0]["body"]


def test_the_parent_body_names_the_script_its_fixtures_its_variants_and_every_case(tmp_path):
    body = plan_of(tmp_path)["parent"]["body"]
    assert "## Script" in body and "`tiny`" in body and "`gui-test/1`" in body
    assert "## Fixtures" in body and "## Variants" in body and "`window.mode`" in body
    assert "## Cases" in body and "| `launch.home` | `launch` |" in body
    assert "## Blocked by" in body


def test_every_string_the_plan_will_publish_is_en_us(tmp_path):
    def strings(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
        elif isinstance(value, str):
            yield value

    for text in strings(plan_of(tmp_path, granularity="batch")):
        assert text.encode("ascii"), text


def test_the_plan_refuses_a_script_that_does_not_cover_the_scenario(tmp_path):
    scenario = two_batch_scenario(tmp_path)
    edited = scenario | {"cases": [dict(c, expected=c["expected"] + " (edited)") if c["id"] == "launch.home" else c
                                   for c in scenario["cases"]]}
    with pytest.raises(ScenarioError) as err:
        build_plan(edited, two_batch_script())
    assert "stale" in str(err.value) and "launch.home" in str(err.value)

    short = two_batch_script()
    short["batches"][0]["steps"] = short["batches"][0]["steps"][:1]
    with pytest.raises(ScenarioError) as err:
        build_plan(scenario, short)
    assert "no step" in str(err.value) and "launch.return" in str(err.value)

    extra = two_batch_script()
    extra["batches"][0]["steps"].append(step("launch.ghost", content_hash="0" * 64))
    with pytest.raises(ScenarioError) as err:
        build_plan(scenario, extra)
    assert "no case" in str(err.value) and "launch.ghost" in str(err.value)
