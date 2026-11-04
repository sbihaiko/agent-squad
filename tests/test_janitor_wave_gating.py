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


@pytest.mark.heavy
def test_a_landing_red_reads_gating_for_every_card_of_the_wave_across_a_restart(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    assert _column(mod, CHILD) == "Gating"
    assert _column(mod, SIBLING) == "Gating"
    reloaded, _ = _janitor(tmp_path, monkeypatch)
    assert _column(reloaded, CHILD) == "Gating"


def test_a_sibling_entry_pass_does_not_clear_the_landing_red(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    ga.record_attempt(journal, SIBLING, "siblinghead", "pass", "e-sib", now=200, wave=RUN)
    assert _column(mod, CHILD) == "Gating"


def test_a_landing_pass_moves_the_wave_card_out_of_gating(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    ga.record_attempt(journal, 0, "landhead2", "pass", "", now=200, wave=RUN)
    assert _column(mod, CHILD) == "Code Complete"


def test_a_live_gate_marker_reads_gating_and_a_gone_gate_does_not(tmp_path, monkeypatch):
    mod, state = _janitor(tmp_path, monkeypatch)
    marker = state / f"gate-wave-{RUN}.{os.getpid()}.live"
    marker.write_text(f"{os.getpid()} {int(time.time())} 900\n")
    assert _column(mod, CHILD) == "Gating"
    marker.unlink()
    assert _column(mod, CHILD) == "Code Complete"


def test_a_landed_wave_reads_closed_even_with_a_red_landing(tmp_path, monkeypatch):
    mod, _ = _janitor(tmp_path, monkeypatch)
    ga, journal = _journal(tmp_path)
    ga.record_attempt(journal, 0, TIP, "fail", "", now=100, wave=RUN)
    mod._wv[RUN] = (TIP, "behind")
    assert _column(mod, CHILD) == "Closed"
