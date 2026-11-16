"""Filing a review's DEBT findings as board tasks (ADR-0273 Decision 4), against a stub `gh`."""
import json
import re
import subprocess

import squad.debt_filing as debt_filing
from squad.debt_filing import file_debt

REVIEW = """VERDICT: CHANGES
## Blocking
- src/a.py:10 — null deref on empty input — an empty list raises TypeError — return [] when the input is empty
## Debt
- src/b.py:42 — retry loop has no backoff — it hammers the API after a 500 — retries wait with exponential backoff, covered by a test
- src/c.py:7
  — dead flag `legacy` — the flag has no reader — the flag and its branches are gone
"""

FIX_ROUND = """VERDICT: APPROVE
## Resolved
- src/a.py:10 — null deref on empty input — fixed: the input is validated now — tests/a.py::empty
## Debt
- src/b.py:42 — retry loop has no backoff — it hammers the API after a 500 — retries wait with exponential backoff, covered by a test
- src/c.py:7 — dead flag `legacy` — fixed: the flag and its branches are gone — tests/c.py::dead
"""

LEGACY = """Review of PR #77
- BLOCKING src/a.py:10 — null deref on empty input. Acceptance: empty input returns [].
- DEBT src/b.py:42 — retry loop has no backoff. Acceptance: retries wait with exponential backoff, covered by a test.
- DEBT src/c.py:7 — dead flag `legacy`. Acceptance: the flag and its branches are gone.
"""


class StubGh:
    def __init__(self, labels=()):
        self.labels, self.issues, self.calls = set(labels), [], []
        self.opened, self.moved, self.on_board = [], [], set()   # items created, the Status set, what the board holds

    def __call__(self, args, input=None):
        self.calls.append(args)
        path = next((a for a in args if a.startswith(("repos/", "users/", "/users/"))), "")
        flag = next((f for f in ("--method", "-X") if f in args), None)
        method = args[args.index(flag) + 1] if flag else "GET"
        if method == "GET" and path == "users/o":
            return json.dumps({"type": "User"})
        if method == "GET" and "projectsV2/4/fields" in path:
            return json.dumps([{"name": "Status", "id": "F1", "options": [{"name": "Todo", "id": "O1"}]}])
        if method == "GET" and re.fullmatch(r"repos/o/r/issues/\d+", path):
            n = int(path.rsplit("/", 1)[1])
            return json.dumps({"number": n, "id": 9000 + n})
        if method == "GET" and re.fullmatch(r".*projectsV2/4/items/PVTI_\d+", path):   # one card, the REST single-item route (#1303)
            n = int(path.rsplit("PVTI_", 1)[1]) - 9000
            status = dict((int(i.removeprefix("PVTI_")) - 9000, v) for i, v in self.moved)
            name = {"O1": "Todo"}.get(status.get(n), status.get(n))   # the write sends the option id, the read answers the name
            fields = [{"name": "Status", "value": {"name": name}}] if name else []
            return json.dumps({"id": f"PVTI_{9000 + n}", "isArchived": False, "content": {"number": n}, "fields": fields})
        if method == "GET" and "projectsV2/4/items" in path and "@tsv" in args[-1]:   # the seam's state read
            status = dict((int(i.removeprefix("PVTI_")) - 9000, v) for i, v in self.moved)
            return "".join(f"{n}\t{status.get(n, '')}\thttps://api.github.com/repos/o/r\n" for n in sorted(self.on_board))
        if method == "GET" and "projectsV2/4/items" in path:   # the seam's item id map
            return "".join(json.dumps([n, f"PVTI_{9000 + n}", "https://api.github.com/repos/o/r"]) + "\n"
                           for n in sorted(self.on_board))
        if method == "POST" and path.endswith("/items"):
            self.opened.append(json.loads(input)["id"])
            self.on_board.add(json.loads(input)["id"] - 9000)
            return json.dumps({"id": f"PVTI_{json.loads(input)['id']}"})
        if method == "PATCH" and "/items/" in path:
            self.moved.append((path.rsplit("/", 1)[1], json.loads(input)["fields"][0]["value"]))
            return "{}"
        if method == "GET" and "/labels/" in path:
            if path.rsplit("/", 1)[1] not in self.labels:
                raise subprocess.CalledProcessError(1, args, stderr="Not Found")
            return "{}"
        if method == "POST" and path.endswith("/labels"):
            self.labels.add(json.loads(input)["name"])
            return "{}"
        if method == "POST" and path.endswith("/issues"):
            body = json.loads(input)
            body["number"] = max((b.get("number", 0) for b in self.issues), default=0) + 1
            self.issues.append(body)
            return json.dumps({"number": body["number"], "html_url": f"https://x/{body['number']}"})
        if method == "GET" and "/issues" in path:
            return json.dumps([{"number": b.get("number", i + 1), "body": b["body"]} for i, b in enumerate(self.issues)])
        if "graphql" in args:   # the card of an issue the items route omitted (#1463), read from the same board state
            n = int(next(a for a in args if a.startswith("number=")).partition("=")[2])
            status = dict((int(i.removeprefix("PVTI_")) - 9000, v) for i, v in self.moved)
            nodes = [{"databaseId": 9000 + n, "isArchived": False, "project": {"number": 4},
                      "fieldValueByName": {"optionId": status[n]} if n in status else None}] if n in self.on_board else []
            return json.dumps({"data": {"repository": {"issue": {"projectItems": {"nodes": nodes}}}}})
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


def test_a_resolved_or_answered_finding_is_never_filed():
    """#1834: the filer asks revformat.py what a finding is, the same predicate the severity counts and the
    `--summary` line read. A bullet under `## Resolved` and one answered `fixed:` are answers, so neither is
    filed; the Debt bullet that is not fixed still is."""
    gh = StubGh()
    filed = file_debt("o/r", 77, 70, FIX_ROUND, "abc123", runner=gh)
    assert len(filed) == 1 and len(gh.issues) == 1
    assert "retry loop has no backoff" in gh.issues[0]["body"]
    assert not any("dead flag" in i["body"] for i in gh.issues)


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
    assert first["labels"] == ["debt", "enhancement"]
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


def test_the_filer_puts_each_issue_it_opens_on_the_board(monkeypatch, tmp_path):
    """#1838: the filer mints the card as it files the issue, the way report-bug.sh does for a bug.

    A filed task with no card is invisible to the frontier and to the close passes, which read the board, and
    board.py answers every later move for it with `#<n> not on the board` on stderr and rc 1 while the caller's
    action line still reads as success -- so it sits open forever. The board comes from the loop's own
    SG_OWNER/SG_PROJECT, the same pair every other board move uses."""
    monkeypatch.setenv("SG_OWNER", "o")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))   # the seam's caches, never the live loop's
    gh = StubGh()
    monkeypatch.setattr(debt_filing, "_sh", gh)
    review = tmp_path / "review.md"
    review.write_text(REVIEW, encoding="utf-8")
    rc = debt_filing.main(["--repo", "o/r", "--pr", "77", "--issue", "70",
                           "--head", "abc123", "--review", str(review)])
    assert rc == 0
    assert gh.opened == [9001, 9002]                                # one card per filed issue
    assert gh.moved == [("PVTI_9001", "O1"), ("PVTI_9002", "O1")]   # Status Todo: dispatchable without a human


def test_an_issue_already_filed_without_a_card_gets_its_card_on_a_later_run(monkeypatch, tmp_path):
    """#1838: the repair half. #1826/#1827 were filed by a run that could not mint a card, and every later run
    answered "no new DEBT findings" and walked past them -- so the merge got its filing and the issue stayed
    invisible to the frontier and to the close passes for good."""
    from squad.debt_filing import MARK, key, parse

    monkeypatch.setenv("SG_OWNER", "o")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))   # the seam's caches, never the live loop's
    gh = StubGh(labels={"debt"})
    first = parse(REVIEW)[0]
    gh.issues.append({"number": 5, "title": "Debt: retry loop has no backoff (src/b.py:42)",
                      "body": f"filed long ago\n<!-- {MARK}{key(first)} -->\n", "labels": ["debt"]})
    monkeypatch.setattr(debt_filing, "_sh", gh)
    review = tmp_path / "review.md"
    review.write_text(REVIEW, encoding="utf-8")
    assert debt_filing.main(["--repo", "o/r", "--pr", "77", "--issue", "70",
                             "--head", "abc123", "--review", str(review)]) == 0
    assert gh.opened == [9005, 9006]              # the already-filed issue and the newly filed one, once each
    assert len(gh.issues) == 2                    # the other finding was filed; the debt was not filed twice


def test_second_run_same_head_is_a_noop_and_new_head_files_once():
    gh = StubGh()
    file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh)
    assert file_debt("o/r", 77, 70, REVIEW, "abc123", runner=gh) == []
    moved = REVIEW.replace("src/b.py:42", "src/b.py:50")   # the same finding, a line later, on a new head
    assert file_debt("o/r", 77, 70, moved, "def456", runner=gh) == []
    assert len(gh.issues) == 2
