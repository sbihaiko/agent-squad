"""The Gating column a wave's card reads, as the janitor writes it (#1257, ADR-0170).

These cases load janitor.py in-process with a temp state dir and board cache, and stub only its
network reads (the wave branch map, the review read, the dev check run). The gating fact is read from
the attempt journal, so each case reloads the module and expects the same column.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
RUN, TIP, CHILD, SIBLING = "W1", "wavetip", 1257, 1258


def _janitor(tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    (state / "auto").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SG_STATE", str(state))
    monkeypatch.setenv("SG_REPO", "o/r")
    monkeypatch.setenv("SG_OWNER", "o")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_BASE", "main")
    monkeypatch.setenv("SG_BOARD_CACHE", str(tmp_path / "board-cache.json"))
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"janitor_under_test_{time.time_ns()}", SCRIPTS / "janitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.work_branches = lambda: None
    mod.review_of = lambda *_: ""
    mod.dev_ci_running = lambda: False
    mod._wv.clear()
    mod._wv[RUN] = (TIP, "ahead")
    return mod, state


def _column(mod, n, run=RUN):
    import board_columns
    return board_columns.column_for(mod.wave_facts(n, {run}))[0]


def _journal(tmp_path: Path):
    import importlib.util as iu
    spec = iu.spec_from_file_location("gate_attempts_for_janitor_test", SCRIPTS / "gate_attempts.py")
    ga = iu.module_from_spec(spec)
    spec.loader.exec_module(ga)
    return ga, ga.journal_path(str(tmp_path / "board-cache.json"), "o/r")


@pytest.fixture(autouse=True)
def _no_live_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SG_GATE", "GATE_")):
            monkeypatch.delenv(k, raising=False)


@pytest.mark.heavy   # one janitor run under -I: 1.7 s here, 2 s on a CI runner
def test_the_gating_fact_finds_its_journal_module_from_the_scripts_own_tree(tmp_path, monkeypatch):
    """The janitor runs under `python3 -I`, so its own directory is off sys.path and every sibling import puts it
    there itself (#1545). The gating fact must find gate_attempts without leaning on an earlier import's side effect."""
    mod, _ = _janitor(tmp_path, monkeypatch)
    monkeypatch.delenv("SG_BOARD_CACHE", raising=False)
    for name in ("gate_attempts", "board_flow_history"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(SCRIPTS)])
    assert mod._gate_gating(CHILD, RUN) is False


@pytest.mark.heavy
def test_a_landing_red_reads_gating_for_every_card_of_the_wave_across_a_restart(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    assert _column(mod, CHILD) == "Gating"
    assert _column(mod, SIBLING) == "Gating"
    reloaded, _ = _janitor(tmp_path, monkeypatch)
    assert _column(reloaded, CHILD) == "Gating"


@pytest.mark.heavy
def test_a_sibling_entry_pass_does_not_clear_the_landing_red(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    ga.record_attempt(journal, SIBLING, "siblinghead", "pass", "e-sib", now=200, wave=RUN)
    assert _column(mod, CHILD) == "Gating"


@pytest.mark.heavy
def test_a_landing_pass_moves_the_wave_card_out_of_gating(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    ga.record_attempt(journal, 0, "landhead2", "pass", "", now=200, wave=RUN)
    assert _column(mod, CHILD) == "Code Complete"


@pytest.mark.heavy
def test_a_live_gate_marker_reads_gating_and_a_gone_gate_does_not(tmp_path, monkeypatch):
    mod, state = _janitor(tmp_path, monkeypatch)
    marker = state / f"gate-wave-{RUN}.{os.getpid()}.live"
    marker.write_text(f"{os.getpid()} {int(time.time())} 900\n")
    assert _column(mod, CHILD) == "Gating"
    marker.unlink()
    assert _column(mod, CHILD) == "Code Complete"


@pytest.mark.heavy
def test_a_landed_wave_reads_closed_even_with_a_red_landing(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    mod._wv[RUN] = (TIP, "behind")
    assert _column(mod, CHILD) == "Closed"


# The reconciler's write (#1430): the gating fact reached column_for, but wave_column handled only Closed, so a card
# never read Gating. One pass runs against a stubbed network and a mutable card map: board_write records each write and
# updates that map, as the real writer's cache update does, so a second pass reads the column the first one wrote.
WAVE_RUN = "run-W1"


def _reconcile(tmp_path: Path, monkeypatch, card: str, standing: str, live_gate: bool = False):
    state = tmp_path / "state"
    (state / "auto").mkdir(parents=True, exist_ok=True)
    co = tmp_path / "co"
    run = co / "runs" / WAVE_RUN
    run.mkdir(parents=True, exist_ok=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {CHILD}:\n")
    (run / "record.jsonl").write_text(
        '{"event":"run_start","run_id":"run-W1","ts":"2026-11-05T10:00:00Z"}\n'
        '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n')
    (state / "active-runs.txt").write_text(WAVE_RUN + "\n")
    (state / "auto" / f"waved-{WAVE_RUN}").write_text(f"wave/{WAVE_RUN}\n")
    if live_gate:
        (state / f"gate-wave-{WAVE_RUN}.{os.getpid()}.live").write_text(f"{os.getpid()} {int(time.time())} 900\n")
    for k, v in {"SG_STATE": str(state), "SG_CHECKOUT": str(co), "SG_REPO": "o/r", "SG_BASE": "main",
                 "SG_BOARD_VOCAB": "shipped", "SG_BOARD_CACHE": str(tmp_path / "board-cache.json"),
                 "SG_TAKEOVER_SECONDS": "1000000000000", "SG_LIVE_DIR": str(tmp_path / "no-live")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SG_OWNER", raising=False)   # the import-time pass is skipped: this case drives one pass itself
    monkeypatch.delenv("SG_PROJECT", raising=False)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"janitor_pass_{time.time_ns()}", SCRIPTS / "janitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.owner, mod.proj, mod.repo0 = "o", "4", "o/r"
    mod.work_branches = lambda: {}
    mod._wv[WAVE_RUN] = (TIP, standing)
    mod.review_of = lambda *_: ""
    mod.dev_ci_running = lambda: False
    mod.entered = lambda: {(WAVE_RUN, str(CHILD))}
    mod.landings = lambda: {str(CHILD): {(WAVE_RUN, "run")}}
    mod.open_issues = lambda: (False, {})
    mod.issue_info = lambda n: ("open", "", "Issue")
    mod.issue_labels = lambda n: set()
    mod.named_in_land = lambda: {}
    have = {str(CHILD): card}
    mod.cards = lambda p: (True, have)
    writes = []

    def board_write(args, n, why, reason=None, event=None):   # the janitor's writer carries the run the write belongs to (#861)
        writes.append((list(args), reason))
        a = args[1:] if args and args[0] == "--add" else args
        if len(a) == 4 and not a[0].startswith("--") and a[1] in have:
            have[str(a[1])] = a[2]
        return True

    mod.board_write = board_write
    mod.terminal_facts = lambda n, col: {"base": "dev", "branch": f"wave/{WAVE_RUN}", "sha": "abc1234", "test": ""}
    mod.reconcile_board()
    return writes, have


def test_the_shipped_axis_orders_gating_after_code_complete_and_before_closed():
    """The writer is reachable only on an axis that ranks Gating forward of Code Complete (ADR-0170 rule 1): the fact
    answers Gating, a write from Code Complete into it is not past, and a write back out of it is."""
    import board_columns
    assert board_columns.column_for({"gating": True, "wave_branch": True})[0] == "Gating"
    assert not board_columns.past("Code Complete", "Gating"), "a write from Code Complete into Gating reads as a move back"
    assert board_columns.past("Gating", "Code Complete"), "a green gate could pull a Gating card back to Code Complete"
    assert not board_columns.past("Gating", "Closed"), "the one forward move out of Gating reads as a move back"
    assert board_columns.past("Closed", "Gating"), "a Closed card could return to Gating"


@pytest.mark.heavy
def test_a_live_gate_writes_the_wave_card_to_gating(tmp_path, monkeypatch):
    writes, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead", live_gate=True)
    assert writes == [(["o", "4", "Gating", str(CHILD)], None)], f"the card did not enter Gating: {writes}"


@pytest.mark.heavy
def test_a_red_attempt_with_no_live_marker_still_writes_gating(tmp_path, monkeypatch):
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=int(time.time()), wave=WAVE_RUN)
    writes, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead")
    assert writes == [(["o", "4", "Gating", str(CHILD)], None)], f"a red attempt did not hold Gating: {writes}"


@pytest.mark.heavy
def test_no_attempt_leaves_the_wave_card_in_code_complete(tmp_path, monkeypatch):
    writes, have = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead")
    assert writes == [], f"a card with no gate attempt moved: {writes}"
    assert have[str(CHILD)] == "Code Complete"


@pytest.mark.heavy
def test_a_second_pass_writes_nothing_when_the_card_already_reads_gating(tmp_path, monkeypatch):
    first, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead", live_gate=True)
    assert first == [(["o", "4", "Gating", str(CHILD)], None)], f"the first pass did not write Gating: {first}"
    second, have = _reconcile(tmp_path, monkeypatch, "Gating", "ahead", live_gate=True)
    assert second == [], f"a second pass rewrote the same column: {second}"
    assert have[str(CHILD)] == "Gating"


@pytest.mark.heavy
def test_a_closed_card_never_returns_to_gating(tmp_path, monkeypatch):
    """A client closed the card first: the live gate holds it in Gating, while a Closed card keeps the landing's column.
    The landed write is the positive control, so a writer that writes nothing cannot pass this case."""
    landed, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "behind", live_gate=True)
    assert landed == [(["o", "4", "Closed", str(CHILD)], None)], f"the landing did not write Closed: {landed}"
    writes, have = _reconcile(tmp_path, monkeypatch, "Closed", "ahead", live_gate=True)
    assert writes == [], f"a Closed card moved: {writes}"
    assert have[str(CHILD)] == "Closed", "a live gate lost a Closed card"


@pytest.mark.heavy
def test_the_landed_wave_writes_closed_after_gating(tmp_path, monkeypatch):
    gating, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead", live_gate=True)
    assert gating == [(["o", "4", "Gating", str(CHILD)], None)], f"the card did not enter Gating: {gating}"
    closed, _ = _reconcile(tmp_path, monkeypatch, "Gating", "behind")
    assert closed == [(["o", "4", "Closed", str(CHILD)], None)], f"the landed wave did not write Closed: {closed}"


@pytest.mark.heavy
def test_a_gating_card_leaves_only_for_closed(tmp_path, monkeypatch):
    """A green gate does not send the card back to Code Complete (ADR-0170 rule 2): it waits in Gating until the wave lands."""
    first, _ = _reconcile(tmp_path, monkeypatch, "Code Complete", "ahead", live_gate=True)
    assert first == [(["o", "4", "Gating", str(CHILD)], None)], f"the pass did not write Gating: {first}"
    second, have = _reconcile(tmp_path, monkeypatch, "Gating", "ahead")
    assert second == [], f"a Gating card moved back to Code Complete: {second}"
    assert have[str(CHILD)] == "Gating"
