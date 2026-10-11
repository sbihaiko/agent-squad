"""Filing a review's DEBT findings as board tasks (ADR-0273 Decision 4), against a stub `gh`."""
import json
import subprocess

from squad.debt_filing import file_debt

REVIEW = """VERDICT: CHANGES
## Blocking
- src/a.py:10 — null deref on empty input — an empty list raises TypeError — return [] when the input is empty
## Debt
- src/b.py:42 — retry loop has no backoff — it hammers the API after a 500 — retries wait with exponential backoff, covered by a test
- src/c.py:7
  — dead flag `legacy` — the flag has no reader — the flag and its branches are gone
"""

LEGACY = """Review of PR #77
- BLOCKING src/a.py:10 — null deref on empty input. Acceptance: empty input returns [].
- DEBT src/b.py:42 — retry loop has no backoff. Acceptance: retries wait with exponential backoff, covered by a test.
- DEBT src/c.py:7 — dead flag `legacy`. Acceptance: the flag and its branches are gone.
"""


class StubGh:
    def __init__(self, labels=()):
        self.labels, self.issues, self.calls = set(labels), [], []

    def __call__(self, args, input=None):
        self.calls.append(args)
        path = next(a for a in args if a.startswith("repos/"))
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        if method == "GET" and "/labels/" in path:
            if path.rsplit("/", 1)[1] not in self.labels:
                raise subprocess.CalledProcessError(1, args, stderr="Not Found")
            return "{}"
        if method == "POST" and path.endswith("/labels"):
            self.labels.add(json.loads(input)["name"])
            return "{}"
        if method == "POST" and path.endswith("/issues"):
            body = json.loads(input)
            self.issues.append(body)
            return json.dumps({"number": len(self.issues), "html_url": f"https://x/{len(self.issues)}"})
        if method == "GET" and "/issues" in path:
            return json.dumps([{"number": i + 1, "body": b["body"]} for i, b in enumerate(self.issues)])
        raise AssertionError(args)


def test_files_one_issue_per_debt_finding_and_none_for_blocking():
    gh = StubGh()
    filed = file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    assert len(filed) == 2 and len(gh.issues) == 2
    assert not any("null deref" in i["body"] for i in gh.issues)


def test_a_bullet_written_over_two_lines_is_one_finding():
    """#1777: a bullet is at most two lines, and the second one belongs to the finding above it."""
    gh = StubGh()
    file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    bare = [i for i in gh.issues if "src/c.py:7" in i["body"]]
    assert len(bare) == 1
    assert "dead flag `legacy`" in bare[0]["body"] and "the flag has no reader" in bare[0]["body"]


def test_a_review_saved_before_the_checklist_shape_still_files():
    """A review written by the older prompt (an inline DEBT tag, no sections) is still readable."""
    gh = StubGh()
    filed = file_debt("o/r", 77, 70, LEGACY, "abc123", runner=gh)
    assert len(filed) == 2 and not any("null deref" in i["body"] for i in gh.issues)


def test_body_carries_pr_location_defect_and_acceptance_and_labels():
    gh = StubGh()
    file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    first = gh.issues[0]
    for needle in ("#77", "#70", "src/b.py:42", "retry loop has no backoff",
                   "retries wait with exponential backoff, covered by a test"):
        assert needle in first["body"], needle
    assert first["labels"] == ["debt"]
    assert "ready-for-agent" not in first["labels"]
    assert "debt" in gh.labels   # created because it was absent


def test_existing_debt_label_is_not_recreated():
    gh = StubGh(labels={"debt"})
    file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    assert not any("--method" in c and "POST" in c and c[-1].endswith("/labels") for c in gh.calls)


def test_existing_debt_plus_ready_for_agent_issue_is_still_a_duplicate():
    """An older filer tagged both labels; the duplicate check still keys on `debt`."""
    from squad.debt_filing import MARK, key, parse

    gh = StubGh(labels={"debt"})
    first = parse(REVIEW)[0]
    gh.issues.append({
        "title": "Debt: retry loop has no backoff (src/b.py:42)",
        "body": f"already filed\n<!-- {MARK}{key(first)} -->\n",
        "labels": ["debt", "ready-for-agent"],
    })
    filed = file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    assert all("retry" not in (u or "").lower() for u in filed)
    assert sum(1 for i in gh.issues if MARK + key(first) in i["body"]) == 1
    assert any("dead flag" in i["body"] for i in gh.issues)


def test_second_run_same_head_is_a_noop_and_new_head_files_once():
    gh = StubGh()
    file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    assert file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh) == []
    moved = REVIEW.replace("src/b.py:42", "src/b.py:50")   # the same finding, a line later, on a new head
    assert file_debt("o/r", 77, 70, moved, "def456", runner=gh) == []
    assert len(gh.issues) == 2
