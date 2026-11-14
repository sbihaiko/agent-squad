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
    hub.board_cache = {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None}
    yield


def _hold(hub, n, by="manual", minutes=60, now=None):
    from squad import handhold
    rc = handhold.hold([n], by=by, minutes=minutes, state=hub.STATE, now=now or time.time(), procs={},
                       cwd_procs=lambda: [])
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


def _lines(hub, items=(), **kw):
    return hub.live_work_lines(list(items), state=hub.STATE, checkout=hub.CHECKOUT, procs={}, now=time.time(), **kw)


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
    """#1695: the lock file names `bash`; the holder command line names the run workspace the gate checks."""
    lock = _held(hub, "gate", "bash")
    line = "bash /r/worktrees/child/gui-1673/runs/run-20261113-204951-f6107e/ws/w/contrib/squad-goal/scripts/serial.sh gate bash x/gate.sh --inner bash contrib/squad-goal/scripts/fast-gate.sh"
    try:
        lines = _lines(hub, argv=lambda pid: line)
    finally:
        shutil.rmtree(lock)
    assert f"gate: pid {os.getpid()} checks run run-20261113-204951-f6107e" in lines, lines
    assert not any("serves" in x or x.endswith("bash") for x in lines), lines


def test_a_gate_lock_outside_a_run_names_its_gate_script(hub):
    lock = _held(hub, "gate", "bash")
    try:
        lines = _lines(hub, argv=lambda pid: "bash /repo/contrib/squad-goal/scripts/serial.sh gate bash /repo/contrib/squad-goal/scripts/gate.sh --inner bash contrib/squad-goal/scripts/fast-gate.sh")
    finally:
        shutil.rmtree(lock)
    assert f"gate: pid {os.getpid()} runs fast-gate.sh" in lines, lines


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
    lines = _lines(hub, items)
    assert not any("#1681" in x or "#1682" in x or "ready-for-human" in x for x in lines), lines


def test_a_doing_card_with_no_live_run_is_hand_work(hub):
    """#1701: the panel agrees with the Doing column: a card worked by hand is a line, a closed card is not."""
    items = [{"n": 1695, "t": "The lock line", "s": "open", "st": "Doing", "l": ["bug"]},
             {"n": 1690, "t": "Done by hand", "s": "closed", "st": "Doing", "l": ["bug"]},
             {"n": 1691, "t": "Queued", "s": "open", "st": "Todo", "l": ["bug"]}]
    lines = _lines(hub, items)
    assert "hand work #1695 The lock line · in Doing, no registered run" in lines, lines
    assert not any("#1690" in x or "#1691" in x for x in lines), lines


def test_a_doing_card_a_live_hold_names_is_one_line_with_its_carrier(hub):
    """#1713: a held card is its hold, with the carrier, and never again a card with no run."""
    _closed(hub)
    _hold(hub, 1713)
    items = [{"n": 1713, "t": "The live work panel", "s": "open", "st": "Doing", "l": ["bug"]}]
    lines = _lines(hub, items)
    assert [r["label"] for r in hub.hold_rows()] == ["Manual · #1713"]
    assert not any("hand work #1713" in x for x in lines), lines
    assert [x for x in lines if "#1713" in x] == [], lines


def test_a_doing_card_a_live_run_names_is_not_listed_twice(hub, monkeypatch, tmp_path):
    run = tmp_path / "run-20261113-000000-abcdef"
    run.mkdir()
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    monkeypatch.setattr(hub, "run_row", lambda folder, port, procs: ({"id": run.name, "issues": [1696], "stage": "router", "age": 60}, None))
    lines = _lines(hub, [{"n": 1696, "t": "Debt", "s": "open", "st": "Doing", "l": []}])
    assert sum("#1696" in x for x in lines) == 1, lines


def test_the_driver_locks_fold_into_one_line_with_no_pid(hub):
    """#1692, #1695: a loop driver lock holds a pid and no command; it is no live work, so the drivers share one line."""
    locks = [_held(hub, n) for n in ("watch", "single-autobug")]
    try:
        lines = _lines(hub)
    finally:
        for lock in locks:
            shutil.rmtree(lock)
    assert "🔒 loop drivers running: single-autobug, watch" in lines, lines
    assert not any(str(os.getpid()) in x for x in lines), lines


def test_a_live_run_names_its_issue_node_and_age(hub, monkeypatch, tmp_path):
    run = tmp_path / "run-20261113-200000-abcd"
    run.mkdir()
    now = time.time()
    (run / "record.jsonl").write_text(
        f'{{"ts": {now}, "event": "spawn_accepted", "text": "GitHub issue #1674"}}\n'
        f'{{"ts": {now}, "event": "outcome", "node": "wave.landing"}}\n')
    monkeypatch.setattr(hub, "loop_live", lambda **kw: {"runs": [str(run)], "gates": []})
    lines = _lines(hub)
    assert any("#1674" in x and "wave.landing" in x and "age" in x for x in lines)


def test_the_panel_carries_no_line_on_work_it_cannot_see(hub):
    """#1701: hand work is named by its Doing card, so the panel no longer says it cannot see it."""
    assert not any("not visible" in x for x in _lines(hub))


def test_a_gate_that_checks_a_live_run_is_drawn_with_its_row(hub):
    """#1713: the card dropped every line that names a live run, so a gate that checks one was never drawn."""
    if NODE is None:
        pytest.skip("node is required")
    fn = re.search(r"^function liveCard\(.*?^ return\{[^\n]*$", hub.PAGE, re.M | re.S).group(0)
    gate = "gate: pid 4242 checks run run-b · #1696"
    script = ('const esc=s=>String(s);const age=s=>s+"s";'
              'const liveRow=(r,shown)=>"<div class=r data-id=\\""+r.id+"\\">"+r.id+"</div>";const LIVE_ROWS=4;\n'
              'let liveWork=["loop off",' + json.dumps(gate) + '];\n'
              + fn + '\nconsole.log(JSON.stringify(liveCard([{id:"run-b",age:5,spend:0}],"").html));\n')
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)
    assert gate in html, "the gate that checks a live run is drawn"
    assert "bash" not in html, "no line shows bash as the thing served"
    assert html.index(gate) > html.index('data-id="run-b"'), "the gate is drawn beside the run's row"


def test_the_card_draws_the_loop_lines_and_no_run_twice(hub):
    if NODE is None:
        pytest.skip("node is required")
    fn = re.search(r"^function liveCard\(.*?^ return\{[^\n]*$", hub.PAGE, re.M | re.S).group(0)
    script = ('const esc=s=>String(s);const age=s=>s+"s";const liveRow=(r,shown)=>"<row "+r.id+">";const LIVE_ROWS=4;\n'
              'let liveWork=["loop off","run run-b · #1674 · node wave.landing · age 3 min","run run-c · #1676 · node wave · age 1 min",'
              '"hand work #1695 The lock line · in Doing, no registered run"];\n'
              + fn + '\nconsole.log(JSON.stringify(liveCard([{id:"run-b",age:5,spend:0}],"").html));\n')
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    html = out.stdout
    assert "loop off" in html and "run-c" in html and "hand work #1695" in html
    assert "no live work" not in html
    assert html.count("run-b") == 1
