"""The Monitor's Live work tile and the holder chip (loop-bug #1664).

The tile is named "Live work" and lists what is being worked on now: a Squad run, a HAL9k hold, and a Manual hold
proven by folder activity (the hold marker's own write time, under the one bound). An orphan hold on a closed or
abandoned issue is never drawn. The card chip names the holder, and a takeover reads "Squad · taken over <time>".
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    state = tempfile.mkdtemp()
    checkout = tempfile.mkdtemp()
    os.environ.setdefault("SG_REPO", "sbihaiko/agent-squad")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "5")
    os.environ["SG_STATE"] = state
    os.environ["SG_CHECKOUT"] = checkout
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_live_work", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def clean(hub):
    """Each case starts with no hold and an empty board cache, so one case's hold never leaks into the next."""
    d = Path(hub.STATE) / "auto"
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("hold-*"):
        p.unlink()
    (d / "hold-alerts").unlink(missing_ok=True)
    (Path(hub.STATE) / "alerts.log").unlink(missing_ok=True)
    (Path(hub.STATE) / "watchd.pid").unlink(missing_ok=True)   # one case's open loop never leaks into the next
    hub.board_cache = {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None}
    yield


def _hold(hub, n, by="manual", minutes=60, now=None):
    from squad import handhold
    rc = handhold.hold([n], by=by, minutes=minutes, state=hub.STATE, now=now or time.time(), procs={},
                       label_write=lambda repo, issue, label, add: None)   # no case writes a label
    assert rc == 0


def _closed(hub, *ns):
    hub.board_cache = {"t": 1.0, "items": [{"n": n, "s": "closed"} for n in ns], "prs": [], "lc": {}, "err": None}


def test_the_tile_is_named_live_work(hub):
    assert "<summary>Live work" in hub.PAGE
    assert "no live work" in hub.PAGE
    assert "<summary>Live runs" not in hub.PAGE


def test_a_fresh_manual_hold_is_live_work(hub):
    _closed(hub)
    _hold(hub, 1664)
    rows = hub.hold_rows()
    assert [r["label"] for r in rows] == ["Manual · #1664"]
    assert rows[0]["kind"] == "hold" and rows[0]["rank"] == 0 and rows[0]["carrier"] == "manual"


def test_a_hal9k_hold_names_its_carrier(hub):
    _closed(hub)
    _hold(hub, 1665, by="hal9k")
    assert [r["label"] for r in hub.hold_rows()] == ["HAL9k · #1665"]


def test_a_stale_hold_is_not_live_work(hub):
    _closed(hub)
    _hold(hub, 1666, minutes=1, now=time.time() - 3600)
    assert hub.hold_rows() == []
    assert "HOLD STALE #1666" in (Path(hub.STATE) / "alerts.log").read_text()


def test_an_orphan_hold_is_never_live_work(hub):
    _hold(hub, 1667)
    _closed(hub, 1667)
    assert hub.hold_rows() == []
    assert "HOLD ORPHAN #1667" in (Path(hub.STATE) / "alerts.log").read_text()


def test_the_chip_names_the_holder(hub):
    assert hub.holder_chip({"n": 1, "h": {"run": "run-x", "started": "2026-11-13T08:00:00Z"}}) == "Squad"


def test_the_takeover_chip_names_the_squad_and_the_time(hub):
    i = {"n": 1, "h": {"run": "run-x", "started": "2026-11-13T08:00:00Z"}}
    chip = hub.holder_chip(i, taken={"1": 1_800_000_000.0})
    assert chip.startswith("Squad · taken over ")


def test_the_card_paints_the_holder_name_the_row_carries(hub):
    if NODE is None:
        pytest.skip("node is required")
    line = re.search(r"^const cards=.*$", hub.PAGE, re.M).group(0)
    lanes = (ROOT / "contrib/squad-goal/scripts/board-lanes.js").read_text()
    script = (lanes + "const SLOTS=specSlots([]);\n"
              'const esc=s=>String(s);const isPR=n=>false;const colOf=()=>"#000000";const tint=()=>"";'
              'const lbc=()=>"";const gstrip=()=>"";const age=()=>"";const ageC=()=>"";const bo={labels:[]};const CN=["Todo"];\n'
              + line + '\nconst I=[{n:241,t:"x",l:[],ho:"held by run-x since then",hc:"Squad"}];'
              'console.log(cards({name:"Todo"},I,I));\n')
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert '<span class=hld title="held by run-x since then">Squad</span>' in out.stdout


def _answer(hub, items=(), **kw):
    """The panel's structured answer: the loop's state, its tagged lines and the board read's age."""
    return hub.live_work_lines(list(items), state=hub.STATE, checkout=hub.CHECKOUT, procs={}, now=time.time(), **kw)


def _lines(hub, items=(), **kw):
    """The panel's text lines, in the order the hub answers them."""
    return [r["text"] for r in _answer(hub, items, **kw)["lines"]]


def _row(ans, kind):
    """The one row of a kind the answer carries."""
    rows = [r for r in ans["lines"] if r["kind"] == kind]
    assert len(rows) == 1, ans["lines"]
    return rows[0]


def test_a_stopped_loop_reads_loop_off_never_no_live_work(hub):
    lines = _lines(hub)
    assert "loop off" in lines
    assert not any("no live work" in x for x in lines)


def _held(hub, name, cmd=None):
    lock = Path(hub.STATE) / "locks" / name
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "pid").write_text(f"{os.getpid()}\n")
    if cmd is not None:
        (lock / "cmd").write_text(cmd + "\n")   # serial.sh writes the command name only: `bash`
    return lock


def test_a_live_gate_lock_names_the_run_it_checks_never_bash(hub):
    """#1695: the lock file names `bash`; the holder command line names the run workspace the gate checks. #1697: the row
    is tagged `gate` and carries the run's id, so the card places it by identity."""
    lock = _held(hub, "gate", "bash")
    line = "bash /r/worktrees/child/gui-1673/runs/run-20261113-204951-f6107e/ws/w/contrib/squad-goal/scripts/serial.sh gate bash x/gate.sh --inner bash contrib/squad-goal/scripts/fast-gate.sh"
    try:
        ans = _answer(hub, argv=lambda pid: line)
    finally:
        shutil.rmtree(lock)
    lines = [r["text"] for r in ans["lines"]]
    assert f"gate: pid {os.getpid()} checks run run-20261113-204951-f6107e" in lines, lines
    assert not any("serves" in x or x.endswith("bash") for x in lines), lines
    gate = _row(ans, "gate")
    assert gate["id"] == "gate-gate" and gate["run"] == "run-20261113-204951-f6107e", gate


def test_a_gate_lock_outside_a_run_names_its_gate_script(hub):
    lock = _held(hub, "gate", "bash")
    try:
        ans = _answer(hub, argv=lambda pid: "bash /repo/contrib/squad-goal/scripts/serial.sh gate bash /repo/contrib/squad-goal/scripts/gate.sh --inner bash contrib/squad-goal/scripts/fast-gate.sh")
    finally:
        shutil.rmtree(lock)
    gate = _row(ans, "gate")
    assert gate["text"] == f"gate: pid {os.getpid()} runs fast-gate.sh", gate
    assert gate["run"] is None, gate   # a gate outside a run names no run


def test_a_gate_lock_whose_command_line_is_unread_names_its_pid_alone(hub):
    lock = _held(hub, "gate", "bash")
    try:
        lines = _lines(hub, argv=lambda pid: "")
    finally:
        shutil.rmtree(lock)
    assert f"gate: pid {os.getpid()}" in lines, lines


def test_a_ready_for_human_issue_is_never_live_work(hub):
    """#1701: a ready-for-human card waits on the owner; it is no live work, in Doing or elsewhere."""
    items = [{"n": 1681, "t": "Health rule", "s": "open", "st": "Todo", "l": ["ready-for-human", "bug"]},
             {"n": 1682, "t": "Held in Doing", "s": "open", "st": "Doing", "l": ["ready-for-human"]}]
    ans = _answer(hub, items)
    lines = [r["text"] for r in ans["lines"]]
    assert not any("#1681" in x or "#1682" in x or "ready-for-human" in x for x in lines), lines
    assert not any(r["kind"] == "ready" for r in ans["lines"]), ans["lines"]   # no row kind for a card that waits on the owner


def test_a_doing_card_with_no_live_run_is_hand_work(hub):
    """#1701: the panel agrees with the Doing column: a card worked by hand is a line, a closed card is not. #1697: the
    row is tagged `hand` and named by its issue."""
    items = [{"n": 1695, "t": "The lock line", "s": "open", "st": "Doing", "l": ["bug"]},
             {"n": 1690, "t": "Done by hand", "s": "closed", "st": "Doing", "l": ["bug"]},
             {"n": 1691, "t": "Queued", "s": "open", "st": "Todo", "l": ["bug"]}]
    ans = _answer(hub, items)
    lines = [r["text"] for r in ans["lines"]]
    assert "hand work #1695 The lock line · in Doing, no registered run" in lines, lines
    assert not any("#1690" in x or "#1691" in x for x in lines), lines
    assert _row(ans, "hand")["id"] == "hand-1695", ans["lines"]


def test_a_doing_card_a_live_hold_names_is_one_line_with_its_carrier(hub):
    """#1713: a held card is its hold, with the carrier, and never again a card with no run. #1697: the row is tagged
    `hold` and carries the id of the hold's own row."""
    _closed(hub)
    _hold(hub, 1713)
    items = [{"n": 1713, "i": "ITEM-1713", "t": "The live work panel", "s": "open", "st": "Doing", "l": ["bug"]}]
    ans = _answer(hub, items)
    lines = [r["text"] for r in ans["lines"]]
    assert [r["label"] for r in hub.hold_rows()] == ["Manual · #1713"]
    named = [x for x in lines if "#1713" in x]
    assert len(named) == 1, lines
    assert "Manual" in named[0], named
    assert not any("hand work #1713" in x for x in lines), lines
    assert _row(ans, "hold")["id"] == hub.hold_rows()[0]["id"] == "hold-1713", ans["lines"]
    assert not any(r["kind"] == "hand" for r in ans["lines"]), ans["lines"]


def test_a_hal9k_hold_line_names_its_carrier(hub):
    """#1713: the hold line names the carrier the registry holds, HAL9k as well as Manual."""
    _closed(hub)
    _hold(hub, 1719, by="hal9k")
    hold = _row(_answer(hub), "hold")
    assert hold["id"] == "hold-1719" and "HAL9k · #1719" in hold["text"], hold


def test_a_held_card_whose_row_carries_its_item_id_is_named_once(hub):
    """#1713: the registry keys a hold by the issue (`auto/hold-<n>`), while a board row also carries its Project item id;
    the panel matches the same card on either identifier, so one hold is one line."""
    _closed(hub)
    _hold(hub, 1718)
    items = [{"n": 1718, "i": "ITEM-1718", "t": "Held beside its run", "s": "open", "st": "Doing", "l": []}]
    lines = _lines(hub, items)
    assert len([x for x in lines if "#1718" in x]) == 1, lines


def test_a_doing_card_a_live_run_names_is_not_listed_twice(hub, monkeypatch, tmp_path):
    run = tmp_path / "run-20261113-000000-abcdef"
    run.mkdir()
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    monkeypatch.setattr(hub, "run_row", lambda folder, port, procs: ({"id": run.name, "issues": [1696], "stage": "router", "age": 60}, None))
    lines = _lines(hub, [{"n": 1696, "t": "Debt", "s": "open", "st": "Doing", "l": []}])
    assert sum("#1696" in x for x in lines) == 1, lines


def test_the_driver_locks_fold_into_one_line_with_no_pid(hub):
    """#1692, #1695: a loop driver lock holds a pid and no command; it is no live work, so the drivers share one line.
    #1697: that line is tagged `lock`."""
    locks = [_held(hub, n) for n in ("watch", "single-autobug")]
    try:
        ans = _answer(hub)
    finally:
        for lock in locks:
            shutil.rmtree(lock)
    lines = [r["text"] for r in ans["lines"]]
    assert "🔒 loop drivers running: single-autobug, watch" in lines, lines
    assert not any(str(os.getpid()) in x for x in lines), lines
    assert _row(ans, "lock")["id"] == "lock-drivers", ans["lines"]


def test_a_live_run_names_its_issue_node_and_age(hub, monkeypatch, tmp_path):
    run = tmp_path / "run-20261113-200000-abcd"
    run.mkdir()
    now = time.time()
    rec = run / "record.jsonl"
    rec.write_text(
        f'{{"ts": {now}, "event": "spawn_accepted", "text": "GitHub issue #1674"}}\n'
        f'{{"ts": {now}, "event": "outcome", "node": "wave.landing"}}\n')
    os.utime(rec, (now - 600, now - 600))   # ten minutes since the run's last write: the age reads a concrete number
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    row = next(x for x in _lines(hub) if "#1674" in x)
    assert re.fullmatch(r"run run-20261113-200000-abcd · #1674 · node wave\.landing · age \d+ min", row), row
    assert 9 <= int(re.search(r"age (\d+) min", row).group(1)) <= 11, row   # the record's own ten minutes, never a bare word


def test_a_live_run_with_no_issue_reads_no_issue(hub, monkeypatch, tmp_path):
    run = tmp_path / "run-20261113-210000-efgh"
    run.mkdir()
    now = time.time()
    rec = run / "record.jsonl"
    rec.write_text(f'{{"ts": {now}, "event": "outcome", "node": "wave.landing"}}\n')
    os.utime(rec, (now - 120, now - 120))
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    row = next(x for x in _lines(hub) if x.startswith("run run-20261113-210000-efgh"))
    assert re.fullmatch(r"run run-20261113-210000-efgh · no issue · node wave\.landing · age \d+ min", row), row


def test_the_panel_carries_no_line_on_work_it_cannot_see(hub):
    """#1701: hand work is named by its Doing card, so the panel no longer says it cannot see it."""
    assert not any("not visible" in x for x in _lines(hub))


def test_an_idle_open_loop_says_no_live_work_and_claims_no_hand_work(hub):
    """#1697: an open loop with no run, no lock, no hold and no Doing card has no live work, and it says so: no other
    line takes the empty state's place."""
    (Path(hub.STATE) / "watchd.pid").write_text(f"{os.getpid()}\n")
    ans = _answer(hub)
    assert [(r["kind"], r["text"]) for r in ans["lines"]] == [("empty", "no live work")], ans["lines"]


def test_the_live_answer_carries_the_loop_state_and_its_tagged_rows(hub):
    """#1697: the answer is one object -- the loop's state apart from the rows, and each row tagged with its kind and id.
    The tagged row is a hand-worked Doing card: a ready-for-human card is no row since #1701."""
    (Path(hub.STATE) / "watchd.pid").write_text(f"{os.getpid()}\n")
    ans = _answer(hub, [{"n": 1681, "t": "Health rule", "s": "open", "st": "Doing", "l": ["bug"]}])
    assert ans["loop"] == "on"
    hand = _row(ans, "hand")
    assert hand["id"] == "hand-1681" and "#1681" in hand["text"]
    assert "no live work" not in [r["text"] for r in ans["lines"]]
    assert all(set(r) >= {"kind", "id", "text"} for r in ans["lines"]), ans["lines"]


def test_a_stopped_loop_is_a_tagged_row_and_a_state(hub):
    """#1697: a stopped loop is named twice over: the answer's own state and one `loop` row."""
    ans = _answer(hub)
    assert ans["loop"] == "off"
    assert _row(ans, "loop") == {"kind": "loop", "id": "loop", "text": "loop off"}


def test_a_live_run_line_is_tagged_with_its_own_kind_and_id(hub, monkeypatch, tmp_path):
    """#1697: a run row carries its own identity in the JSON, so the card never reads a run id out of prose."""
    run = tmp_path / "run-20261113-220000-ijkl"
    run.mkdir()
    now = time.time()
    (run / "record.jsonl").write_text(
        f'{{"ts": {now}, "event": "spawn_accepted", "text": "GitHub issue #1680"}}\n'
        f'{{"ts": {now}, "event": "outcome", "node": "wave.review"}}\n')
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    row = next(r for r in _answer(hub)["lines"] if r["kind"] == "run")
    assert row["id"] == "run-20261113-220000-ijkl" and "#1680" in row["text"]


def test_a_malformed_lock_dir_never_breaks_the_panel(hub):
    """#1697: a stray file and a pid that is a directory are no lock, never a failed /api/live: the scan checks that the
    entry is a directory before it reads it."""
    locks = Path(hub.STATE) / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "a-stray-file").write_text("not a lock\n")
    (locks / "no-pid").mkdir()
    (locks / "bad" / "pid").mkdir(parents=True)   # a pid that is a directory cannot be read as one
    try:
        ans = _answer(hub)
    finally:
        shutil.rmtree(locks)
    assert not any(r["kind"] in ("gate", "lock") for r in ans["lines"]), ans["lines"]


def test_a_lock_read_that_raises_is_skipped_never_raised(hub, monkeypatch):
    """#1697: one unreadable lock is no lock: the panel still answers, and the lock is never drawn, as a gate or among
    the loop drivers."""
    lock = Path(hub.STATE) / "locks" / "bad"
    lock.mkdir(parents=True, exist_ok=True)
    real = hub._read

    def boom(p):
        if str(p).startswith(str(lock)):
            raise OSError("unreadable lock")
        return real(p)

    monkeypatch.setattr(hub, "_read", boom)
    try:
        ans = _answer(hub)
    finally:
        shutil.rmtree(lock)
    assert not any(r["kind"] in ("gate", "lock") or "bad" in r["text"] for r in ans["lines"]), ans["lines"]


def test_the_live_answer_carries_the_board_cache_age(hub):
    """#1697: the hand-worked Doing cards come from the board read the hub holds, so the answer says how old that read is."""
    now = time.time()
    hub.board_cache = {"t": now - 600, "items": [], "prs": [], "lc": {}, "err": None}
    ans = hub.live_work_lines([], state=hub.STATE, checkout=hub.CHECKOUT, procs={}, now=now)
    assert 595 <= ans["boardAge"] <= 610, ans["boardAge"]


def test_the_panel_takes_the_column_the_board_draws(hub, monkeypatch):
    """#1713: the panel reads the column the board draws, so an old Status name and a recorded move both give Doing.
    The overlay is the board's own setting (#651) and it is read on, the default the served board draws with."""
    monkeypatch.setenv("SG_PENDING_OVERLAY", "on")
    now = int(time.time())
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5))
    hub.MOVES.write_text(json.dumps({"ITEM-1711": {"column": "Doing", "at": at, "by": "owner"}}))
    try:
        # `ot` is the read's own start instant: the production row the overlay compares a proved move against (#651)
        items = [{"n": 1711, "i": "ITEM-1711", "t": "Moved by hand", "s": "open", "st": "Todo", "l": ["bug"], "ot": now},
                 {"n": 1712, "i": "ITEM-1712", "t": "An old column name", "s": "open", "st": "In Progress", "l": ["bug"], "ot": now}]
        ans = _answer(hub, items)
    finally:
        hub.MOVES.unlink(missing_ok=True)
    lines = [r["text"] for r in ans["lines"]]
    assert "hand work #1711 Moved by hand · in Doing, no registered run" in lines, lines
    assert "hand work #1712 An old column name · in Doing, no registered run" in lines, lines
    assert [r["id"] for r in ans["lines"] if r["kind"] == "hand"] == ["hand-1711", "hand-1712"], ans["lines"]


def _card_fn(hub):
    return re.search(r"^function liveCard\(.*?^ return\{[^\n]*$", hub.PAGE, re.M | re.S).group(0)


def _card_run(hub, prelude, live="[]", row='"<row "+r.id+">"'):
    """The card's html for one answer and one list of live rows, as the JSON text node prints. `row` is the stand-in for
    the page's run row."""
    real_age = re.search(r"^const age=s=>[^\n]*", hub.PAGE, re.M).group(0)   # the page's own age rule, not a stand-in
    script = ('const esc=s=>String(s);' + real_age + 'const liveRow=(r,shown)=>' + row + ';const LIVE_ROWS=4;\n'
              + prelude + "\n" + _card_fn(hub) + f'\nconsole.log(JSON.stringify(liveCard({live},"").html));\n')
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_gate_that_checks_a_live_run_is_drawn_with_its_row(hub):
    """#1713: the card dropped every line that names a live run, so a gate that checks one was never drawn. #1697: the
    gate is placed by the run id its row carries, never by a substring of its text."""
    if NODE is None:
        pytest.skip("node is required")
    gate = "gate: pid 4242 checks run run-b · #1696"
    html = json.loads(_card_run(
        hub,
        'let liveWork={loop:"off",boardAge:null,lines:[{kind:"loop",id:"loop",text:"loop off"},'
        '{kind:"gate",id:"gate-gate",run:"run-b",text:' + json.dumps(gate) + '}]};',
        '[{id:"run-b",age:5,spend:0}]',
        row='"<div class=r data-id=\\""+r.id+"\\">"+r.id+"</div>"'))
    assert html.count(gate) == 1, "the gate that checks a live run is drawn, and once"
    assert "bash" not in html, "no line shows bash as the thing served"
    assert html.index('data-id="run-b"') < html.index(gate) < html.index("loop off"), "the gate is drawn beside the run's row"


def test_a_gate_is_placed_by_the_run_id_it_carries_never_by_its_text(hub):
    """#1697: a gate whose text happens to hold a live run's id, and whose own run is another, is not drawn beside that
    run's row; a gate that names no run is drawn once, below the rows."""
    if NODE is None:
        pytest.skip("node is required")
    other = "gate: pid 4242 checks run run-bb · #1696"
    bare = "gate: pid 4343 runs fast-gate.sh"
    html = json.loads(_card_run(
        hub,
        'let liveWork={loop:"off",boardAge:null,lines:[{kind:"loop",id:"loop",text:"loop off"},'
        '{kind:"gate",id:"gate-gate",run:"run-bb",text:' + json.dumps(other) + '},'
        '{kind:"gate",id:"gate-full",run:null,text:' + json.dumps(bare) + '}]};',
        '[{id:"run-b",age:5,spend:0}]'))
    assert html.count(other) == 1 and html.count(bare) == 1, html
    assert html.index("loop off") < html.index(other), "a gate of another run is not drawn beside this run's row"


def test_the_card_draws_a_held_card_once_as_its_hold_line(hub):
    """#1713: the Live work card draws a held card once: its hold line, never again as a run row of the same hold."""
    if NODE is None:
        pytest.skip("node is required")
    hold = "hold: Manual · #1713 The live work panel"
    row = {"id": "hold-1713", "label": "Manual · #1713", "age": 0, "rank": 0, "kind": "hold", "issues": ["1713"]}
    html = json.loads(_card_run(
        hub,
        'let liveWork={loop:"on",boardAge:null,lines:[{kind:"hold",id:"hold-1713",text:' + json.dumps(hold) + '}]};',
        "[" + json.dumps(row) + "]",
        row='"<div class=r data-id=\\""+r.id+"\\">"+r.label+"</div>"'))
    assert html.count("#1713") == 1, html
    assert "Manual" in html, html


def test_the_card_dedupes_a_run_row_by_kind_and_id(hub):
    """#1697: dedupe by identity, never by a substring of the text: a run id that is a prefix of another's must not
    swallow the other's line, and a line of another kind that names the run in its text is still drawn."""
    if NODE is None:
        pytest.skip("node is required")
    html = _card_run(hub,
                     'let liveWork={loop:"on",boardAge:null,lines:['
                     '{kind:"run",id:"run-b",text:"run run-b · #1674 · node wave.landing · age 3 min"},'
                     '{kind:"run",id:"run-bb",text:"run run-bb · #1676 · node wave · age 1 min"},'
                     '{kind:"hand",id:"hand-1695",text:"hand work #1695 Fix run-b · in Doing, no registered run"}]};',
                     '[{id:"run-b",age:5,spend:0}]')
    assert "run run-b · #1674" not in html, html   # the drawn run's own line is not drawn twice
    assert "run run-bb · #1676" in html, html      # a longer id is another run, not a substring of the first
    assert "hand work #1695 Fix run-b" in html, html   # a card whose title holds the run's id is not the run


def test_the_card_draws_the_loop_lines_and_no_run_twice(hub):
    if NODE is None:
        pytest.skip("node is required")
    html = _card_run(hub,
                     'let liveWork={loop:"off",boardAge:null,lines:['
                     '{kind:"loop",id:"loop",text:"loop off"},'
                     '{kind:"run",id:"run-b",text:"run run-b · #1674 · node wave.landing · age 3 min"},'
                     '{kind:"run",id:"run-c",text:"run run-c · #1676 · node wave · age 1 min"},'
                     '{kind:"hand",id:"hand-1695",text:"hand work #1695 The lock line · in Doing, no registered run"}]};',
                     '[{id:"run-b",age:5,spend:0}]')
    assert "loop off" in html and "run-c" in html and "hand work #1695" in html
    assert "no live work" not in html
    assert html.count("run-b") == 1


def test_the_card_draws_the_board_cache_age(hub):
    if NODE is None:
        pytest.skip("node is required")
    html = _card_run(hub, 'let liveWork={loop:"on",boardAge:720,lines:[{kind:"empty",id:"",text:"no live work"}]};')
    assert "board read 12m ago" in html, html


def test_the_card_owns_no_empty_state_beside_the_hub_s_own(hub):
    """#1697: the hub answers the empty state as its own tagged `empty` row and a failed read as a `notice` row, so the page
    draws no `no live work` of its own while it holds an answer: a stopped loop with a live run, and an answer whose only
    row the card deduped away, each keep the reader's two cases apart. The fallback stands only before the first read."""
    if NODE is None:
        pytest.skip("node is required")
    holding = _card_run(hub, 'let liveWork={loop:"on",boardAge:null,lines:[]};')
    assert "no live work" not in holding, holding   # no answer row, no empty state of the page's own
    once = _card_run(hub, 'let liveWork={loop:"on",boardAge:null,lines:[{kind:"empty",id:"",text:"no live work"}]};')
    assert once.count("no live work") == 1, once    # the hub's own empty row is the one the reader sees
    drained = _card_run(hub,
                        'let liveWork={loop:"off",boardAge:null,lines:['
                        '{kind:"loop",id:"loop",text:"loop off"},'
                        '{kind:"run",id:"run-b",text:"run run-b · #1674 · node wave.landing · age 3 min"}]};',
                        '[{id:"run-b",age:5,spend:0}]')
    assert "no live work" not in drained, drained   # a loop off while a run still lives is not an empty tile


def test_a_failed_live_read_reads_live_work_unreadable(hub):
    """#1697: a /api/live that fails is named in the panel, never drawn as an empty tile."""
    if NODE is None:
        pytest.skip("node is required")
    poll = re.search(r"^async function livePoll\(\).*$", hub.PAGE, re.M).group(0)
    script = ('let liveWork=null;function refresh(){}\n'
              'async function fetch(){throw new Error("down")}\n' + poll +
              "\nconst esc=s=>String(s);" + re.search(r"^const age=s=>[^\n]*", hub.PAGE, re.M).group(0)
              + 'const liveRow=(r,shown)=>"<row "+r.id+">";const LIVE_ROWS=4;\n'
              + _card_fn(hub) + '\nlivePoll().then(()=>console.log(JSON.stringify(liveCard([],"").html)));\n')
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "live work unreadable" in out.stdout, out.stdout


def test_the_live_poll_is_its_own_statement_after_the_runs_poll(hub):
    """#1697: a failed runs read must not skip the live read."""
    line = re.search(r"^async function poll\(\).*$", hub.PAGE, re.M).group(0)
    assert "refresh();livePoll()" not in line, line
    assert "catch(e){}livePoll()}" in line, line


def test_the_page_reads_live_work_on_load_and_after_a_loop_action(hub):
    """#1697: the live rows are read when the page loads, and again after a start, a drain or a stop."""
    assert re.search(r"^setInterval\(pollIfVisible\(loopPoll\),5000\);loopPoll\(\);livePoll\(\);", hub.PAGE, re.M), \
        "the page does not read the live work on load"
    loop_post = re.search(r"^async function loopPost\(.*$", hub.PAGE, re.M).group(0)
    assert "livePoll()" in loop_post, loop_post


def test_the_page_reads_live_work_after_a_run_pause_or_resume(hub):
    """#1697: a paused or resumed run changes its live line, so the page reads the live work again."""
    m = re.search(r'\$\("#rkpause"\)\.onclick.*?^addEventListener', hub.PAGE, re.M | re.S)
    assert m and "livePoll()" in m.group(0)
