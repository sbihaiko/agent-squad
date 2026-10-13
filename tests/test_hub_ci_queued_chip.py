"""The hub's CI chip: a queued check is not a run, and a queue nobody picks up is not progress (#149).

No CI job started: every check GitHub reported for every head read `queued` (`check (3.11) queued`, `check (3.13)
queued`), and `pr_state` folded that word into `CI running` -- a claim about a run in progress that nobody could
confirm, which ADR-0018 refuses. The chip now reads the word the check carries: `CI queued` while the head is young,
and `CI cannot run` once the head has sat queued past the timeout the job gives itself in `.github/workflows/ci.yml`.
The age comes from GitHub's own `committedDate` of the head commit: a fact the query returns, never a guess, so a row
without it reads queued and is never called a stop.
"""
import datetime as dt
import importlib.util
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SHA = "1e38e5a7b41c0d2e"          # current head of the PR under test
SHA9 = SHA[:9]
JOB_TIMEOUT_MIN = 20             # the timeout-minutes the ci job gives itself: past it a queued check never started


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_ci_queued_chip", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A squad state dir with no loop files: the chip under test is the CI one, and nothing else may stand in for it."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    monkeypatch.setattr(hub, "STATE", st)
    monkeypatch.delenv("SG_REREVIEW_MINUTES", raising=False)
    return st


def _iso(epoch):
    """A GitHub timestamp: committedDate as the query returns it (UTC, Z)."""
    return dt.datetime.fromtimestamp(epoch, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(ci, hc=None, n=1491, head=SHA):
    """One open PR as the batched query returns it; `ci` is the list of check states, `hc` the head commit's date."""
    return {"n": n, "t": "CI never starts: every run sits queued", "u": "2026-10-11T09:00:00Z", "o": "2026-10-11T08:00:00Z",
            "h": head, "d": False, "ci": ci, "hc": hc, "ms": "clean", "facts": True}


def test_a_queued_check_reads_queued_and_never_running(hub, state):
    """A check no runner has picked up carries `queued`: the chip says that word, not a run in progress."""
    now = time.time()
    ps = hub.pr_state(_pr(["queued", "queued"], hc=_iso(now - 60)))
    assert ps[1] == "CI queued"
    assert "running" not in ps[1]
    assert ps[0] == "running", "a queued check is still in flight: the chip waits, it does not fail"
    assert SHA9 in ps[2] and "queued" in ps[2]


def test_a_check_that_is_in_progress_still_reads_running(hub, state):
    """The positive control: a runner did pick a check up, so the run is in progress and the chip says so."""
    now = time.time()
    ps = hub.pr_state(_pr(["in_progress", "queued"], hc=_iso(now - 60)))
    assert ps[1] == "CI running"


def test_a_head_queued_past_the_jobs_own_timeout_says_it_cannot_run(hub, state):
    """The head has sat queued longer than the job's own timeout: no verdict can come, so the chip is a stop, not a wait."""
    now = time.time()
    ps = hub.pr_state(_pr(["queued", "queued"], hc=_iso(now - (JOB_TIMEOUT_MIN + 5) * 60)))
    assert ps[1] == "CI cannot run"
    assert ps[0] == "broke", "a queue that never moved is red, never blue: blue reads as progress"
    assert f"{JOB_TIMEOUT_MIN} min" in ps[2] and SHA9 in ps[2]


def test_a_queue_inside_the_timeout_is_not_called_a_stop(hub, state):
    """The other side of the threshold: a head pushed two minutes ago is young, and a young queue is only a queue."""
    now = time.time()
    ps = hub.pr_state(_pr(["queued"], hc=_iso(now - 120)))
    assert ps[1] == "CI queued" and ps[0] == "running"


def test_a_row_without_the_head_date_never_reads_as_a_stop(hub, state):
    """A fact the query did not return is unknown, never a stop (#1772): with no head date the chip reads queued."""
    ps = hub.pr_state(_pr(["queued"], hc=None))
    assert ps[1] == "CI queued" and ps[0] == "running"


def test_the_row_carries_the_head_commits_date_and_the_query_asks_for_it(hub):
    """The chip's evidence is GitHub's own committedDate of the head commit, mapped on the row by the one query."""
    assert "committedDate" in hub.OPEN_PR_QUERY
    at = _iso(1750000000)
    rows = hub.prs_from_graphql({"data": {"repository": {"pullRequests": {"nodes": [{
        "number": 1491, "title": "a PR", "isDraft": False, "updatedAt": "2026-10-11T09:00:00Z",
        "createdAt": "2026-10-11T08:00:00Z", "headRefOid": SHA, "baseRefName": "main", "mergeStateStatus": "CLEAN",
        "labels": {"nodes": []}, "reviewThreads": {"totalCount": 0, "nodes": []},
        "commits": {"nodes": [{"commit": {"committedDate": at, "statusCheckRollup": {
            "state": "PENDING", "contexts": {"nodes": [{"__typename": "CheckRun", "status": "QUEUED", "conclusion": None}]}}}}]}}]}}}})
    assert rows[0]["hc"] == at and rows[0]["ci"] == ["queued"]
