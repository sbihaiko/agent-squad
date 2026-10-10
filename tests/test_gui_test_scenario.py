"""The `/to-test-scenario` module: scenario + script in, plan out; results in, verdicts written back (MesenAI #1223).

Driven through its public contract only: a fake GitHub runner records every call, and the run command is a fake that
returns a report. Nothing here touches the network, `gh` or the real run command of #1222.
"""
import json

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
    """Records `(op, kwargs)` and answers the numbers the module needs back."""

    def __init__(self):
        self.calls = []
        self._next = 100

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
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
    assert plan["parent"]["labels"] == ["ready-for-agent", "wave:01"]
    assert plan["parent"]["title"] == "Test scenario: tiny"
    assert "docs/play.gui-test.json" in plan["parent"]["body"]
    assert plan["variants"] == {"window.mode": ["windowed", "fullscreen"]}


def test_plan_per_batch_gives_one_issue_per_batch_covering_its_cases():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN, granularity="per-batch")
    assert [(i["cases"], i["wave"]) for i in plan["issues"]] == [
        (["HOME-01"], "01"), (["HOME-02"], "01"), (["LIB-01"], "01")]


def test_a_case_blocked_by_a_setup_step_sits_one_wave_later_and_names_it():
    s = covered_script()
    s["batches"][1]["setup"] = [_step("setup.open-rom", role="setup")]
    plan = tts.build_plan(s, markdown=MARKDOWN)
    by_case = {i["cases"][0]: i for i in plan["issues"]}
    assert "setup.open-rom" in by_case
    assert by_case["setup.open-rom"]["wave"] == "01"
    assert by_case["HOME-02"]["blocked_by"] == ["setup.open-rom"]
    assert by_case["HOME-02"]["wave"] == "02"
    assert by_case["HOME-01"]["wave"] == "01" and by_case["HOME-01"]["blocked_by"] == []
    assert "setup.open-rom" in by_case["HOME-02"]["body"]
    assert "## Blocked by" in by_case["HOME-02"]["body"]


def test_a_case_never_carries_ready_for_agent_and_the_parent_always_does():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    assert all("test-case" in i["labels"] and "ready-for-agent" not in i["labels"] for i in plan["issues"])
    assert plan["parent"]["labels"] == ["ready-for-agent", "wave:01"]


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
    out = tts.publish(plan, gh)
    created = gh.of("create_issue")
    assert [c["title"] for c in created][0] == "Test scenario: tiny"
    assert [c["title"] for c in created][1:] == [
        "Test case HOME-01", "Test case HOME-02", "Test case LIB-01"]
    assert sorted(out["issues"]) == ["HOME-01", "HOME-02", "LIB-01"]
    links = gh.of("add_sub_issue")
    assert [link["sub"] for link in links] == [gh.id_of(out["issues"][c])
                                                  for c in ("HOME-01", "HOME-02", "LIB-01")]
    assert {link["parent"] for link in links} == {out["parent"]}


def test_publish_ensures_the_labels_it_applies_before_the_first_issue():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    tts.publish(plan, gh)
    ensured = [kw["name"] for kw in gh.of("ensure_label")]
    assert "test-case" in ensured and "ready-for-agent" in ensured and "wave:01" in ensured
    assert gh.ops().index("ensure_label") < gh.ops().index("create_issue")


def test_publish_puts_every_issue_on_the_board_with_the_initial_status():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    tts.publish(plan, gh)
    assert len(gh.of("board")) == 4 and all(b["status"] == "To triage" for b in gh.of("board"))


def test_republish_with_the_saved_map_updates_instead_of_creating_a_duplicate():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    first = tts.publish(plan, gh)
    again = FakeGitHub()
    second = tts.publish(plan, again, saved=first["issues"], parent=first["parent"])
    assert "create_issue" not in again.ops()
    assert second["issues"] == first["issues"]
    assert [kw["number"] for kw in again.of("update_issue")] == [first["parent"]] + [first["issues"][c]
                                                                                  for c in ("HOME-01", "HOME-02", "LIB-01")]


def test_a_case_removed_from_the_script_is_reported_never_closed():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    first = tts.publish(plan, gh)
    shrunk = tts.build_plan(script(batches=[{"id": "launch", "setup": [], "teardown": [],
                                             "steps": [_step("HOME-01")]}]), markdown=None)
    again = FakeGitHub()
    out = tts.publish(shrunk, again, saved=first["issues"])
    assert out["removed"] == ["HOME-02", "LIB-01"]
    assert "close" not in again.ops()


def test_the_map_round_trips_beside_the_script(tmp_path):
    path = tmp_path / "play.gui-test.issues.json"
    tts.save_map(path, {"HOME-01": 7})
    assert tts.load_map(path) == {"HOME-01": 7}
    assert json.loads(path.read_text())["HOME-01"] == 7
    assert tts.load_map(tmp_path / "absent.json") == {}


# --- writing a run's results back ---

def _run(results, **over):
    r = {"run_id": "r-1", "script_sha": "abc", "platform": "macos-arm64", "build_sha": "def",
         "core_available": True, "results": [{"id": i, "verdict": v} for i, v in results]}
    r.update(over)
    return r


def _published():
    plan = tts.build_plan(covered_script(), markdown=MARKDOWN)
    gh = FakeGitHub()
    return plan, tts.publish(plan, gh)


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


def test_many_failed_steps_open_exactly_one_bug_listing_them():
    plan, pub = _published()
    gh = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "failed (timeout)"), ("HOME-02", "failed (timeout)")]),
                            gh, published=pub)
    assert len(gh.of("file_bug")) == 1
    body = gh.of("file_bug")[0]["body"]
    assert "HOME-01" in body and "HOME-02" in body
    assert out["bug"] == "https://example.test/bug/1"


def test_needs_review_pending_and_a_manual_cases_pending_are_never_a_bug():
    s = covered_script()
    s["batches"][0]["steps"][0]["mode"] = "manual"        # the runner puts a manual step to a person: it ends `pending`
    plan = tts.build_plan(s, markdown=MARKDOWN)
    gh = FakeGitHub()
    pub = tts.publish(plan, gh)
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
    pub = tts.publish(plan, gh)
    again = FakeGitHub()
    out = tts.write_results(plan, _run([("HOME-01", "passed"), ("HOME-02", "failed (x)"), ("LIB-01", "passed")]),
                            again, published=pub)
    assert out["counts"]["failed"] == 1
    assert len(again.of("comment")) == 4          # 3 batches + the parent


# --- the whole flow ---

def test_the_scenario_stops_after_publishing_when_the_run_command_does_not_exist():
    gh = FakeGitHub()
    out = tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=None)
    assert out["run"] is None and out["reason"] == "run command unavailable"
    assert out["published"]["parent"] == 101          # the parent is the first issue created
    assert gh.ops().count("create_issue") == 4
    assert "comment" not in gh.ops()


def test_the_scenario_publishes_then_runs_and_writes_every_verdict_back():
    gh, cmd = FakeGitHub(), _cmd(_run([("HOME-01", "passed"), ("HOME-02", "passed"), ("LIB-01", "failed (x)")]))
    out = tts.run_scenario(MARKDOWN, covered_script(), gh, run_command=cmd)
    assert out["counts"]["failed"] == 1 and out["bug"] == "https://example.test/bug/1"
    assert [c["script"] for c in cmd.calls] == ["tiny"]


def test_the_scenario_reports_a_gap_and_publishes_nothing():
    gh = FakeGitHub()
    out = tts.run_scenario(MARKDOWN, script(batches=[{"id": "b", "setup": [], "teardown": [],
                                                      "steps": [_step("NOPE-01")]}]), gh, run_command=None)
    assert out["gaps"]["missing_in_script"] == ["HOME-01", "HOME-02", "LIB-01"]
    assert gh.calls == []


def test_the_scenario_reuses_the_saved_map_on_a_second_invocation():
    first, second = FakeGitHub(), FakeGitHub()
    a = tts.run_scenario(MARKDOWN, covered_script(), first, run_command=None, map_path=None)
    out = tts.run_scenario(MARKDOWN, covered_script(), second, run_command=None,
                           saved=a["published"]["issues"], parent=a["published"]["parent"])
    assert "create_issue" not in second.ops() and out["published"]["issues"] == a["published"]["issues"]


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
    gh("create_issue", title="Test case HOME-01", body="body", labels=["test-case", "wave:01"])
    gh("add_sub_issue", parent=7, sub=99)
    assert "repos/sbihaiko/MesenAI/issues" in sh.args[0]
    assert "=Test case HOME-01" in " ".join(sh.args[0]) and "labels[]=test-case" in sh.args[0]
    link = " ".join(sh.args[1])
    assert "repos/sbihaiko/MesenAI/issues/7/sub_issues" in link and "sub_issue_id=99" in link


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
    assert out == {"url": None}


class _cmd:
    """A stand-in for #1222's run command: a script name in, a run report out."""

    def __init__(self, report):
        self.report = report
        self.calls = []

    def __call__(self, script):
        self.calls.append({"script": script["name"]})
        return self.report
