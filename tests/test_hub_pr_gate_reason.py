"""Hub OPEN PRS card: the chip reports the loop's own reason, from `auto/gate-<pr>` (#1771).

A card said `ready to merge` while the loop was holding the merge: the hub derived readiness from the review line and never
asked the loop. `auto/gate-<pr>` is the loop's own record, rewritten at every exit of a pass (#1766), and it is the loop's
word only for the head it names, from the pass `auto/.pass` stamps. On any other head the chip says one of the three honest
states instead: `loop off`, `loop idle`, `new head, not seen yet`.
"""
import importlib.util
import os
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SHA = "1e38e5a7b41c0d2e9f"       # the head the PR has right now
SHA9 = SHA[:9]
OLD = "9c0ffee12deadbeef"        # the head before it

HOLD = ("hold-threads", "unresolved review threads opened after the cache")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_pr_gate_reason", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """A squad state dir holding the loop's own files (auto/.pass, auto/gate-<pr>, auto/off)."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    monkeypatch.setattr(hub, "STATE", st)
    return st


def _pass(st, at):
    """auto/.pass: the epoch of the loop's last pass, stamped at the top of every pass (autopilot.sh)."""
    (st / "auto/.pass").write_text(f"{int(at)}\n")


def _gate(st, pr, sha9, at, reason, text):
    """auto/gate-<pr>: `<head sha9><TAB><epoch><TAB><reason><TAB><what it means>`, the pass's latest decision."""
    (st / "auto" / f"gate-{pr}").write_text(f"{sha9}\t{int(at)}\t{reason}\t{text}\n")


def _approve(st, pr, sha9):
    """The review line the hub reads for a head: an APPROVE is what used to make the card claim `ready to merge`."""
    (st / f"revci-{pr}.out").write_text(f"#{pr} {sha9} clean CI=success:2 VERDICT: APPROVE\n")


def _pr(n=1801, head=SHA, **kw):
    x = {"n": n, "t": "Hub: the card reports the loop's own reason", "u": "2026-10-11T09:00:00Z", "h": head,
         "d": False, "ci": [], "ms": "clean", "o": "2026-10-11T08:00:00Z"}
    x.update(kw)
    return x


def test_a_hold_the_loop_reported_is_the_chip_not_ready_to_merge(hub, state):
    now = time.time()
    _pass(state, now - 30)
    _gate(state, 1801, SHA9, now - 20, *HOLD)
    _approve(state, 1801, SHA9)
    ps = hub.pr_state(_pr())
    assert ps[1] == "held: threads"
    assert "ready to merge" not in ps[1]
    assert ps[0] == "changes"
    assert HOLD[1] in ps[2] and "20 s ago" in ps[2]   # the tooltip carries the gate line and its age


def test_an_approve_the_loop_never_judged_is_not_ready_to_merge(hub, state):
    """The reported symptom: a green review line with no gate line of its own is not the loop's word."""
    now = time.time()
    _pass(state, now - 30)
    _approve(state, 1801, SHA9)
    ps = hub.pr_state(_pr())
    assert ps[1] == "new head, not seen yet"
    assert ps[0] == "idle"


def test_a_gate_line_for_the_head_the_pr_no_longer_has_does_not_stand(hub, state):
    now = time.time()
    _pass(state, now - 30)
    _gate(state, 1801, OLD[:9], now - 20, *HOLD)   # written about the head before this one
    _approve(state, 1801, SHA9)
    ps = hub.pr_state(_pr())
    assert ps[1] == "new head, not seen yet"
    assert HOLD[0] not in ps[2] and HOLD[1] not in ps[2]   # the word about the old head is not carried as the verdict


def test_a_gate_line_from_an_earlier_pass_than_the_one_on_disk_does_not_stand(hub, state):
    now = time.time()
    _pass(state, now - 60)
    _gate(state, 1801, SHA9, now - 300, *HOLD)   # same head, but the pass that wrote it has ended
    _approve(state, 1801, SHA9)
    assert hub.pr_state(_pr())[1] == "new head, not seen yet"


def test_a_loop_that_stopped_passing_says_so_instead_of_its_last_reason(hub, state):
    now = time.time()
    _pass(state, now - 600)                      # idle past three watch cycles
    _gate(state, 1801, SHA9, now - 600, *HOLD)
    _approve(state, 1801, SHA9)
    ps = hub.pr_state(_pr())
    assert ps[1] == "loop idle"
    assert ps[0] == "idle"
    assert "10 min" in ps[2]


def test_the_gate_saying_the_merge_goes_ahead_is_what_ready_to_merge_is_for(hub, state):
    now = time.time()
    _pass(state, now - 30)
    _gate(state, 1801, SHA9, now - 20, "merged", "merged as abc1234: APPROVE + CI green + clean + no open threads")
    _approve(state, 1801, SHA9)
    ps = hub.pr_state(_pr())
    assert ps[1] == "ready to merge"
    assert ps[0] == "ready"


def test_the_kill_switch_takes_every_open_card_not_only_the_green_one(hub, state):
    now = time.time()
    (state / "auto/off").write_text("")
    _pass(state, now - 30)
    _gate(state, 1801, SHA9, now - 20, "merged", "merged as abc1234: APPROVE + CI green + clean + no open threads")
    _approve(state, 1801, SHA9)
    cards = [_pr(1801), _pr(1802, d=True), _pr(1803, ms="dirty"), _pr(1804, ci=["failure"]), _pr(1805)]
    (state / "revci-1805.out").write_text(f"#1805 {SHA9} clean CI=success:2 VERDICT: CHANGES\n")
    for x in cards:
        assert hub.pr_state(x)[1] == "loop off", x
        assert hub.pr_state(x)[0] == "idle", x


def test_the_hub_writes_nothing_under_auto(hub, state):
    """The loop owns auto/; the hub only reads it, so a card can never be the thing the loop reads back."""
    now = time.time()
    _pass(state, now - 30)
    _gate(state, 1801, SHA9, now - 20, *HOLD)
    _approve(state, 1801, SHA9)
    before = {p.name: p.stat().st_mtime for p in (state / "auto").iterdir()}
    for x in (_pr(), _pr(1802, d=True), _pr(1803, ms="dirty"), _pr(1804, ci=["failure"])):
        hub.pr_state(x)
    state.joinpath("auto/off").write_text("")     # the kill switch is the owner's file, not the hub's
    for x in (_pr(), _pr(1802, d=True)):
        hub.pr_state(x)
    after = {p.name: p.stat().st_mtime for p in (state / "auto").iterdir()}
    assert set(after) - {"off"} == set(before)
    assert all(after[k] == before[k] for k in before)
