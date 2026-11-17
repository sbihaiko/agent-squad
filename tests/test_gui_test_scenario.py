"""The `/to-test-scenario` module: scenario + script in, plan out; results in, verdicts written back (MesenAI #1223).

Driven through its public contract only: a fake GitHub runner records every call, and the run command is a fake that
returns a report. Nothing here touches the network, `gh` or the real run command of #1222.
"""
import json

import pytest

from squad import to_test_scenario as tts

MARKDOWN = """# A scenario

### 1. Launch

| ID | Precondition | Steps | Expected | [W] | [F] | Notes |
|---|---|---|---|---|---|---|
| HOME-01 | Fresh settings | Launch the app; look. | Home is on screen. | | — | |
| HOME-02 | HOME-01 | Press A. | The library opens. | | | |

### 2. Library

| ID | Precondition | Steps | Expected | [W] | [F] | Notes |
|---|---|---|---|---|---|---|
| LIB-01 | HOME-02 | Press B. | Back on Home. | | | |
"""


def _step(sid, **over):
    s = {"id": sid, "role": "under-test", "precondition": "ui.screen == play.home",
         "wait": {"check": "ui.screen == play.home", "timeout_ticks": 120},
         "check": {"name": "ui.screen", "args": {"is": "play.home"}},
         "expect": "the app is where the case says", "severity": "major", "mode": "automated"}
    s.update(over)
    return s


def script(*, name="tiny", batches=None, **over):
    d = {"format": "gui-test/1", "name": name, "target": "mesen-gui",
         "requires": {"actions": ["pad.press", "nav.goal"], "checks": ["ui.screen"]},
         "variants": {"window.mode": ["windowed", "fullscreen"]}, "fixtures": {},
         "batches": batches if batches is not None else
         [{"id": "launch", "setup": [], "teardown": [],
           "steps": [_step("HOME-01"), _step("HOME-02")]}]}
    d.update(over)
    return d


def covered_script():
    """The same three case ids of MARKDOWN, in script order, one step per case."""
    return script(batches=[
        {"id": "launch", "setup": [], "teardown": [], "steps": [_step("HOME-01", mode="manual")]},
        {"id": "home", "setup": [], "teardown": [], "steps": [_step("HOME-02")]},
        {"id": "library", "setup": [], "teardown": [], "steps": [_step("LIB-01")]},
    ])


class FakeGitHub:
    """Records `(op, kwargs)` and answers the numbers the module needs back.

    `states` is what GitHub says of an issue right now — its state and the verdict of its last comment — which is what
    the write-back reads to decide whether a verdict changed and whether a case must be reopened.
    """

    def __init__(self, states=None):
        self.calls = []
        self.states = dict(states or {})
        self._next = 100

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
        if op == "item_status":
            return "Backlog"           # the parent is boarded in Backlog (#895)
        if op == "issue_state":
            return self.states.get(kw["number"], {"state": "open", "verdict": None})
        if op == "create_issue":
            self._next += 1
            return {"number": self._next, "id": 900 + self._next}
        if op == "file_bug":
            return {"url": "https://example.test/bug/1"}
        return {}

    def id_of(self, number):
        """The numeric id of an issue the run created: sub-issue links use the id, never the number."""
        return 900 + number

    def ops(self):
        return [c[0] for c in self.calls]

    def of(self, op):
        return [kw for o, kw in self.calls if o == op]


# --- coverage: the Markdown's case ids against the script's step ids ---

def test_case_ids_reads_the_id_column_of_a_case_table():
    assert tts.case_ids(MARKDOWN) == ["HOME-01", "HOME-02", "LIB-01"]


def test_case_ids_ignores_tables_that_are_not_case_tables():
    assert tts.case_ids("| Item | Verdict |\n|---|---|\n| 1. Composition editor | |\n") == []


def test_step_ids_lists_every_step_in_script_order():
    assert tts.step_ids(covered_script()) == ["HOME-01", "HOME-02", "LIB-01"]


def test_gap_report_is_complete_when_every_case_has_a_step():
    r = tts.gap_report(MARKDOWN, covered_script())
    assert r == {"missing_in_script": [], "steps_without_case": [], "complete": True}


def test_gap_report_names_a_case_with_no_step_and_a_step_with_no_case():
    s = covered_script()
    s["batches"][2]["steps"] = [_step("LIB-09")]
    r = tts.gap_report(MARKDOWN, s)
    assert r["missing_in_script"] == ["LIB-01"]
    assert r["steps_without_case"] == ["LIB-09"]
    assert r["complete"] is False


# --- the plan ---

def test_plan_per_case_gives_one_issue_per_case_in_script_order():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN, script_path="docs/play.gui-test.json",
                          purpose="the pad-only Play script")
    assert [i["cases"] for i in plan["issues"]] == [["HOME-01"], ["HOME-02"], ["LIB-01"]]
    assert plan["name"] == "tiny"
    assert plan["parent"]["labels"] == ["ready-for-agent", "Test-Scenario"]
    assert plan["parent"]["title"] == "Test scenario: tiny"
    assert "docs/play.gui-test.json" in plan["parent"]["body"]
    assert plan["variants"] == {"window.mode": ["windowed", "fullscreen"]}


def test_plan_per_batch_gives_one_issue_per_batch_covering_its_cases():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN, granularity="per-batch")
    assert [i["cases"] for i in plan["issues"]] == [["HOME-01"], ["HOME-02"], ["LIB-01"]]
    assert all("wave" not in i for i in plan["issues"])


def test_a_case_blocked_by_a_setup_step_sits_one_wave_later_and_names_it():
    s = covered_script()
    s["batches"][1]["setup"] = [_step("setup.open-rom", role="setup")]
    plan = tts.build_plan(s, markdown=MARKDOWN)
    by_case = {i["cases"][0]: i for i in plan["issues"]}
    assert "setup.open-rom" in by_case
    assert by_case["HOME-02"]["blocked_by"] == ["setup.open-rom"]
    assert by_case["HOME-01"]["blocked_by"] == []
    assert all(not any(label.startswith("wave:") for label in i["labels"]) for i in plan["issues"])
    assert "setup.open-rom" in by_case["HOME-02"]["body"]
    assert "## Blocked by" in by_case["HOME-02"]["body"]


def test_a_case_never_carries_ready_for_agent_and_the_parent_always_does():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    assert all("Test-Case" in i["labels"] and "ready-for-agent" not in i["labels"] for i in plan["issues"])
    assert plan["parent"]["labels"] == ["ready-for-agent", "Test-Scenario"]


def test_a_sub_issue_body_carries_the_case_id_steps_expected_variant_and_mode():
    s = script(batches=[{"id": "launch", "setup": [], "teardown": [],
                         "steps": [_step("HOME-01", mode="manual", expect="Home is on screen",
                                         variants={"window.mode": "windowed"})]}])
    body = tts.build_plan(s)["issues"][0]["body"]
    for part in ("HOME-01", "Home is on screen", "window.mode=windowed", "manual", "## Blocked by"):
        assert part in body


def test_the_plan_lists_manual_cases_and_goal_cases_separately():
    s = script(batches=[{"id": "launch", "setup": [], "teardown": [], "steps": [
        _step("HOME-01", mode="manual"),
        _step("HOME-02", action={"name": "nav.goal", "args": {"goal": "ui.screen == play.home"}}, role="recovery"),
    ]}])
    plan = tts.build_plan(s)
    assert plan["manual"] == ["HOME-01"]
    assert plan["goals"] == ["HOME-02"]


# --- publishing, idempotently ---

def test_publish_creates_the_parent_and_one_sub_issue_per_case_and_links_them():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    out = tts.publish(plan, gh, board=True)
    created = gh.of("create_issue")
    assert [c["title"] for c in created][0] == "Test scenario: tiny"
    assert [c["title"] for c in created if "Test case" in c["title"]] == [
        "⚪ Test case HOME-01", "⚪ Test case HOME-02", "⚪ Test case LIB-01"]
    assert sorted(out["issues"]) == ["HOME-01", "HOME-02", "LIB-01"]
    links = {link["sub"]: link["parent"] for link in gh.of("add_sub_issue")}
    containers = {c["number"] for a in out["map"]["areas"].values() for c in a["containers"]}
    assert {links[gh.id_of(out["issues"][c])] for c in ("HOME-01", "HOME-02", "LIB-01")} == containers
    assert out["parent"] not in {links[gh.id_of(out["issues"][c])] for c in out["issues"]}   # never the scenario


def test_publish_ensures_the_labels_it_applies_before_the_first_issue():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    tts.publish(plan, gh, board=True)
    ensured = [kw["name"] for kw in gh.of("ensure_label")]
    assert "Test-Case" in ensured and "ready-for-agent" in ensured
    assert not any(name.startswith("wave:") for name in ensured), "no wave label is written (#2132)"
    assert gh.ops().index("ensure_label") < gh.ops().index("create_issue")


# --- writing a run's results back ---

def _run(results, **over):
    r = {"run_id": "r-1", "script_sha": "abc", "platform": "macos-arm64", "build_sha": "def",
         "core_available": True, "results": [{"id": i, "verdict": v} for i, v in results]}
    r.update(over)
    return r


def _published():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    return plan, tts.publish(plan, gh, board=True)


def test_a_passed_case_is_commented_and_closed():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "passed"), ("HOME-02", "passed"), ("LIB-01", "passed")]),
                            gh, published=pub)
    assert out["counts"]["passed"] == 3
    assert sorted(out["closed"]) == ["HOME-01", "HOME-02", "LIB-01"]
    assert sorted(kw["number"] for kw in gh.of("close")) == sorted(pub["issues"].values())


def test_a_failed_case_is_commented_with_expected_and_observed_and_left_open():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "failed (expected play.home, observed play.library)"),
                                        ("HOME-02", "passed"), ("LIB-01", "passed")]), gh, published=pub)
    assert out["counts"]["failed"] == 1
    assert pub["issues"]["HOME-01"] not in [kw["number"] for kw in gh.of("close")]
    body = [kw["body"] for kw in gh.of("comment") if kw["number"] == pub["issues"]["HOME-01"]][0]
    assert "failed" in body and "play.library" in body and "expected" in body
    assert out["needs_review"] == []


def test_many_failed_cases_link_the_run_s_bug_and_open_none_here():
    plan, pub = _published()
    gh = FakeGitHub()
    run = _run([("HOME-01", "failed (timeout)"), ("HOME-02", "failed (timeout)")],
               bug={"filed": True, "url": "https://example.test/bug/7"})
    out = tts.write_results(plan, run, gh, published=pub)
    assert gh.of("file_bug") == [] and out["bug"] == "https://example.test/bug/7"
    parent = [kw["body"] for kw in gh.of("comment") if kw["number"] == pub["parent"]][0]
    assert "https://example.test/bug/7" in parent


def test_needs_review_pending_and_a_manual_cases_pending_are_never_a_bug():
    s = covered_script()
    s["batches"][0]["steps"][0]["mode"] = "manual"        # the runner puts a manual step to a person: it ends `pending`
    plan = tts.build_plan(s, markdown=MARKDOWN)
    gh = FakeGitHub()
    pub = tts.publish(plan, gh, board=True)
    again = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "pending"), ("HOME-02", "needs review")]),
                            again, published=pub)
    assert out["bug"] is None and "file_bug" not in again.ops()
    assert out["needs_review"] == ["HOME-02"] and out["pending"] == ["HOME-01"]
    assert out["counts"]["passed"] == 0 and "close" not in again.ops()
    assert plan["manual"] == ["HOME-01"]
    parent_body = [kw["body"] for kw in again.of("comment") if kw["number"] == pub["parent"]][0]
    assert "HOME-01" in parent_body and "HOME-02" in parent_body


def test_the_parent_gets_one_summary_comment_with_counts_run_id_sha_and_platform():
    plan, pub = _published()
    gh = FakeGitHub()
    tts.write_results(plan, _run([("HOME-01", "passed"), ("HOME-02", "passed"), ("LIB-01", "failed (x)")]),
                      gh, published=pub)
    comments = [kw for kw in gh.of("comment") if kw["number"] == pub["parent"]]
    assert len(comments) == 1
    for part in ("r-1", "abc", "macos-arm64", "def", "2", "1"):
        assert part in comments[0]["body"]
    assert "close" not in [kw["number"] for kw in gh.of("close")]


def test_a_step_cut_short_by_the_vision_spend_cap_is_pending_and_never_a_pass():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "skipped (spend cap $2.00)"), ("HOME-02", "passed"),
                                        ("LIB-01", "passed")]), gh, published=pub)
    assert out["counts"]["passed"] == 2 and out["pending"] == ["HOME-01"]
    assert out["bug"] is None and "file_bug" not in gh.ops()


def test_a_run_that_executed_no_step_is_a_failure_and_writes_nothing():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([]), gh, published=pub)
    assert out["ok"] is False and "empty" in out["reason"]
    assert gh.calls == []


def test_a_missing_native_core_fails_the_run_and_writes_nothing():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "passed")], core_available=False), gh, published=pub)
    assert out["ok"] is False and "core" in out["reason"]
    assert gh.calls == []


def test_a_case_with_no_result_in_the_run_is_reported_not_passed():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "passed")]), gh, published=pub)
    assert out["missing_results"] == ["HOME-02", "LIB-01"]
    assert out["ok"] is False


def test_a_per_batch_plan_aggregates_its_steps_onto_the_batch_issue():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN, granularity="per-batch")
    gh = FakeGitHub()
    pub = tts.publish(plan, gh, board=True)
    again = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "passed"), ("HOME-02", "failed (x)"), ("LIB-01", "passed")]),
                            again, published=pub)
    assert out["counts"]["failed"] == 1
    assert len(again.of("comment")) == 4          # 3 batches + the parent


# --- the whole flow ---

def test_the_scenario_stops_after_publishing_when_the_run_command_does_not_exist():
    gh = FakeGitHub()
    out = tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=None, board=True)
    assert out["run"] is None and out["reason"] == "run command unavailable"
    assert out["published"]["parent"] == 101          # the parent is the first issue created
    assert gh.ops().count("create_issue") == 1 + 3 * 3          # the scenario, then an area, a container, a case each
    assert "comment" not in gh.ops()


def test_the_scenario_publishes_then_runs_and_writes_every_verdict_back():
    report = _run([("HOME-01", "passed"), ("HOME-02", "passed"), ("LIB-01", "failed (x)")],
                  bug={"filed": True, "url": "https://example.test/bug/9"})
    gh, cmd = FakeGitHub(), _cmd(report)
    out = tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=cmd, board=True)
    assert out["counts"]["failed"] == 1 and out["bug"] == "https://example.test/bug/9"
    assert "file_bug" not in gh.ops()
    assert [c["script"] for c in cmd.calls] == ["tiny"]


def test_a_run_scenario_on_a_board_less_runner_never_asks_for_a_status():
    """The run's publish takes `board=False` from its caller: a runner with no board never answers item_status."""
    class BoardlessGitHub(FakeGitHub):
        def __call__(self, op, **kw):
            if op == "item_status":
                raise AssertionError("a board-less publish must not read a card's Status")
            return super().__call__(op, **kw)

    gh = BoardlessGitHub()
    out = tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=None, board=False)
    assert out["run"] is None and out["published"]["parent"] == 101
    assert "item_status" not in gh.ops()


def test_run_scenario_refuses_to_guess_the_board_when_the_caller_does_not_name_one():
    gh = FakeGitHub()
    with pytest.raises(TypeError, match="board"):
        tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=None)
    assert gh.calls == []


def test_the_scenario_reports_a_gap_and_publishes_nothing():
    gh = FakeGitHub()
    out = tts.run_scenario(MARKDOWN, script(batches=[{"id": "b", "setup": [], "teardown": [],
                                                      "steps": [_step("NOPE-01")]}]), gh, run_command=None, board=True)
    assert out["gaps"]["missing_in_script"] == ["HOME-01", "HOME-02", "LIB-01"]
    assert gh.calls == []


# --- the real runner: REST, and the numeric id on a sub-issue link ---

class _Shell:
    """Stands in for `subprocess.run`: records the argv and answers the JSON a REST call returns."""

    def __init__(self):
        self.args = []

    def __call__(self, args, **kw):
        self.args.append(list(args))
        if args[:3] == ["gh", "api", "--method"] and args[3] == "POST" and args[-2:] != ["-f", "sub_issue_id=99"]:
            return '{"number": 12, "id": 900}'
        if "sub_issues" in " ".join(args):
            return '{"number": 12}'
        return "{}"


def test_the_rest_runner_posts_issues_and_links_a_sub_issue_by_its_numeric_id():
    sh = _Shell()
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh)
    gh("create_issue", title="Test case HOME-01", body="body", labels=["Test-Case", "wave:01", "enhancement"])
    gh("add_sub_issue", parent=7, sub=99)
    assert "repos/sbihaiko/MesenAI/issues" in sh.args[0]
    assert "=Test case HOME-01" in " ".join(sh.args[0]) and "labels[]=Test-Case" in sh.args[0]
    assert "labels[]=enhancement" in sh.args[0]
    link = " ".join(sh.args[1])
    assert "repos/sbihaiko/MesenAI/issues/7/sub_issues" in link and "sub_issue_id=99" in link


def test_the_rest_runner_refuses_an_issue_without_a_product_type_label():
    import pytest

    gh = tts.rest_runner("sbihaiko/MesenAI", shell=_Shell())
    with pytest.raises(ValueError, match="bug, enhancement, spec"):
        gh("create_issue", title="Test case HOME-01", body="body", labels=["Test-Case", "wave:01"])


def test_the_rest_runner_closes_through_rest_and_never_graphql():
    sh = _Shell()
    tts.rest_runner("sbihaiko/MesenAI", shell=sh)("close", number=3)
    assert sh.args[0][:4] == ["gh", "api", "--method", "PATCH"]
    assert "repos/sbihaiko/MesenAI/issues/3" in sh.args[0] and "state=closed" in sh.args[0]


def test_the_rest_runner_refuses_to_file_a_bug_without_the_repo_helper():
    import pytest
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=_Shell())
    with pytest.raises(ValueError, match="bug_helper"):
        gh("file_bug", title="t", body="b", priority="P1")


def test_the_rest_runner_files_a_bug_through_the_repos_own_helper():
    sh = _Shell()
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh, bug_helper="/repo/scripts/report-bug.sh")
    out = gh("file_bug", title="GUI test: 2 failed", body="steps", priority="P1")
    assert sh.args[0] == ["/repo/scripts/report-bug.sh", "GUI test: 2 failed", "steps", "P1"]
    assert out == {"url": None, "number": None}


class _cmd:
    """A stand-in for #1222's run command: a script name in, a run report out."""

    def __init__(self, report):
        self.report = report
        self.calls = []

    def __call__(self, script):
        self.calls.append({"script": script["name"]})
        return self.report


# --- MesenAI #1226: the write-back command reads a run's results file and writes it to the issues ---------------

RESULTS = {
    "format": "gui-test-results/1", "run_id": "20261010T120000-ab12cd", "adapter_name": "mesen-gui",
    "script": {"path": "docs/validation/process/play-pad-only.gui-test.json", "sha256": "b" * 64, "name": "tiny"},
    "platform": "darwin-arm64", "build_sha": "c" * 40, "started": "2026-10-10T12:00:00-0300",
    "status": "failed", "reasons": ["1 step(s) failed: HOME-02"],
    "steps": [
        {"id": "HOME-01", "batch": "launch", "mode": "automated", "role": "under-test", "verdict": "passed",
         "capture": None, "held": False},
        {"id": "HOME-02", "batch": "home", "mode": "automated", "role": "under-test", "verdict": "passed",
         "capture": None, "held": False},
        {"id": "LIB-01", "batch": "library", "mode": "automated", "role": "under-test", "verdict": "pending",
         "capture": None, "held": False},
    ],
    "bug": {"filed": False, "reason": "no step failed"},
}


def results_file(tmp_path, **over):
    """A well-formed `gui-test-results/1` file (the schema frozen in MesenAI #1222) that a test can bend."""
    data = json.loads(json.dumps(RESULTS))
    data.update(over)
    path = tmp_path / "tiny-20261010T120000-ab12cd.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def map_file(tmp_path, cases=None, parent=7):
    """The committed map beside the script (MesenAI #1225): the parent and the issue of every case."""
    path = tmp_path / "tiny.gui-test.issues.json"
    path.write_text(json.dumps({"parent": parent, "issues": cases if cases is not None else
                                {"HOME-01": 11, "HOME-02": 12, "LIB-01": 13}}), encoding="utf-8")
    return path


def write_back(tmp_path, gh=None, plan=None, results=None, mapping=None, parent=7):
    """Drive the command at its public seam: a results file and a map file in, a fake GitHub runner out."""
    path = results_file(tmp_path) if results is None else results
    gh = gh if gh is not None else FakeGitHub()
    out = tts.write_back(path, mapping or map_file(tmp_path, parent=parent), gh, plan=plan)
    return gh, out


def test_1226_a_valid_results_file_drives_the_comments_and_closes_without_a_plan(tmp_path):
    gh, out = write_back(tmp_path)
    assert out["ok"] is True
    assert sorted(out["counts"].items()) == [("passed", 2), ("pending", 1)]
    assert sorted(out["closed"]) == ["HOME-01", "HOME-02"]
    assert sorted(kw["number"] for kw in gh.of("close")) == [11, 12]
    assert out["pending"] == ["LIB-01"] and 13 not in [kw["number"] for kw in gh.of("close")]
    assert sorted(kw["number"] for kw in gh.of("comment") if kw["number"] != 7) == [11, 12, 13]
    assert out["bug"] is None and "file_bug" not in gh.ops()


def test_1226_the_plan_groups_the_steps_of_one_issue(tmp_path):
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh, out = write_back(tmp_path, plan=plan)
    assert out["ok"] is True
    numbers = [kw["number"] for kw in gh.of("comment") if kw["number"] != 7]
    assert sorted(numbers) == [11, 12, 13] and len(set(numbers)) == 3


def test_1226_a_malformed_results_file_is_rejected_with_the_reason(tmp_path):
    bad = results_file(tmp_path, steps=[dict(RESULTS["steps"][0], verdict="probably")])
    gh, out = write_back(tmp_path, results=bad)
    assert out["ok"] is False and "malformed" in out["reason"] and "probably" in out["reason"]
    assert gh.calls == []


def test_1226_every_shape_of_a_malformed_file_is_named(tmp_path):
    cases = [
        ({"format": "gui-test-results/9"}, "format"),
        ({"run_id": ""}, "run_id"),
        ({"platform": None}, "platform"),
        ({"build_sha": None}, "build_sha"),
        ({"script": {"name": "tiny"}}, "script.sha256"),
        ({"steps": [{"batch": "home", "verdict": "passed"}]}, "has no id"),
        ({"steps": [{"id": "HOME-01", "verdict": "passed"}]}, "has no batch"),
        ({"steps": [dict(RESULTS["steps"][0]), dict(RESULTS["steps"][0])]}, "duplicate step id"),
        ({"steps": "all of them"}, "steps"),
    ]
    for over, expected in cases:
        with pytest.raises(tts.ResultsError) as err:
            tts.read_results(results_file(tmp_path, **over))
        assert expected in str(err.value), (over, str(err.value))


def test_1226_a_file_that_is_not_json_is_rejected_with_the_reason(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(tts.ResultsError) as err:
        tts.read_results(path)
    assert "not JSON" in str(err.value)


def test_1226_an_unchanged_verdict_adds_no_comment_and_a_closed_case_that_fails_is_reopened(tmp_path):
    states = {11: {"state": "closed", "verdict": "passed"},      # same verdict, already closed: nothing to say
              12: {"state": "closed", "verdict": "passed"},      # regressed: comment, reopen
              13: {"state": "open", "verdict": "pending"}}       # same verdict, still open: nothing to say
    steps = [dict(RESULTS["steps"][0]),
             dict(RESULTS["steps"][1], verdict="failed (timeout waiting for ui.screen == play.library)"),
             RESULTS["steps"][2]]
    gh, out = write_back(tmp_path, gh=FakeGitHub(states), results=results_file(tmp_path, steps=steps))
    assert out["unchanged"] == ["HOME-01", "LIB-01"] and out["commented"] == ["HOME-02"]
    assert out["reopened"] == ["HOME-02"] and [kw["number"] for kw in gh.of("reopen")] == [12]
    assert [kw["number"] for kw in gh.of("comment") if kw["number"] != 7] == [12]
    assert out["closed"] == [] and gh.of("close") == []


def test_1226_a_case_that_passes_after_a_reopen_is_closed_again(tmp_path):
    states = {11: {"state": "open", "verdict": "failed (x)"}}
    gh, out = write_back(tmp_path, gh=FakeGitHub(states), plan=None, mapping=map_file(tmp_path, {"HOME-01": 11}),
                         results=results_file(tmp_path, steps=[dict(RESULTS["steps"][0])]))
    assert out["closed"] == ["HOME-01"] and [kw["number"] for kw in gh.of("close")] == [11]
    assert out["reopened"] == []


def test_1226_no_bug_is_ever_opened_in_any_verdict_mix(tmp_path):
    steps = [dict(RESULTS["steps"][0]),
             dict(RESULTS["steps"][1], verdict="failed (expected play.library, observed play.home)"),
             dict(RESULTS["steps"][2], verdict="needs review")]
    run = results_file(tmp_path, steps=steps, bug={"filed": True, "url": "https://example.test/bug/1230"})
    gh, out = write_back(tmp_path, results=run, mapping=map_file(tmp_path, {"HOME-01": 11, "HOME-02": 12, "LIB-01": 13}))
    assert gh.of("file_bug") == [] and not (gh.ops().count("file_bug"))
    assert out["bug"] == "https://example.test/bug/1230"
    parent = [kw["body"] for kw in gh.of("comment") if kw["number"] == 7][0]
    assert "https://example.test/bug/1230" in parent
    assert out["needs_review"] == ["LIB-01"]


def test_1226_evidence_is_linked_only_for_a_failed_or_a_needs_review_case(tmp_path):
    steps = [dict(RESULTS["steps"][0], evidence="https://evidence.test/passed.png"),
             dict(RESULTS["steps"][1], verdict="failed (x)", evidence="https://evidence.test/home-02.png"),
             dict(RESULTS["steps"][2], verdict="needs review", evidence="https://evidence.test/lib-01.png")]
    gh, _ = write_back(tmp_path, results=results_file(tmp_path, steps=steps))
    bodies = {kw["number"]: kw["body"] for kw in gh.of("comment")}
    assert "https://evidence.test/home-02.png" in bodies[12] and "https://evidence.test/lib-01.png" in bodies[13]
    assert "evidence" not in bodies[11] and "passed.png" not in bodies[11]


def test_1226_a_run_that_executed_no_step_is_a_failure_not_a_pass(tmp_path):
    run = results_file(tmp_path, steps=[], status="failed", reasons=["no step executed"])
    gh, out = write_back(tmp_path, results=run)
    assert out["ok"] is False and "no step executed" in out["reason"] and gh.calls == []


def test_1226_a_results_file_marking_the_core_unavailable_is_a_failure(tmp_path):
    run = results_file(tmp_path, core_available=False, steps=[],
                       reasons=["the native core /tmp/MesenCore.dylib cannot be loaded"])
    gh, out = write_back(tmp_path, results=run)
    assert out["ok"] is False and "core" in out["reason"] and gh.calls == []


def test_1226_a_case_the_run_never_reached_is_reported_not_passed(tmp_path):
    run = results_file(tmp_path, steps=[dict(RESULTS["steps"][0])])
    gh, out = write_back(tmp_path, results=run)
    assert out["missing_results"] == ["HOME-02", "LIB-01"] and out["ok"] is False
    assert "no result" in out["reason"]


def test_1226_a_step_the_map_does_not_know_is_reported(tmp_path):
    steps = RESULTS["steps"] + [dict(RESULTS["steps"][0], id="GHOST-01")]
    gh, out = write_back(tmp_path, results=results_file(tmp_path, steps=steps))
    assert out["unknown_cases"] == ["GHOST-01"]


def test_1226_the_map_beside_the_script_is_read_in_the_shape_the_publish_writes(tmp_path):
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"HOME-01": 11, "parent": 7}), encoding="utf-8")
    assert tts.read_map(flat) == {"parent": 7, "issues": {"HOME-01": 11}}
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps({"parent": 7, "cases": {"HOME-01": {"number": 11, "content_hash": "a" * 64}}}),
                      encoding="utf-8")
    assert tts.read_map(nested) == {"parent": 7, "issues": {"HOME-01": 11},
                                    "cases": {"HOME-01": {"number": 11, "content_hash": "a" * 64}}}
    assert tts.read_map(tmp_path / "absent.json") == {"parent": None, "issues": {}}


def test_1226_a_map_that_names_no_case_is_reported_and_writes_nothing(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"parent": 7, "issues": {}}), encoding="utf-8")
    gh, out = write_back(tmp_path, results=results_file(tmp_path), mapping=empty)
    assert out["ok"] is False and "publish" in out["reason"] and gh.calls == []


def test_1226_a_map_without_a_parent_is_reported(tmp_path):
    orphan = tmp_path / "orphan.json"
    orphan.write_text(json.dumps({"HOME-01": 11}), encoding="utf-8")
    gh, out = write_back(tmp_path, results=results_file(tmp_path), mapping=orphan)
    assert out["ok"] is False and "parent" in out["reason"] and gh.calls == []


def test_1226_the_parent_gets_one_summary_comment_with_the_counts_the_shas_and_the_run_s_bug(tmp_path):
    run = results_file(tmp_path, steps=[dict(RESULTS["steps"][0]),
                                        dict(RESULTS["steps"][1], verdict="failed (x)"),
                                        dict(RESULTS["steps"][2], verdict="needs review")],
                       bug={"filed": True, "url": "https://example.test/bug/1234"})
    gh, out = write_back(tmp_path, results=run)
    summaries = [kw["body"] for kw in gh.of("comment") if kw["number"] == 7]
    assert len(summaries) == 1
    body = summaries[0]
    for part in ("20261010T120000-ab12cd", "b" * 64, "darwin-arm64", "c" * 40,
                 "https://example.test/bug/1234", "LIB-01"):
        assert part in body
    assert out["counts"] == {"failed": 1, "needs review": 1, "passed": 1}
    assert 7 not in [kw["number"] for kw in gh.of("close")]


def test_1226_the_command_is_deterministic_and_model_free_the_same_run_twice_writes_nothing_the_second_time(tmp_path):
    run = results_file(tmp_path)
    mapping = map_file(tmp_path)
    states = {}
    gh = FakeGitHub(states)
    first = tts.write_back(run, mapping, gh)
    for number, verdict in ((11, "passed"), (12, "passed"), (13, "pending")):
        states[number] = {"state": "closed" if verdict == "passed" else "open", "verdict": verdict}
    again = FakeGitHub(states)
    second = tts.write_back(run, mapping, again)
    assert first["commented"] == ["HOME-01", "HOME-02", "LIB-01"]
    assert second["commented"] == [] and second["unchanged"] == ["HOME-01", "HOME-02", "LIB-01"]
    assert [kw["number"] for kw in again.of("comment")] == [7]          # the summary, and nothing else


def test_1226_the_command_returns_zero_when_the_verdicts_land_and_one_when_they_cannot(tmp_path, capsys):
    assert tts.main(["write-back", str(results_file(tmp_path)), "--map", str(map_file(tmp_path))],
                    github=FakeGitHub()) == 0
    assert "HOME-01" in capsys.readouterr().out
    broken = results_file(tmp_path, steps=[])
    assert tts.main(["write-back", str(broken), "--map", str(map_file(tmp_path))], github=FakeGitHub()) == 1
    assert "no step executed" in capsys.readouterr().out


class _ReadShell:
    """Stands in for `subprocess.run` on the two GETs the write-back makes: the issue, and its comments."""

    def __init__(self, state="open", comments=None):
        self.args, self.state, self.comments = [], state, list(comments or [])

    def __call__(self, args, **kw):
        self.args.append(list(args))
        path = next((a for a in args if a.startswith("repos/")), "")
        if path.endswith("/comments"):
            return json.dumps([{"body": b} for b in self.comments])
        return json.dumps({"number": 3, "id": 903, "state": self.state})


def test_1226_the_rest_runner_reads_an_issue_s_state_and_the_verdict_of_its_last_comment():
    sh = _ReadShell(comments=["a person said hello", "**passed**\n\n- `HOME-01`: passed"])
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh)
    assert gh("issue_state", number=3) == {"state": "open", "verdict": "passed", "title": None, "labels": [],
                                           "id": 903}
    assert sh.args[0][:2] == ["gh", "api"] and "repos/sbihaiko/MesenAI/issues/3" in sh.args[0]


def test_1226_the_rest_runner_reads_an_issue_that_never_carried_a_verdict():
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=_ReadShell(state="closed", comments=["hello"]))
    assert gh("issue_state", number=3) == {"state": "closed", "verdict": None, "title": None, "labels": [],
                                           "id": 903}


def test_1226_the_rest_runner_reopens_through_rest_and_never_graphql():
    sh = _ReadShell()
    tts.rest_runner("sbihaiko/MesenAI", shell=sh)("reopen", number=3)
    assert sh.args[0][:4] == ["gh", "api", "--method", "PATCH"]
    assert "repos/sbihaiko/MesenAI/issues/3" in sh.args[0] and "state=open" in sh.args[0]


def test_1226_everything_the_write_back_writes_is_en_us(tmp_path):
    run = results_file(tmp_path, steps=[dict(RESULTS["steps"][0]),
                                        dict(RESULTS["steps"][1], verdict="failed (x)",
                                             evidence="https://evidence.test/home-02.png"),
                                        dict(RESULTS["steps"][2], verdict="needs review")],
                       reasons=["1 step(s) failed: HOME-02"])
    gh, _ = write_back(tmp_path, results=run)
    bodies = [kw["body"] for kw in gh.of("comment")] + [out["reason"] for out in
                                                        [tts.write_back(results_file(tmp_path, steps=[]),
                                                                        map_file(tmp_path), FakeGitHub())]]
    assert bodies and all(body.encode("ascii") for body in bodies)


# --- #1473 (ADR-0275 Slice B): the rest_runner's re-parenting, listing, title, body and bug-search ops ---

class _Scripted:
    """A stub shell answering by the argv's text: `{substring: [answers...]}`, one answer per call."""

    def __init__(self, answers=None):
        self.args = []
        self.answers = {k: list(v) for k, v in (answers or {}).items()}

    def __call__(self, args, **kw):
        self.args.append(list(args))
        text = " ".join(args)
        for key, queue in self.answers.items():
            if key in text and queue:
                return queue.pop(0)
        return "{}"


def test_1473_add_sub_issue_replace_parent_asks_github_to_move_the_child():
    sh = _Scripted()
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh)
    gh("add_sub_issue", parent=7, sub=99, replace_parent=True)
    gh("add_sub_issue", parent=7, sub=98)
    assert "replace_parent=true" in sh.args[0] and "sub_issue_id=99" in sh.args[0]
    assert not any(a.startswith("replace_parent") for a in sh.args[1])


def test_1561_add_sub_issue_sends_replace_parent_as_a_json_boolean_not_a_string():
    sh = _Scripted()
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh)
    gh("add_sub_issue", parent=7, sub=99, replace_parent=True)
    argv = sh.args[0]
    # `gh api -f k=v` sends a string, so GitHub answers 422 ("true" is not of type boolean); only `-F` types it.
    at = argv.index("replace_parent=true")
    assert argv[at - 1] == "-F"


def test_1473_remove_sub_issue_deletes_the_link_through_rest():
    sh = _Scripted()
    tts.rest_runner("sbihaiko/MesenAI", shell=sh)("remove_sub_issue", parent=7, sub=99)
    assert sh.args[0][:4] == ["gh", "api", "--method", "DELETE"]
    assert "repos/sbihaiko/MesenAI/issues/7/sub_issue" in sh.args[0] and "sub_issue_id=99" in sh.args[0]


def test_1473_list_sub_issues_follows_every_page():
    import json
    page1 = json.dumps([{"number": n} for n in range(100)])
    page2 = json.dumps([{"number": 100}])
    sh = _Scripted({"page=1": [page1], "page=2": [page2]})
    out = tts.rest_runner("sbihaiko/MesenAI", shell=sh)("list_sub_issues", number=7)
    assert [i["number"] for i in out] == list(range(101))
    assert all("issues/7/sub_issues" in " ".join(a) for a in sh.args) and len(sh.args) == 2


def test_1473_set_title_patches_only_the_title():
    sh = _Scripted()
    tts.rest_runner("sbihaiko/MesenAI", shell=sh)("set_title", number=5, title="New")
    assert sh.args[0][:4] == ["gh", "api", "--method", "PATCH"]
    assert "repos/sbihaiko/MesenAI/issues/5" in sh.args[0] and "title=New" in sh.args[0]
    assert not any(a.startswith("body=") for a in sh.args[0])


def test_1473_issue_body_returns_the_body_text():
    sh = _Scripted({"issues/5": ['{"body": "hello"}']})
    assert tts.rest_runner("sbihaiko/MesenAI", shell=sh)("issue_body", number=5) == "hello"
    sh = _Scripted({"issues/5": ['{"body": null}']})
    assert tts.rest_runner("sbihaiko/MesenAI", shell=sh)("issue_body", number=5) == ""


def test_1473_search_open_bug_finds_an_open_issue_by_exact_title():
    import json
    items = [{"number": 3, "title": "GUI test: 2 failed (old)", "html_url": "u3"},
             {"number": 4, "title": "GUI test: 2 failed", "html_url": "u4"}]
    sh = _Scripted({"search/issues": [json.dumps({"items": items})]})
    hit = tts.rest_runner("sbihaiko/MesenAI", shell=sh)("search_open_bug", title="GUI test: 2 failed")
    assert hit == {"number": 4, "url": "u4", "title": "GUI test: 2 failed"}
    import urllib.parse
    q = urllib.parse.unquote(" ".join(sh.args[0]))
    assert "is:issue" in q and "is:open" in q and "label:bug" in q and "repo:sbihaiko/MesenAI" in q
    sh = _Scripted({"search/issues": ['{"items": []}']})
    assert tts.rest_runner("sbihaiko/MesenAI", shell=sh)("search_open_bug", title="x") is None


def test_1473_file_bug_returns_the_number_parsed_from_the_url():
    sh = _Scripted({"report-bug": ["created https://github.com/sbihaiko/MesenAI/issues/1500\n"]})
    gh = tts.rest_runner("sbihaiko/MesenAI", shell=sh, bug_helper="/repo/scripts/report-bug.sh")
    assert gh("file_bug", title="t", body="b", priority="P1") == {
        "url": "https://github.com/sbihaiko/MesenAI/issues/1500", "number": 1500}


def test_1473_a_rate_limit_on_a_new_op_is_waited_out_not_failed():
    import subprocess
    state = {"n": 0}

    def shell(args, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise subprocess.CalledProcessError(1, args, "", "You have exceeded a secondary rate limit")
        return '[{"number": 1}]'

    waits = []
    call = tts._paced(tts.rest_runner("sbihaiko/MesenAI", shell=shell), wait=waits.append, attempts=3, backoff=1.0)
    assert call("list_sub_issues", number=7) == [{"number": 1}]
    assert waits == [1.0]
