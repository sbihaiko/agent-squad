"""A run's children become sub-issues of one parent that names the run (#304).

A run that dispatches more than one child gets one parent issue, titled for the run, and each child is linked
to it as a sub-issue, so the platform rolls the run's progress up. The link is written through the seam and
proven by the read that follows; that read may lag, so it is asked again a bounded number of times. The
carrier is a stub gh that keeps issues and their sub-issues, the way the REST routes do.
"""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_run_parent", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

RUN = "run-20261014-030735-11d7af"


class Repo:
    """A stub gh over one repository. `lag` is how many sub-issue reads answer without the links the writes made;
    `drop` makes a link write answer ok and never land."""

    def __init__(self, lag=0, drop=False, existing=()):
        self.issues = {n: {"id": 1000 + n, "number": n, "title": t} for n, t in existing}
        self.subs = {}
        self.lag, self.drop = lag, drop
        self.created, self.link_posts, self.sub_reads, self.list_reads = [], [], 0, 0

    def _jq(self, cmd, rows):
        jq = cmd[cmd.index("--jq") + 1]
        return subprocess.run(["jq", "-r", jq], input=json.dumps(rows), capture_output=True, text=True, check=True).stdout

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        post = "POST" in cmd
        out = ""
        if "/sub_issues" in s:
            n = int(s.split("/issues/")[1].split("/")[0])
            if post:
                body = json.loads(input) if input else {}
                sid = body.get("sub_issue_id") if body else int(next(c for c in cmd if c.startswith("sub_issue_id=")).split("=")[1])
                self.link_posts.append((n, sid))
                if not self.drop:
                    self.subs.setdefault(n, []).append(next(i for i in self.issues.values() if i["id"] == sid))
                out = json.dumps(self.issues[n])
            else:
                self.sub_reads += 1
                rows = [] if self.sub_reads <= self.lag else self.subs.get(n, [])
                out = self._jq(cmd, rows)
        elif post and re.search(r"repos/x/y/issues( |$)", s):
            body = json.loads(input)
            n = max(self.issues, default=0) + 1
            self.issues[n] = {"id": 1000 + n, "number": n, "title": body["title"]}
            self.created.append(body["title"])
            out = json.dumps(self.issues[n])
        elif "/issues/" in s:
            out = json.dumps(self.issues[int(s.split("/issues/")[1].split()[0])])
        elif "/issues?" in s:
            self.list_reads += 1
            out = self._jq(cmd, list(self.issues.values()))
        return subprocess.CompletedProcess(cmd, 0, out, "")


def repo_with(n=(10, 11, 12), **kw):
    r = Repo(**kw)
    for i in n:
        r.issues[i] = {"id": 1000 + i, "number": i, "title": f"child {i}"}
    return r


def seam(tmp_path, repo):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=repo, env={"SG_BOARD_READ_BACKOFF": "0"})


def test_two_children_link_to_one_parent_that_names_the_run(tmp_path):
    repo = repo_with()
    parent = seam(tmp_path, repo).group_run(RUN, [10, 11])

    assert repo.created == [f"Squad run {RUN}"], "one parent, titled for the run"
    assert repo.issues[parent]["title"] == f"Squad run {RUN}"
    assert [c["number"] for c in repo.subs[parent]] == [10, 11]
    assert {sid for _, sid in repo.link_posts} == {1010, 1011}, "the write names the child's id, never its number"


def test_one_child_creates_no_parent(tmp_path):
    repo = repo_with()
    state = seam(tmp_path, repo)

    assert state.group_run(RUN, [10]) is None
    assert state.group_run(RUN, [10, 10]) is None, "one issue named twice is still one child"
    assert repo.created == [] and repo.link_posts == []


def test_an_existing_parent_is_reused_and_a_linked_child_is_not_linked_again(tmp_path):
    repo = repo_with(existing=[(5, f"Squad run {RUN}")])
    state = seam(tmp_path, repo)

    assert state.group_run(RUN, [10, 11]) == 5
    assert state.group_run(RUN, [10, 11, 12]) == 5

    assert repo.created == [], "the run's parent exists: no second one"
    assert [c["number"] for c in repo.subs[5]] == [10, 11, 12]
    assert [sid for _, sid in repo.link_posts].count(1010) == 1


def test_a_read_that_lags_behind_the_write_is_asked_again(tmp_path):
    repo = repo_with(lag=2)
    parent = seam(tmp_path, repo).group_run(RUN, [10, 11])

    assert [c["number"] for c in repo.subs[parent]] == [10, 11]
    assert repo.sub_reads > 2


def test_a_link_no_read_carries_is_named(tmp_path):
    repo = repo_with(drop=True)

    with pytest.raises(issue_state.NotCarried, match="issue 10.*sub-issue"):
        seam(tmp_path, repo).group_run(RUN, [10, 11])


def test_a_child_that_is_a_run_parent_is_refused(tmp_path):
    repo = repo_with(existing=[(7, "Squad run run-20261014-010101-aaaaaa")])
    state = seam(tmp_path, repo)

    with pytest.raises(issue_state.RunParentChild, match="#7"):
        state.group_run(RUN, [10, 7])

    assert repo.link_posts == [], "nothing is linked when one child is a run parent"


def test_a_parent_whose_links_did_not_land_is_found_again_without_paging_the_issues(tmp_path):
    repo = repo_with(drop=True)
    state = seam(tmp_path, repo)
    with pytest.raises(issue_state.NotCarried):
        state.group_run(RUN, [10, 11])
    reads = repo.list_reads
    repo.drop = False

    parent = state.group_run(RUN, [10, 11])

    assert repo.created == [f"Squad run {RUN}"], "the retry reuses the parent, never creates a second"
    assert repo.list_reads == reads, "the parent is known from the first pass: no page of every issue"
    assert [c["number"] for c in repo.subs[parent]] == [10, 11]
