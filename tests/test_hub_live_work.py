"""The Monitor's Live work tile and the holder chip (loop-bug #1664).

The tile is named "Live work" and lists what is being worked on now: a Squad run, a HAL9k hold, and a Manual hold
proven by folder activity (the hold marker's own write time, under the one bound). An orphan hold on a closed or
abandoned issue is never drawn. The card chip names the holder, and a takeover reads "Squad · taken over <time>".
"""
import importlib.util
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


def test_a_live_gate_lock_names_its_pid_and_the_child_it_serves(hub):
    lock = Path(hub.STATE) / "locks" / "gate"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "pid").write_text(f"{os.getpid()}\n")
    (lock / "cmd").write_text("bash contrib/squad-goal/scripts/fast-gate.sh\n")
    try:
        lines = _lines(hub)
    finally:
        shutil.rmtree(lock)
    assert any(f"pid {os.getpid()}" in x and "fast-gate.sh" in x for x in lines)


def test_a_ready_for_human_issue_is_listed_with_its_label(hub):
    items = [{"n": 1681, "t": "Health rule", "s": "open", "l": ["ready-for-human", "bug"]}]
    lines = _lines(hub, items)
    assert any("#1681" in x and "ready-for-human" in x for x in lines)


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


def test_the_panel_says_it_cannot_see_hand_work(hub):
    assert any("hand" in x and "not visible" in x for x in _lines(hub))
