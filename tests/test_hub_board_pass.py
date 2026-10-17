"""Hub board: one pass re-reads the board every 60 s on its own; the TASKS header has no refresh button.

A failed pass keeps the last good rows. No route runs a pass on request, so a click never adds GitHub reads.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

PR_NODE = {"number": 7, "title": "a PR", "isDraft": False, "updatedAt": "2026-10-11T10:00:00Z", "createdAt": "2026-10-11T09:00:00Z",
           "headRefOid": "abcdef1234", "baseRefName": "main", "mergeStateStatus": "CLEAN",
           "labels": {"nodes": []}, "reviewThreads": {"totalCount": 0, "nodes": []},
           "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]}}
PRS_PAYLOAD = json.dumps({"data": {"repository": {"pullRequests": {"nodes": [PR_NODE]}}}})


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_refresh_button", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_gh(calls, fail=False):
    """The gh CLI as the pass uses it: each call is recorded, and the answer is canned per endpoint."""
    def run(cmd, **kw):
        calls.append(cmd)
        if fail:
            raise FileNotFoundError("gh is not on PATH")
        s = " ".join(cmd)
        if "/fields?" in s:
            out = "PVTSSF_status\n"                                # status_field()
        elif "projectsV2" in s and "/items" in s:
            out = json.dumps({"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "l": []}) + "\n"
        elif "pulls?state=closed" in s:
            out = ""
        elif "search/issues" in s:
            out = "0\n"
        elif "issues?state=open" in s:
            out = ""
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})
        elif "pullRequests(first:100" in s:
            out = PRS_PAYLOAD                                      # the open-PR query
        elif "/labels?" in s:
            out = ""
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _endpoints(calls):
    """The gh endpoints a pass touched, in order, as one string per call."""
    return [" ".join(c[3:] if c[:3] == ["gh", "api", "--paginate"] else c[2:]) for c in calls]


def test_a_pass_reads_the_project_the_blockers_and_the_prs(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh(calls))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    hub.board_pass()
    ep = " | ".join(_endpoints(calls))
    assert f"/users/{hub.OWNER}/projectsV2/{hub.PROJECT_NO}/items" in ep      # the Project statuses
    assert "blockedBy" in ep                                                 # the blocked-by page
    assert "branches?per_page" in ep                                         # the open work branches (#358: no pull request lane)
    assert "labels?" in ep, "a real pass re-reads the labels too (the label filter); the PR columns read after it (#1820)"
    assert hub.board_cache["t"] > 1.0                                        # fresh data, not another read of the cache
    assert [i["n"] for i in hub.board_cache["items"]] == [11]
    assert isinstance(hub.board_cache["prs"], list)


def test_the_tasks_header_has_no_refresh_button(hub):
    """The board re-reads itself every 60 s and within 2 s of a board move; a hand refresh only adds GitHub reads."""
    head = re.search(r"<div id=bhead>.*?</div>\n", hub.PAGE, re.S).group(0)
    assert "id=br" not in head, f"the TASKS header carries no refresh button: {head!r}"
    assert '$("#br")' not in hub.PAGE, "no click handler is left for a button that is gone"


def test_no_route_runs_a_board_pass_on_request(hub):
    """Only the timer runs a pass: no GET route spends the GitHub reads of a pass on a click."""
    routes = re.search(r"def do_GET.*?(?=\n    def |\nclass )", HUB_PY.read_text(), re.S).group(0)
    assert "/api/board/refresh" not in routes and "board_pass()" not in routes, "a GET route still runs a board pass"


def test_a_failed_pass_keeps_the_last_good_board_and_surfaces_the_error(hub, monkeypatch):
    good = {"t": 99.0, "items": [{"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "l": []}], "prs": [], "lc": {}, "err": None}
    monkeypatch.setattr(hub, "board_cache", dict(good))
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([], fail=True))
    hub.board_pass()
    assert hub.board_cache["err"], "a failed pass surfaces the failure"
    assert hub.board_cache["t"] == 99.0 and [i["n"] for i in hub.board_cache["items"]] == [11], \
        "the last good rows stay on the board: never a blank board"
    b = hub.board()
    assert b["err"] == hub.board_cache["err"] and [g["n"] for g in b["groups"] if g["name"] == "Todo"] == [1], \
        "the served board returns the last good data with the error beside it, which the page already renders"


def test_the_board_passes_on_its_own_every_sixty_seconds(hub, monkeypatch):
    """The automatic pass runs on its own, one every 60 s."""
    passes, sleeps = [], []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 32:
            raise StopIteration            # one full wait plus two more: enough to see the second pass and its schedule

    monkeypatch.setattr(hub, "board_pass", lambda: passes.append(1))
    monkeypatch.setattr(hub.time, "sleep", fake_sleep)
    monkeypatch.setattr(hub, "STATE", Path(tempfile.mkdtemp()))   # no board-dirty flag of a live loop on this machine
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 2, "the loop keeps passing on its own"
    assert sleeps[:30] == [2] * 30, "one pass every 60 s: 30 waits of 2 s between passes"


def test_a_pass_that_raises_does_not_end_the_refresh_loop(hub, monkeypatch):
    """#512: one pass that raised ended the refresh thread, and the board stayed frozen until a restart."""
    passes, sleeps = [], []

    def bad_pass():
        passes.append(1)
        if len(passes) == 1:
            raise FileNotFoundError("the temp file is gone")

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 31:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", bad_pass)
    monkeypatch.setattr(hub.time, "sleep", fake_sleep)
    monkeypatch.setattr(hub, "STATE", Path(tempfile.mkdtemp()))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 2, "the next pass still runs after a pass raised"
    assert "temp file is gone" in (hub.board_cache["err"] or ""), "the error of the failed pass is recorded"


def test_two_writers_in_one_process_never_share_a_temp_file(hub, monkeypatch):
    """#512: hubmux runs one hub per profile in one process; a temp name keyed by the pid alone was shared, and one
    writer's os.replace took the file away from the other."""
    import threading
    srcs = []
    real = os.replace
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([]))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub.os, "replace", lambda a, b: (srcs.append(str(a)), real(a, b)))
    ts = [threading.Thread(target=hub.board_pass) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(srcs) == 2 and srcs[0] != srcs[1], srcs


def test_a_move_pass_is_read_again_after_the_board_settles(hub, monkeypatch):
    """The items route lags a write by seconds: the pass a board-dirty touch starts read the old Status and the card sat
    in its old column for the full 60 s. A touch is followed by one settle pass, well before the next scheduled one."""
    import time as real_time
    passes, sleeps = [], []
    state = Path(tempfile.mkdtemp())
    (state / "board-dirty").touch()

    def fake_pass():
        passes.append(1)
        if len(passes) > 1: hub.board_cache["t"] = real_time.time() + 1   # the start pass leaves the touch unanswered

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 15:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", fake_pass)
    monkeypatch.setattr(hub.time, "sleep", fake_sleep)
    monkeypatch.setattr(hub, "STATE", state)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 3, "the start pass, the pass the touch asked for, and the settle pass"
