"""ADR-0275 slice A (MesenAI #1472): the plan is a tree of areas, containers and case issues.

Driven through `build_plan` and the `publish` command only; no network, nothing written.
"""
import pytest

from squad import to_test_scenario as tts
from tests.test_gui_test_scenario import MARKDOWN, FakeGitHub, _step, covered_script, script

# #1286's batches and their step counts, setup and teardown included (ADR-0275 section 2).
PAD_ONLY = [("launch", 3), ("home", 11), ("home-after-game", 12), ("library", 35), ("favorites", 8),
            ("favorites-home", 8), ("game", 5), ("game-archive", 4), ("game-bios", 4), ("dialogs", 1),
            ("pause", 60), ("settings", 57), ("pause-pack-multi", 3), ("settings-menu-sounds", 2),
            ("controller", 11), ("controller-done", 3), ("controller-b", 3), ("controller-no-game", 1),
            ("lamps", 2), ("lamps-game", 1), ("loss", 4), ("fullscreen", 5), ("feel", 3), ("trap", 4),
            ("journey", 2), ("two-pads", 4)]


def batch(bid, count, tag=None, **over):
    tag = tag or bid.upper().replace("-", "")
    b = {"id": bid, "setup": [], "teardown": [],
         "steps": [_step(f"{tag}-{n:03d}") for n in range(1, count + 1)]}
    b.update(over)
    return b


def nodes(plan):
    """Every node's sub-issue count: the scenario, each area, each container."""
    out = [len(plan["areas"])]
    for area in plan["areas"]:
        out.append(len(area["containers"]))
        out += [len(c["cases"]) for c in area["containers"]]
    return out


def test_the_pad_only_script_plans_16_areas_and_no_node_exceeds_100():
    plan = tts.build_plan(script(batches=[batch(b, n) for b, n in PAD_ONLY]))
    assert [a["key"] for a in plan["areas"]] == [
        "launch", "home", "library", "favorites", "game", "dialogs", "pause", "settings", "controller",
        "lamps", "loss", "fullscreen", "feel", "trap", "journey", "two"]
    assert max(nodes(plan)) <= 100
    assert sum(len(c["cases"]) for a in plan["areas"] for c in a["containers"]) == len(plan["issues"]) == sum(n for _, n in PAD_ONLY)


def test_flat_issues_are_the_same_objects_in_run_order():
    plan = tts.build_plan(script(batches=[batch("pause", 2), batch("home", 1), batch("pause-pack", 1)]))
    tree = [i for a in plan["areas"] for c in a["containers"] for i in c["cases"]]
    assert sorted(id(i) for i in tree) == sorted(id(i) for i in plan["issues"])
    assert [i["batch"] for i in plan["issues"]] == ["pause", "pause", "home", "pause-pack"]


def test_explicit_area_and_objective_win_over_the_prefix():
    plan = tts.build_plan(script(batches=[
        batch("two-pads", 1, area="multiplayer", objective="Two pads share one session."),
        batch("two-more", 1, area="multiplayer", objective="ignored: the first one found wins"),
        batch("pause-a", 1)]))
    assert [a["key"] for a in plan["areas"]] == ["multiplayer", "pause"]
    first = plan["areas"][0]
    assert first["objective"] == "Two pads share one session." and "Two pads share one session." in first["body"]
    assert plan["areas"][1]["objective"] == "(no objective stated)"
    assert first["title"] == "Area: multiplayer" and len(first["containers"][0]["cases"]) == 2


def test_cases_table_truncates_expect_to_160_characters():
    long = "x" * 400
    plan = tts.build_plan(script(
        batches=[{"id": "home", "setup": [], "teardown": [], "steps": [_step("HOME-001", expect=long)]}]))
    body = plan["areas"][0]["body"]
    assert "## Cases" in body and "`HOME-001`" in body
    assert "x" * 157 + "..." in body and "x" * 158 not in body


def test_a_container_splits_by_batch_and_never_inside_a_batch():
    plan = tts.build_plan(script(batches=[batch("pause-a", 60), batch("pause-b", 60)]))
    containers = plan["areas"][0]["containers"]
    assert [c["title"] for c in containers] == ["Cases (1/2)", "Cases (2/2)"]
    assert [len(c["cases"]) for c in containers] == [60, 60]
    assert plan["areas"][0]["containers"][0]["cases"][0]["batch"] == "pause-a"
    single = tts.build_plan(script(batches=[batch("pause-a", 60)]))
    assert [c["title"] for c in single["areas"][0]["containers"]] == ["Cases"]


def test_per_batch_granularity_puts_batch_issues_in_the_container():
    plan = tts.build_plan(script(batches=[batch("pause-a", 3), batch("pause-b", 2)]), granularity="per-batch")
    cases = plan["areas"][0]["containers"][0]["cases"]
    assert [i["batch"] for i in cases] == ["pause-a", "pause-b"] and cases == plan["issues"]


def test_a_batch_over_100_cases_is_refused_naming_the_batch():
    with pytest.raises(tts.ScenarioError) as err:
        tts.build_plan(script(batches=[batch("pause", 101)]))
    assert "pause" in str(err.value.problems) and "100" in str(err.value.problems)


def test_an_area_body_over_60000_characters_is_refused_naming_the_area():
    batches = [batch(f"pause-{n}", 100, tag=f"P{n}", **{}) for n in range(8)]
    for b in batches:
        for s in b["steps"]:
            s["expect"] = "y" * 300
    with pytest.raises(tts.ScenarioError) as err:
        tts.build_plan(script(batches=batches))
    assert "pause" in str(err.value.problems) and "60000" in str(err.value.problems)


def test_publishing_a_101_case_batch_writes_nothing(tmp_path, capsys):
    rows = "\n".join(f"| HOME-{n:03d} | Fresh | Do it. | Done. | | | |" for n in range(1, 102))
    md = tmp_path / "s.md"
    md.write_text("# S\n\n### 1. Home\n\n| ID | Precondition | Steps | Expected | [W] | [F] | Notes |\n"
                  "|---|---|---|---|---|---|---|\n" + rows + "\n", encoding="utf-8")
    path = tmp_path / "s.gui-test.json"
    path.write_text(tts.json.dumps(script(batches=[batch("home", 101, tag="HOME")])), encoding="utf-8")
    gh = FakeGitHub()
    assert tts.main(["publish", str(md), str(path), "--stamp"], github=gh) == 1
    problems = tts.json.loads(capsys.readouterr().out)["problems"]
    assert "home" in str(problems) and "100" in str(problems)
    assert gh.calls == [] and not tts.issues_path(path).exists()


def test_the_scenario_plan_carries_the_same_area_tree(tmp_path):
    md = tmp_path / "s.md"
    md.write_text(MARKDOWN, encoding="utf-8")
    scenario = tts.read_scenario(md)
    plan = tts.build_plan(scenario, tts.record_hashes(scenario, covered_script()))
    assert [a["key"] for a in plan["areas"]] == ["launch", "home", "library"]
    tree = [i for a in plan["areas"] for c in a["containers"] for i in c["cases"]]
    assert [id(i) for i in tree] == [id(i) for i in plan["issues"]]
