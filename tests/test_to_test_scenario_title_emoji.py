"""#1419: a test-case sub-issue carries its latest verdict as a leading title emoji, driven through the real REST
runner against a stub `gh` that models issues, so what is asserted is the title GitHub would hold (and every edit sent)."""
import json

from squad import to_test_scenario as tts

REPO = "o/r"


class StubGh:
    """`gh api` over a dict of issues: titles, states, comments, and a log of every PATCH that touched a title."""

    def __init__(self, issues=None):
        self.issues = {n: {"title": t, "state": "open"} for n, t in (issues or {}).items()}
        self.comments = []
        self.title_edits = []
        self.next = 100

    @staticmethod
    def _fields(args):
        out = {}
        for flag, value in zip(args, args[1:], strict=False):
            if flag in ("-f", "-F") and "=" in value:
                key, _, val = value.partition("=")
                out[key] = val
        return out

    def __call__(self, args, **kw):
        if args[:2] == ["gh", "label"]:
            return ""
        assert args[:2] == ["gh", "api"], args
        method = args[3] if args[2] == "--method" else "GET"
        path = next(a for a in args if a.startswith(f"repos/{REPO}/"))[len(f"repos/{REPO}/"):]
        fields = self._fields(args)
        parts = path.split("/")
        if method == "POST" and path == "issues":
            self.next += 1
            self.issues[self.next] = {"title": fields["title"], "state": "open"}
            return json.dumps({"number": self.next, "id": 900 + self.next})
        if method == "POST" and parts[-1] == "sub_issues":
            return "{}"
        if method == "POST" and parts[-1] == "comments":
            self.comments.append((int(parts[1]), fields["body"]))
            return "{}"
        if method == "GET" and parts[-1] == "comments":
            return json.dumps([{"body": b} for n, b in self.comments if n == int(parts[1])])
        if method == "GET":
            return json.dumps(self.issues[int(parts[1])])
        if method == "PATCH":
            issue = self.issues[int(parts[1])]
            if "title" in fields:
                if fields["title"] != issue["title"]:
                    self.title_edits.append((int(parts[1]), fields["title"]))
                issue["title"] = fields["title"]
            issue["state"] = fields.get("state", issue["state"])
            return json.dumps(issue)
        raise AssertionError(args)

    def title(self, number):
        return self.issues[number]["title"]


def run_of(**verdicts):
    return {"run_id": "r1", "results": [{"id": k.replace("_", "-"), "verdict": v} for k, v in verdicts.items()]}


def plan_of(*ids):
    return {"name": "home", "issues": [{"cases": [i]} for i in ids]}


def published_of(stub, ids):
    return {"parent": 1, "issues": {i: n for i, n in zip(ids, range(11, 11 + len(ids)), strict=True)}}


def seeded(titles):
    stub = StubGh({1: "Scenario", **{11 + i: t for i, t in enumerate(titles)}})
    return stub, tts.rest_runner(REPO, shell=stub)


def test_verdict_sets_the_prefix_and_leaves_unexecuted_cases_alone():
    stub, gh = seeded(["⚪ Test case A-01", "❌ Test case A-02"])
    tts.write_results(plan_of("A-01", "A-02"), run_of(A_01="passed"), gh, published_of(stub, ["A-01", "A-02"]))
    assert stub.title(11) == "✅ Test case A-01"
    assert stub.title(12) == "❌ Test case A-02"


def test_every_verdict_maps_to_its_emoji():
    ids = ["A-01", "A-02", "A-03", "A-04"]
    stub, gh = seeded([f"⚪ Test case {i}" for i in ids])
    run = run_of(A_01="passed", A_02="failed", A_03="needs review", A_04="pending")
    tts.write_results(plan_of(*ids), run, gh, published_of(stub, ids))
    assert [stub.title(n)[0] for n in (11, 12, 13, 14)] == ["✅", "❌", "🟡", "👤"]


def test_a_changed_verdict_replaces_the_prefix_and_never_stacks():
    stub, gh = seeded(["⚪ Test case A-01"])
    for verdict in ("failed", "passed", "failed"):
        tts.write_results(plan_of("A-01"), run_of(A_01=verdict), gh, published_of(stub, ["A-01"]))
    assert stub.title(11) == "❌ Test case A-01"


def test_the_same_verdict_twice_makes_no_title_edit():
    stub, gh = seeded(["⚪ Test case A-01"])
    tts.write_results(plan_of("A-01"), run_of(A_01="passed"), gh, published_of(stub, ["A-01"]))
    assert len(stub.title_edits) == 1
    tts.write_results(plan_of("A-01"), run_of(A_01="passed"), gh, published_of(stub, ["A-01"]))
    assert len(stub.title_edits) == 1


def test_a_title_without_a_prefix_gets_the_verdict_emoji():
    stub, gh = seeded(["Test case A-01"])
    tts.write_results(plan_of("A-01"), run_of(A_01="failed"), gh, published_of(stub, ["A-01"]))
    assert stub.title(11) == "❌ Test case A-01"


def test_a_new_publish_starts_with_the_not_run_emoji():
    stub = StubGh()
    gh = tts.rest_runner(REPO, shell=stub)
    plan = {"parent": {"title": "Scenario", "body": "b", "labels": []},
            "issues": [{"kind": "case", "id": "A-01", "cases": ["A-01"], "title": "Test case A-01", "body": "b",
                        "labels": [tts.CASE_LABEL], "hash": "h"}]}
    out = tts.publish(plan, gh, board=True)
    assert stub.title(out["issues"]["A-01"]) == "⚪ Test case A-01"


def test_republish_backfills_an_unprefixed_title_once_and_keeps_a_verdict_prefix():
    stub = StubGh({1: "Scenario", 11: "Test case A-01", 12: "✅ Test case A-02"})
    gh = tts.rest_runner(REPO, shell=stub)
    plan = {"parent": {"title": "Scenario", "body": "b", "labels": []},
            "issues": [{"kind": "case", "id": i, "cases": [i], "title": f"Test case {i}", "body": "b",
                        "labels": [tts.CASE_LABEL], "hash": "h"} for i in ("A-01", "A-02")]}
    saved = {"parent": 1, "boarded": True, "cases": {"A-01": {"number": 11}, "A-02": {"number": 12}}}
    tts.publish(plan, gh, saved=saved, board=True)
    assert (stub.title(11), stub.title(12)) == ("⚪ Test case A-01", "✅ Test case A-02")


def test_the_scenario_gets_a_one_line_summary_with_counts_and_the_results_link():
    stub, gh = seeded(["⚪ Test case A-01", "⚪ Test case A-02", "⚪ Test case A-03"])
    run = run_of(A_01="passed", A_02="failed")
    run["results_url"] = "https://example.test/results/r1"
    tts.write_results(plan_of("A-01", "A-02", "A-03"), run, gh, published_of(stub, ["A-01", "A-02", "A-03"]))
    body = next(b for n, b in stub.comments if n == 1)
    line = body.splitlines()[0]
    assert "1 passed" in line and "1 failed" in line and "0 needs review" in line
    assert "0 manual" in line and "1 not run" in line and "https://example.test/results/r1" in line
