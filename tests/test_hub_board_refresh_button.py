"""Hub board: the header's refresh button forces a real board re-read, and the 60 s pass is untouched (#1810).

The button is a control, so the seam under test is behaviour: the pass it triggers, the route that serves it, the
state the page paints while it runs, and the failure path. Only the page's own busy/failure wiring is read as markup.
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


def test_pressing_the_button_forces_a_real_pass_over_the_project_the_blockers_and_the_prs(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh(calls))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "prev": [], "prev_n": 0, "lc": {}, "err": None})
    hub.board_pass()
    ep = " | ".join(_endpoints(calls))
    assert f"/users/{hub.OWNER}/projectsV2/{hub.PROJECT_NO}/items" in ep      # the Project statuses
    assert "blockedBy" in ep                                                 # the blocked-by page
    assert "pullRequests(first:100" in ep                                    # the open PRs
    assert "labels?" in ep, "a real pass re-reads the labels too (the label filter); the PR columns read after it (#1820)"
    assert hub.board_cache["t"] > 1.0                                        # fresh data, not another read of the cache
    assert [i["n"] for i in hub.board_cache["items"]] == [11]
    assert [p["n"] for p in hub.board_cache["prs"]] == [7]


def test_the_refresh_route_runs_the_pass_and_is_not_the_cached_board_route(hub):
    """The route the button calls re-reads; the plain /api/board only re-serves what the last pass left."""
    src = HUB_PY.read_text()
    served = re.search(r'elif self\.path\.startswith\("/api/board/refresh"\):(.*?)\n\s+elif', src, re.S)
    assert served, "no /api/board/refresh route: the button would re-read the cache and change nothing"
    assert "board_pass()" in served.group(1), f"the route must run one pass: {served.group(1)!r}"
    routes = re.search(r"def do_GET.*?(?=\n    def |\nclass )", src, re.S).group(0)
    assert routes.index("/api/board/refresh") < routes.index('elif self.path.startswith("/api/board")'), \
        "the refresh route is matched before the plain one, which would swallow it"


def test_the_button_sits_beside_the_title_in_the_header(hub):
    head = re.search(r"<div id=bhead>.*?</div>\n", hub.PAGE, re.S).group(0)
    m = re.search(r"<b>BUILDER <span id=bt class=m></span></b>(<button id=br\b[^>]*>)", head)
    assert m, f"#br sits inline right after the title text: {head!r}"
    assert head.index("id=br") < head.index("id=lf"), "it belongs to the title, not to the labels/maximize/collapse group"


def test_the_button_carries_a_title_and_an_accessible_name(hub):
    m = re.search(r"<button id=br\b[^>]*>(.*?)</button>", hub.PAGE, re.S)
    assert m, "no #br element in the served page"
    assert re.search(r'title="[^"]+"', m.group(0)), f"no title: {m.group(0)!r}"
    assert re.search(r'aria-label="[^"]+"', m.group(0)), f"the icon is not the only label: {m.group(0)!r}"
    assert m.group(1).strip(), "the button paints an icon, not an empty box"


def test_the_button_says_it_is_busy_while_the_pass_runs_and_clears_when_it_ends(hub):
    h = re.search(r'\$\("#br"\)\.onclick=.*?;\n', hub.PAGE, re.S)
    assert h, "no click handler on #br"
    js = h.group(0)
    assert 'fetch("/api/board/refresh")' in js, "the press asks for a pass, never a reload of the page"
    assert "b.disabled=true" in js and 'b.setAttribute("aria-busy","true")' in js, f"the press shows it is busy: {js!r}"
    assert "if(b.disabled)return" in js, "a second press cannot stack another pass"
    assert re.search(r"finally\{.*b\.disabled=false.*\}", js), f"a finished or failed pass clears the busy state: {js!r}"
    assert 'b.setAttribute("aria-busy","false")' in js
    assert "pollBoard()" in js, "the fresh data is on screen when the pass ends"


def test_a_failed_pass_keeps_the_last_good_board_and_surfaces_the_error(hub, monkeypatch):
    good = {"t": 99.0, "items": [{"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "l": []}], "prs": [], "prev": [], "prev_n": 0, "lc": {}, "err": None}
    monkeypatch.setattr(hub, "board_cache", dict(good))
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([], fail=True))
    hub.board_pass()
    assert hub.board_cache["err"], "a failed pass surfaces the failure"
    assert hub.board_cache["t"] == 99.0 and [i["n"] for i in hub.board_cache["items"]] == [11], \
        "the last good rows stay on the board: never a blank board"
    b = hub.board()
    assert b["err"] == hub.board_cache["err"] and [g["n"] for g in b["groups"] if g["name"] == "Todo"] == [1], \
        "the served board returns the last good data with the error beside it, which the page already renders"


def test_the_sixty_second_pass_is_unchanged_and_is_not_replaced_by_the_button(hub, monkeypatch):
    """The button adds a way to ask; the automatic pass keeps running on its own, one every 60 s."""
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
    assert len(passes) == 2, "the loop keeps passing on its own, not only when the button is pressed"
    assert sleeps[:30] == [2] * 30, "one pass every 60 s: 30 waits of 2 s between passes"
