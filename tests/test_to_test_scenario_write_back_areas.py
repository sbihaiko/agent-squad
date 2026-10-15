"""ADR-0275 slice E (MesenAI #1476): `write_back` makes one run issue per area and rewrites the dashboards.

Driven through `publish` (to build a real areas map) and `write_back`; the runner models GitHub's issues and
sub-issue tree, so a duplicate container or an unclosed run issue is an observable failure. No network.
"""
import json
import re

import pytest

from squad import to_test_scenario as tts
from tests.test_gui_test_scenario import script
from tests.test_to_test_scenario_areas import batch
from tests.test_to_test_scenario_tree_publish import TreeGitHub

START, END = tts.DASHBOARD_START, tts.DASHBOARD_END
RUN_BLOCK = re.compile(r"<!-- gui-test:run (\{.*\}) -->")


class RunGitHub(TreeGitHub):
    """The tree runner plus what a write-back needs: labels, closing and a body-only update."""

    def __call__(self, op, **kw):
        if op == "create_issue":
            out = super().__call__(op, **kw)
            self.issues[out["number"]]["labels"] = kw.get("labels")
            return out
        if op == "close":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["state"] = "closed"
            return {}
        if op == "set_body":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["body"] = kw["body"]
            return {}
        return super().__call__(op, **kw)

    def runs(self, area_number):
        """Every run issue under an area's 📅 containers, as (number, issue)."""
        return [(n, self.issues[n]) for c in self.kids.get(area_number, []) if self.issues[c]["title"].startswith("📅")
                for n in self.kids.get(c, [])]


def block(issue):
    return json.loads(RUN_BLOCK.search(issue["body"]).group(1))


@pytest.fixture
def tree(tmp_path):
    plan = tts.build_plan(script(batches=[batch("aa-x", 3), batch("bb-x", 2)]))
    gh, path = RunGitHub(), tmp_path / "m.gui-test.issues.json"
    out = tts.publish(plan, gh, map_path=path)
    return gh, path, out["map"]["areas"]


def results(tmp_path, run_id, started, verdicts, build="c" * 40):
    """A results file; `verdicts` maps a case id to a verdict, and its batch is the id's tag (`AAX-001` -> `aa-x`)."""
    steps = [{"id": case, "batch": {"AAX": "aa-x", "BBX": "bb-x", "ZZZ": "zz-x"}[case[:3]], "verdict": verdict}
             for case, verdict in verdicts.items()]
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps({
        "format": "gui-test-results/1", "run_id": run_id, "started": started, "platform": "darwin-arm64",
        "build_sha": build, "script": {"name": "tiny", "sha256": "b" * 64}, "steps": steps}), encoding="utf-8")
    return path


FIRST = {"AAX-001": "passed", "AAX-002": "failed", "AAX-003": "failed", "BBX-001": "passed", "BBX-002": "pending"}
SECOND = {"AAX-001": "failed", "AAX-002": "passed", "AAX-003": "failed", "BBX-001": "passed", "BBX-002": "pending"}


def test_a_write_back_makes_one_closed_run_issue_per_area_with_totals_and_changes(tmp_path, tree):
    gh, path, areas = tree
    tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", FIRST), path, gh)
    out = tts.write_back(results(tmp_path, "r2", "2026-10-11T08:00:00-0300", SECOND, "d" * 40), path, gh)
    assert out["written"] is True and out["ok"] is False      # a red run is still a non-zero exit
    aa = gh.runs(areas["aa"]["number"])
    assert len(aa) == 2 and len(gh.runs(areas["bb"]["number"])) == 2
    number, issue = aa[1]
    assert issue["title"] == "❌ Run 2026-10-11 11:00 UTC · aa · ddddddd"
    assert issue["state"] == "closed" and issue["labels"] == ["Test-Run", "enhancement"]
    assert [gh.issues[n]["title"] for n in gh.kids[areas["aa"]["number"]]] == ["📋 Cases", "📅 Runs 2026-10"]
    assert "✅ 1 · ❌ 2 · 🟡 0 · 👤 0 · ⚪ 0 (of 3)" in issue["body"]
    assert "New failures: `AAX-001`" in issue["body"]
    assert "Fixed: `AAX-002`" in issue["body"]
    assert "Still failing: `AAX-003`" in issue["body"]
    assert block(issue)["run_id"] == "r2" and block(issue)["cases"]["AAX-002"] == "passed"
    assert gh.issues[gh.runs(areas["bb"]["number"])[1][0]]["title"].startswith("👤 Run")


def test_a_partial_run_lists_only_its_cases_and_absent_cases_stay_out_of_the_trend(tmp_path, tree):
    gh, path, areas = tree
    tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", FIRST), path, gh)
    tts.write_back(results(tmp_path, "r2", "2026-10-11T08:00:00-0300", {"AAX-001": "failed"}), path, gh)
    issue = gh.runs(areas["aa"]["number"])[1][1]
    assert set(block(issue)["cases"]) == {"AAX-001"}
    assert "(of 1)" in issue["body"] and "AAX-002" not in issue["body"]
    dashboard = gh.issues[areas["aa"]["number"]]["body"]
    assert re.search(r"`AAX-001` \| ✅ ❌ \|", dashboard) and re.search(r"`AAX-002` \| ❌ \|", dashboard)


def test_trend_and_flaky_follow_the_run_blocks_ordered_by_started_not_issue_number(tmp_path, tree):
    gh, path, areas = tree
    flip = lambda verdict: dict(FIRST, **{"AAX-001": verdict})   # noqa: E731
    tts.write_back(results(tmp_path, "r3", "2026-10-11T09:00:00-0300", flip("passed")), path, gh)   # newest, written first
    tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", flip("passed")), path, gh)
    tts.write_back(results(tmp_path, "r2", "2026-10-11T08:00:00-0300", flip("failed")), path, gh)
    assert re.search(r"`AAX-001` \| ✅ ❌ ✅ \|", gh.issues[areas["aa"]["number"]]["body"])
    scenario = gh.issues[path_parent(path)]["body"]
    flaky = scenario.split("Flaky", 1)[1]
    assert "`AAX-001`" in flaky and "`AAX-003`" not in flaky
    runs = [line for line in scenario.splitlines() if line.startswith("| r")]
    assert [line.split("|")[1].strip() for line in runs] == ["r3", "r2", "r1"]


def path_parent(path):
    return json.loads(path.read_text(encoding="utf-8"))["parent"]


def test_text_outside_the_dashboard_markers_is_untouched(tmp_path, tree):
    gh, path, areas = tree
    number = areas["aa"]["number"]
    gh.issues[number]["body"] = f"before\n\n{START}\nhand edit\n{END}\n\nafter"
    gh.issues[path_parent(path)]["body"] = f"scenario text\n{START}\nold\n{END}\ntail"
    tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", FIRST), path, gh)
    body = gh.issues[number]["body"]
    assert body.startswith("before\n\n" + START) and body.endswith(END + "\n\nafter")
    assert "hand edit" not in body and "AAX-001" in body
    scenario = gh.issues[path_parent(path)]["body"]
    assert scenario.startswith("scenario text\n" + START) and scenario.endswith(END + "\ntail")


def test_a_second_writer_fails_while_the_lock_is_held_and_writes_nothing(tmp_path, tree):
    gh, path, areas = tree
    lock = path.with_name(path.name + ".lock")
    lock.write_text("held", encoding="utf-8")
    before = len(gh.calls)
    out = tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", FIRST), path, gh)
    assert out["ok"] is False and "lock" in out["reason"]
    assert len(gh.calls) == before and lock.exists()
    lock.unlink()
    assert tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", FIRST), path, gh)["written"] is True
    assert not lock.exists()


def test_a_step_with_no_area_in_the_map_is_reported_and_not_written(tmp_path, tree):
    gh, path, areas = tree
    out = tts.write_back(results(tmp_path, "r1", "2026-10-11T07:00:00-0300", dict(FIRST, **{"ZZZ-001": "passed"})),
                         path, gh)
    assert out["unknown_cases"] == ["ZZZ-001"]
    assert all("ZZZ-001" not in i["body"] for _, i in gh.runs(areas["aa"]["number"]) + gh.runs(areas["bb"]["number"]))
