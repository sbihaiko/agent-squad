"""ADR-0275 slice F (MesenAI #1477): `write_back` updates the case records and keeps one bug per failing case.

Driven through `publish` and `write_back` with a runner that models issues, comments and bugs. No network.
"""
import pytest

from squad import to_test_scenario as tts
from tests.test_gui_test_scenario import script
from tests.test_to_test_scenario_areas import batch
from tests.test_to_test_scenario_write_back_areas import RunGitHub, results


class BugGitHub(RunGitHub):
    """The run runner plus records' title/state edits, comments and the bug helper."""

    def __init__(self):
        super().__init__()
        self.comments = {}

    def __call__(self, op, **kw):
        if op in ("retitle", "set_title"):
            self.calls.append((op, kw))
            self.issues[kw["number"]]["title"] = kw["title"]
            return {}
        if op == "reopen":
            self.calls.append((op, kw))
            self.issues[kw["number"]]["state"] = "open"
            return {}
        if op == "comment":
            self.calls.append((op, kw))
            self.comments.setdefault(kw["number"], []).append(kw["body"])
            return {}
        if op == "file_bug":
            self.calls.append((op, kw))
            self.next += 1
            self.issues[self.next] = {"id": 900 + self.next, "title": kw["title"], "body": kw["body"],
                                      "state": "open", "priority": kw["priority"], "bug": True}
            return {"number": self.next, "url": f"https://example.test/{self.next}"}
        if op == "search_open_bug":
            self.calls.append((op, kw))
            hit = next((n for n, i in self.issues.items()
                        if i.get("bug") and i["state"] == "open" and i["title"] == kw["title"]), None)
            return None if hit is None else {"number": hit, "title": kw["title"], "url": None}
        return super().__call__(op, **kw)

    def bugs(self):
        return {n: i for n, i in self.issues.items() if i.get("bug")}


@pytest.fixture
def tree(tmp_path):
    plan = tts.build_plan(script(batches=[batch("aa-x", 3), batch("bb-x", 2)]))
    gh, path = BugGitHub(), tmp_path / "m.gui-test.issues.json"
    out = tts.publish(plan, gh, map_path=path)
    return gh, path, out["map"]["cases"]


def run(tmp_path, gh, path, name, verdicts):
    return tts.write_back(results(tmp_path, name, f"2026-10-11T0{len(gh.calls) % 9}:00:00-0300", verdicts), path, gh)


def record(gh, cases, case):
    return gh.issues[cases[case]["number"]]


def test_a_failure_reopens_the_record_with_the_red_emoji_and_a_pass_closes_it_with_the_green_one(tmp_path, tree):
    gh, path, cases = tree
    gh.issues[cases["AAX-001"]["number"]]["state"] = "closed"
    run(tmp_path, gh, path, "r1", {"AAX-001": "failed"})
    held = record(gh, cases, "AAX-001")
    assert held["state"] == "open" and held["title"].startswith("❌ ")
    run(tmp_path, gh, path, "r2", {"AAX-001": "passed"})
    assert held["state"] == "closed" and held["title"].startswith("✅ ")
    run(tmp_path, gh, path, "r3", {"AAX-001": "needs review"})
    assert held["state"] == "open" and held["title"].startswith("🟡 ")
    assert not any(b for n, b in gh.comments.items() if n == cases["AAX-001"]["number"])   # no per-run comment


def test_a_hand_closed_pending_record_stays_closed_and_untouched(tmp_path, tree):
    gh, path, cases = tree
    held = record(gh, cases, "BBX-002")
    held["state"] = "closed"
    before = dict(held)
    run(tmp_path, gh, path, "r1", {"BBX-002": "pending", "AAX-001": "skipped"})
    assert held == before
    assert not [c for c in gh.of("reopen") if c["number"] == cases["BBX-002"]["number"]]
    assert not [c for c in gh.of("retitle") + gh.of("set_title") if c["number"] == cases["BBX-002"]["number"]]


def test_a_failing_case_gets_exactly_one_bug_with_the_priority_of_its_severity(tmp_path, tree):
    gh, path, cases = tree
    run(tmp_path, gh, path, "r1", {"AAX-001": "failed", "AAX-002": "failed"})
    run(tmp_path, gh, path, "r2", {"AAX-001": "failed", "AAX-002": "failed"})
    bugs = gh.bugs()
    assert len(bugs) == 2
    first = next(b for b in bugs.values() if b["title"].endswith("· AAX-001"))
    assert first["title"] == "gui-test ❌ tiny · AAX-001"
    assert first["priority"] == "P1"
    assert f"Bug: #{next(n for n, b in bugs.items() if b is first)}" in record(gh, cases, "AAX-001")["body"]


@pytest.mark.parametrize("severity,priority", [("critical", "P0"), ("major", "P1"), ("minor", "P2"), ("nope", "P2")])
def test_priority_follows_severity(tmp_path, tree, severity, priority):
    gh, path, cases = tree
    held = record(gh, cases, "AAX-001")
    held["body"] = held["body"].replace("**Severity:** major", f"**Severity:** {severity}")
    run(tmp_path, gh, path, "r1", {"AAX-001": "failed"})
    assert next(iter(gh.bugs().values()))["priority"] == priority


def test_second_fail_comments_next_pass_closes_and_a_later_fail_files_a_new_bug_linking_the_closed_one(tmp_path, tree):
    gh, path, cases = tree
    run(tmp_path, gh, path, "r1", {"AAX-001": "failed"})
    (bug,) = gh.bugs()
    run(tmp_path, gh, path, "r2", {"AAX-001": "failed"})
    assert len(gh.bugs()) == 1 and len(gh.comments[bug]) == 1
    run(tmp_path, gh, path, "r3", {"AAX-001": "passed"})
    assert gh.issues[bug]["state"] == "closed" and "r3" in gh.comments[bug][-1]
    run(tmp_path, gh, path, "r4", {"AAX-001": "failed"})
    again = [n for n in gh.bugs() if n != bug]
    assert len(again) == 1 and gh.issues[again[0]]["state"] == "open"
    assert f"#{bug}" in gh.issues[again[0]]["body"]
    assert f"Bug: #{again[0]}" in record(gh, cases, "AAX-001")["body"]


def test_the_open_bug_is_found_by_title_when_the_record_names_none(tmp_path, tree):
    gh, path, cases = tree
    run(tmp_path, gh, path, "r1", {"AAX-001": "failed"})
    (bug,) = gh.bugs()
    held = record(gh, cases, "AAX-001")
    held["body"] = "\n".join(l for l in held["body"].splitlines() if not l.startswith("Bug: #"))
    path.unlink()                                                   # nothing but GitHub remembers the bug
    tts.publish(tts.build_plan(script(batches=[batch("aa-x", 3), batch("bb-x", 2)])), gh, map_path=path)
    run(tmp_path, gh, path, "r2", {"AAX-001": "failed"})
    assert len(gh.bugs()) == 1 and len(gh.comments[bug]) == 1


def test_start_run_no_longer_passes_file_bug(tmp_path):
    runner = tmp_path / "fake.py"
    runner.write_text("import sys\nopen(sys.argv[sys.argv.index('--out') + 1] + '.argv', 'w').write(' '.join(sys.argv))\n")
    scr = tmp_path / "demo.gui-test.json"
    scr.write_text("{}")
    out = tts.start_run(scr, command=[__import__("sys").executable, str(runner)], out_dir=tmp_path / "o")
    import time
    argv = tmp_path / "o" / (out["results"].rsplit("/", 1)[-1] + ".argv")
    for _ in range(40):
        if argv.exists():
            break
        time.sleep(0.25)
    assert "--file-bug" not in argv.read_text()
