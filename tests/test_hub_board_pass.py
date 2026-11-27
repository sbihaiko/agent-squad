"""Hub board: one pass re-reads the board every FULL_EVERY seconds on its own; the TASKS header has no refresh button.

A failed pass keeps the last good rows. No route runs a pass on request, so a click never adds GitHub reads.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
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
    # Pinned, not `setdefault`: in one pytest process another module may already have named another repository, and the
    # fast read keeps only this repository's rows, so an inherited SG_REPO would empty the snapshot (#985).
    pinned = {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4",
              "SG_CHECKOUT": tempfile.mkdtemp(), "SG_HUB_DIR": tempfile.mkdtemp()}
    previous = {k: os.environ.get(k) for k in pinned}
    os.environ.update(pinned)
    spec = importlib.util.spec_from_file_location("hub_board_refresh_button", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


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
            out = json.dumps({"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "l": []}) + "\n" + json.dumps({"_page": 1}) + "\n"
        elif "pulls?state=closed" in s:
            out = ""
        elif "search/issues" in s:
            out = "0\n"
        elif "issues?state=open" in s:
            out = ""
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}})
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


def _node(n, status="Todo"):
    """One open item of the fast read's snapshot for the repository this hub reads."""
    return {"updatedAt": "2026-11-01T10:00:00Z", "isArchived": False,
            "fieldValueByName": {"name": status, "updatedAt": "2026-11-01T10:00:00Z"},
            "content": {"id": f"I_{n}", "number": n, "fullDatabaseId": str(900000 + n), "state": "OPEN", "stateReason": None,
                        "updatedAt": "2026-11-01T10:00:00Z", "repository": {"nameWithOwner": "x/y"},
                        "labels": {"totalCount": 0, "nodes": []}}}


def _snapshot(*nodes):
    """One whole fast-read answer for the user root: a page no further page follows."""
    return {"data": {"rateLimit": {"cost": 1},
                     "user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(nodes)}}}}}


def _row(n):
    """One open card as the full read stores it, joined to the snapshot by its database id."""
    return {"n": n, "t": f"card {n}", "s": "open", "st": "Todo", "l": [], "i": f"ITEM{n}", "fd": 900000 + n, "ot": 1}


def _fast_pass_ready(hub, monkeypatch, state, *answers):
    """The fast read wired to recorded answers: its own state directory, a fresh hour and the cache of one held card."""
    queue = list(answers)

    def gql(query, variables, timeout=None):
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(hub, "STATE", state)
    monkeypatch.setattr(hub, "_fast_root", lambda: "user")
    monkeypatch.setattr(hub, "FAST_BUDGET", hub.board_fast.Budget(600))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [_row(11)], "prs": [], "lc": {}, "err": None, "parts": {}})
    monkeypatch.setattr(hub, "_graphql", gql)


def test_moves_closer_than_the_minimum_gap_ask_for_one_full_read(hub, monkeypatch):
    """A burst of card moves asks for one full read, never one per move. The board holds more than a thousand cards, so a
    full read is a dozen pages; a loop that moves cards every few seconds turned the dirty flag into a full read every few
    seconds, and the hub spent most of its CPU parsing a board that had changed in one card (#1790). The fast read already
    carries each card's column within FAST_EVERY, so the full read is allowed to wait out the gap."""
    state = Path(tempfile.mkdtemp())
    _fast_pass_ready(hub, monkeypatch, state, _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    assert (state / "board-dirty").exists(), "the move asks for a full read"
    hub.board_cache["t"] = time.time() - 5   # the full read ran five seconds ago: inside the minimum gap

    passes, sleeps = [], []

    def fake_pass():
        passes.append(1)

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) > 2:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", fake_pass)
    monkeypatch.setattr(hub, "read_pause", fake_sleep)
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 1, "a move inside the minimum gap waits for the gap instead of re-reading the whole board"
    assert sleeps == [2, 2, 2], "the flag is polled every beat and the read stays due"


def test_a_card_the_cache_does_not_hold_asks_for_the_full_read(hub, monkeypatch):
    """A card added to the project is in the fast snapshot and not in the cache: the pass touches the dirty flag, and the
    full read leaves its wait after one 2 s beat, never the whole period (#985)."""
    state = Path(tempfile.mkdtemp())
    _fast_pass_ready(hub, monkeypatch, state, _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    assert (state / "board-dirty").exists(), "a card the fast read saw and the cache does not hold asks for a full read"

    passes, sleeps = [], []

    def fake_pass():
        passes.append(1)

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) > 1:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", fake_pass)
    monkeypatch.setattr(hub, "read_pause", fake_sleep)
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 2, "the full read runs again at the flag, not at its period"
    assert sleeps == [2, 2], "the flag is read after one 2 s beat, so the new card never waits the full period"


def test_the_same_unread_card_asks_for_one_full_read_only(hub, monkeypatch):
    """The anti-storm rule: a card the cache still lacks is asked for once per database id, so a fast pass every 15 s
    never becomes a full read every 15 s."""
    state = Path(tempfile.mkdtemp())
    _fast_pass_ready(hub, monkeypatch, state, _snapshot(_node(11), _node(12)), _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    flag = state / "board-dirty"
    assert flag.exists(), "the first pass asks for the card"
    os.utime(flag, (1, 1))   # a stamp a second touch would move
    hub.fast_board_pass()
    assert flag.stat().st_mtime == 1, "a card already asked for is never asked for again"
    assert 900012 in {int(x) for x in hub.board_cache.get("fast_asked") or []}, "the cache remembers the card it asked for"


def test_an_ask_a_full_read_outran_is_asked_again(hub, monkeypatch):
    """A full read already in flight stamps its own end after the touch the fast pass just made, so that first ask
    bought nothing. The same card under the next read is asked again, once: an ask the read outran must not leave the
    card waiting the whole period (#985)."""
    state = Path(tempfile.mkdtemp())
    _fast_pass_ready(hub, monkeypatch, state, _snapshot(_node(11), _node(12)), _snapshot(_node(11), _node(12)))
    hub.fast_board_pass()
    flag = state / "board-dirty"
    os.utime(flag, (1, 1))
    hub.board_cache["t"] = 2.0   # a full read ended after the ask and still holds no card 12
    hub.fast_board_pass()
    assert flag.stat().st_mtime != 1, "a card the read outran is asked for again under the new read"
    os.utime(flag, (1, 1))
    hub.fast_board_pass()
    assert flag.stat().st_mtime == 1, "that second ask is spent too: one ask per (database id, full read)"


def test_another_repositorys_cache_keeps_no_ask_and_no_pending_card(hub, monkeypatch, tmp_path):
    """The cache file is one per owner and project, so two repositories share it. Its rows are dropped when the hub
    reads another repository's cache; the ask and the pending card go with them, or the page names a count from a board
    this hub never read (#985, #1015)."""
    cache = {"repo": "another/repo", "t": 5.0, "items": [_row(11)], "prs": [], "lc": {}, "err": None,
             "fast_asked": {"900012": 0.0}, "fast_pending": [900012]}
    fresh = _load_hub_with_cache("fresh", cache, tmp_path, monkeypatch)
    assert fresh.board_cache.get("fast_asked") == {}, "another repository's ask is no ask of this board"
    assert fresh.board_cache.get("fast_pending") == [], "another repository's card is no card of this board"
    assert fresh.board()["pending_new"] == 0, "the served count names no card from another repository's cache"


def test_a_failed_snapshot_asks_for_no_full_read(hub, monkeypatch):
    """A read that discarded its whole snapshot has seen no card, so it asks for no full read either."""
    state = Path(tempfile.mkdtemp())
    _fast_pass_ready(hub, monkeypatch, state, RuntimeError("HTTP 502"))
    hub.fast_board_pass()
    assert not (state / "board-dirty").exists(), "a failed read has seen no card"
    assert hub.board_cache["parts"]["status"]["err"], "the failure is still named on the status part"


def _profile_hub(tag, base, monkeypatch):
    """One profile's hub in this process, as hubmux loads it: its own state and board cache around the shared board_fast."""
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir(parents=True)
    for key, value in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"),
                       ("SG_CHECKOUT", str(base / "checkout")), ("SG_HUB_DIR", str(base / "hub")),
                       ("SG_STATE", str(base / "state")), ("SG_BOARD_CACHE", str(base / "hub-board.json"))):
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(f"hub_profile_{tag}", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_two_profiles_in_one_process_each_ask_for_the_same_card(tmp_path, monkeypatch):
    """hubmux loads one hub per profile into one process: the memory of what was asked is the cache's, never a module's,
    or the first profile's fast read would silence the second profile's new card (#985)."""
    a, b = _profile_hub("a", tmp_path / "a", monkeypatch), _profile_hub("b", tmp_path / "b", monkeypatch)
    for mod in (a, b):
        _fast_pass_ready(mod, monkeypatch, mod.STATE, _snapshot(_node(11), _node(12)))
        mod.fast_board_pass()
    assert (a.STATE / "board-dirty").exists() and (b.STATE / "board-dirty").exists(), \
        "each profile's own cache names the card it has not read"


def _load_hub_with_cache(tag, cache, tmp_path, monkeypatch):
    """A fresh hub process reading one board cache file: the module's load is what a restart does."""
    for name in ("checkout", "hub", "state"):
        (tmp_path / name).mkdir(exist_ok=True)
    for key, value in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"),
                       ("SG_CHECKOUT", str(tmp_path / "checkout")), ("SG_HUB_DIR", str(tmp_path / "hub")),
                       ("SG_STATE", str(tmp_path / "state")), ("SG_BOARD_CACHE", str(cache))):
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(f"hub_cache_{tag}", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_board_cache_written_before_the_close_reason_is_read_again(tmp_path, monkeypatch):
    """#921 (DEBT 2): a cache written before the rows carried the close reason answers no `cr`, so a card closed as not
    planned would draw Closed and count as delivered. The marker makes such a cache read again; a cache this read wrote
    keeps its rows across a restart."""
    cache = tmp_path / "hub-board.json"
    row = {"n": 7, "t": "a closed card", "s": "closed", "c": "2026-10-02T00:00:00Z", "st": "Closed", "l": []}
    cache.write_text(json.dumps({"t": 99.0, "items": [row], "prs": [], "lc": {}, "err": None}))

    stale = _load_hub_with_cache("stale", cache, tmp_path, monkeypatch)

    assert stale.board_cache["items"] == [] and stale.board_cache["t"] == 0, \
        "a cache with no close reason on its rows is read again, never drawn as delivered"
    assert stale.board_cache["rv"] == stale.BOARD_READ

    stale.board_cache["items"] = [{**row, "cr": "not_planned"}]
    stale.save_board()
    fresh = _load_hub_with_cache("fresh", cache, tmp_path, monkeypatch)

    assert [i["n"] for i in fresh.board_cache["items"]] == [7], "a cache this read wrote keeps its rows across a restart"


def test_the_tasks_header_has_no_refresh_button(hub):
    """The board re-reads itself on its full-read period and within 2 s of a board move; a hand refresh only adds GitHub reads."""
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


def test_the_board_passes_on_its_own_every_full_read_period(hub, monkeypatch):
    """The automatic full read runs on its own, one every FULL_EVERY seconds (SPEC 968)."""
    passes, sleeps = [], []
    waits = hub.FULL_EVERY // 2

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == waits + 2:
            raise StopIteration            # one full wait plus two more: enough to see the second pass and its schedule

    monkeypatch.setattr(hub, "board_pass", lambda: passes.append(1))
    monkeypatch.setattr(hub, "read_pause", fake_sleep)
    monkeypatch.setattr(hub, "STATE", Path(tempfile.mkdtemp()))   # no board-dirty flag of a live loop on this machine
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 2, "the loop keeps passing on its own"
    assert sleeps[:waits] == [2] * waits, "one full read every FULL_EVERY seconds: waits of 2 s between passes"


def test_a_pass_that_raises_does_not_end_the_refresh_loop(hub, monkeypatch):
    """#512: one pass that raised ended the refresh thread, and the board stayed frozen until a restart."""
    passes, sleeps = [], []

    def bad_pass():
        passes.append(1)
        if len(passes) == 1:
            raise FileNotFoundError("the temp file is gone")

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == hub.FULL_EVERY // 2 + 1:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", bad_pass)
    monkeypatch.setattr(hub, "read_pause", fake_sleep)
    monkeypatch.setattr(hub, "STATE", Path(tempfile.mkdtemp()))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 2, "the next pass still runs after a pass raised"
    assert "temp file is gone" in (hub.board_cache["err"] or ""), "the error of the failed pass is recorded"


def test_a_full_read_past_its_deadline_ends_the_pass_and_keeps_the_rows(hub, monkeypatch):
    fetched = []
    old = [{"n": 5, "st": "Todo", "s": "open", "ot": 1}]
    monkeypatch.setattr(hub, "FULL_DEADLINE", 0)
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([]))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": old, "prs": [], "lc": {}, "err": None})
    hub.board_pass(fetch_open=lambda repo: fetched.append(repo) or [], repos=["x/y"])
    assert fetched == [], "a fetch the deadline has passed is never sent"
    assert hub.board_cache["items"] == old and "deadline" in (hub.board_cache.get("err") or "")


def test_the_full_read_saves_the_cache_under_the_board_lock(hub, monkeypatch):
    held = []
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([]))
    monkeypatch.setattr(hub, "save_board", lambda: held.append(hub._board_lock.locked()))
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    hub.board_pass(fetch_open=lambda repo: [], repos=["x/y"])
    assert held and all(held), "the cache is written only while the lock holds it, so a fast read never sees a half-written cache"


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
    # No two writes share a temp name. Both writers write the board cache; the flow history appends only when its counts
    # changed, so the second writer may find the first's sample and write nothing there (#835).
    assert srcs, "the pass wrote no file"
    assert len(set(srcs)) == len(srcs), f"two writers shared a temp file: {srcs}"
    board = [s for s in srcs if Path(s).name.startswith(hub.BOARD.name + ".")]
    assert len(board) == 2, f"both writers write the board cache: {srcs}"


def test_a_move_pass_is_read_again_after_the_board_settles(hub, monkeypatch):
    """The items route lags a write by seconds: the pass a board-dirty touch starts read the old Status and the card sat
    in its old column for the full 60 s. A touch is followed by one settle pass, well before the next scheduled one."""
    import time as real_time
    passes, sleeps = [], []
    state = Path(tempfile.mkdtemp())
    (state / "board-dirty").touch()

    def fake_pass():
        passes.append(1)
        if len(passes) > 1:   # the start pass leaves the touch unanswered
            hub.board_cache["t"] = real_time.time() + 1

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 15:
            raise StopIteration

    monkeypatch.setattr(hub, "board_pass", fake_pass)
    monkeypatch.setattr(hub, "read_pause", fake_sleep)
    monkeypatch.setattr(hub, "STATE", state)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    with pytest.raises(StopIteration):
        hub.refresh_board()
    assert len(passes) == 3, "the start pass, the pass the touch asked for, and the settle pass"


class _Done(BaseException):
    """Ends a refresh loop under test; a BaseException, so the loop's own `except Exception` never takes it."""


class _Clock:
    """Time that moves only when the refresh loop pauses: a retry gap is read off the clock, and no test sleeps. The pause
    patched is the loop's own seam, `read_pause`, never the process-global `time.sleep`: an unrelated thread of this
    process that sleeps -- a hub server another test of the same worker left running -- moved the clock and stretched a
    gap to 613.2 s (#2646)."""

    def __init__(self, monkeypatch, hub):
        self.now = 1_000_000.0
        monkeypatch.setattr(hub.time, "time", lambda: self.now)
        monkeypatch.setattr(hub, "read_pause", self.sleep)

    def sleep(self, secs):
        self.now += secs


def _refresh_with(hub, monkeypatch, tmp_path, results, clock):
    """refresh_board over fake passes: results[i] is pass i's answer, True for a good read and False for a failed one."""
    monkeypatch.setattr(hub, "STATE", tmp_path)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    starts = []

    def fake_pass():
        if len(starts) == len(results):
            raise _Done()
        starts.append(clock.now)
        return results[len(starts) - 1]

    monkeypatch.setattr(hub, "board_pass", fake_pass)
    with pytest.raises(_Done):
        hub.refresh_board()
    return [b - a for a, b in zip(starts, starts[1:], strict=False)]


def test_a_failed_full_read_is_retried_within_thirty_seconds(hub, monkeypatch, tmp_path):
    clock = _Clock(monkeypatch, hub)
    gaps = _refresh_with(hub, monkeypatch, tmp_path, [False, True], clock)
    assert gaps and gaps[0] <= 30, "one failed full read: the next read starts within 30 s, not one full period later"


def test_consecutive_failures_grow_the_wait_and_stop_at_the_full_period(hub, monkeypatch, tmp_path):
    clock = _Clock(monkeypatch, hub)
    gaps = _refresh_with(hub, monkeypatch, tmp_path, [False] * 9, clock)
    assert gaps == [16, 30, 60, 120, 240, 480, 600, 600], \
        "each failure doubles the 15 s first wait, rounded to the 2 s beat, and the wait never passes FULL_EVERY"


def test_a_sleep_outside_the_refresh_loop_never_moves_its_clock(hub, monkeypatch):
    """#2646: the gaps are counted on the loop's own pause. Another thread of this process -- a hub server an earlier test
    of the same worker left running -- calls the global `time.sleep`; that global is not the loop's seam, so with it
    patched a stray sleep stretched the measured gaps to 613.2 s and 600.2 s instead of 600 s."""
    clock = _Clock(monkeypatch, hub)
    before = clock.now
    hub.time.sleep(0.2)
    assert clock.now == before, "only the refresh loop's own pause moves its clock"
    hub.read_pause(2)
    assert clock.now == before + 2, "the loop's own pause still moves the clock"


def test_a_good_read_after_a_failure_restores_the_full_cadence_and_clears_err(hub, monkeypatch, tmp_path):
    clock = _Clock(monkeypatch, hub)
    monkeypatch.setattr(hub, "STATE", tmp_path)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    gh = {"run": _fake_gh([], fail=True)}
    monkeypatch.setattr(hub.subprocess, "run", lambda cmd, **kw: gh["run"](cmd, **kw))
    starts, errs = [], []
    board_pass = hub.board_pass

    def real_pass():
        starts.append(clock.now)
        if len(starts) > 3:
            raise _Done()
        if len(starts) == 2:
            gh["run"] = _fake_gh([])   # GitHub answers again from the second read on
        result = board_pass()
        errs.append(hub.board_cache["err"])
        return result

    monkeypatch.setattr(hub, "board_pass", real_pass)
    with pytest.raises(_Done):
        hub.refresh_board()
    assert errs[0], "the failed read names its error"
    assert starts[1] - starts[0] <= 30, "the failed read is retried within 30 s"
    assert errs[1] is None, "the good read clears err"
    assert starts[2] - starts[1] == hub.FULL_EVERY, "after a good read the cadence is the full period again"


def test_no_retry_starts_while_a_github_cooldown_holds(hub, monkeypatch, tmp_path):
    clock = _Clock(monkeypatch, hub)
    until = clock.now + 700   # longer than the full period, so the cooldown alone sets the wait
    (tmp_path / "gh-cooldown.json").write_text(json.dumps({"until": until, "at": clock.now, "pass": "landing",
                                                           "message": "API rate limit exceeded"}))
    gaps = _refresh_with(hub, monkeypatch, tmp_path, [False, True], clock)
    assert gaps and gaps[0] >= 700, "the retry starts only once the GitHub cooldown has ended"
