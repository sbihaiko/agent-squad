"""A janitor pass has a deadline, and it names its slow calls (#1774).

The watch cycle starts the janitor detached under its own lock, so a hung pass no longer holds the cycle. A pass that
hangs would then hold only its lock, in silence: one pass of 5046 s ran on 2026-11-14. So a pass past SG_JANITOR_DEADLINE
refuses every further call and ends, with one line in cycle.log and one in alerts.log. Each call of SG_JANITOR_SLOW_CALL
seconds or more writes its time and its command, and the pass ends with one line that counts its calls.

These cases drive janitor.py end to end with the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh
and a temp state dir. No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_janitor_card_done_base import janitor, sandbox, writes


def _log(tmp_path: Path, name: str) -> str:
    f = tmp_path / "st" / name
    return f.read_text() if f.exists() else ""


@pytest.mark.heavy
def test_a_pass_past_its_deadline_refuses_every_further_call_and_says_so(tmp_path):
    sandbox(tmp_path, "echo 0")
    p = janitor(tmp_path, SG_JANITOR_DEADLINE="0.000001", JANITOR_NOTIFY="/usr/bin/true")
    assert p.returncode == 0, p.stderr
    cyc, alerts = _log(tmp_path, "cycle.log"), _log(tmp_path, "alerts.log")
    assert re.search(r"janitor pass deadline \S+s: \d+ calls, the rest of the pass is skipped", cyc), cyc
    assert "janitor pass deadline" in alerts, f"no alert for a pass past its deadline: {alerts}"
    assert writes(tmp_path) == "", f"a pass past its deadline still called out: {writes(tmp_path)}"


@pytest.mark.heavy
def test_a_slow_call_is_named_and_the_pass_counts_its_calls(tmp_path):
    sandbox(tmp_path, "echo 0")
    p = janitor(tmp_path, SG_JANITOR_SLOW_CALL="0", JANITOR_NOTIFY="/usr/bin/true")
    assert p.returncode == 0, p.stderr
    cyc = _log(tmp_path, "cycle.log")
    assert re.search(r"janitor slow call \d+(\.\d+)?s: gh ", cyc), cyc
    m = re.search(r"janitor pass \d+s: (\d+) calls$", cyc, re.M)
    assert m and int(m.group(1)) > 0, f"no count line at the end of the pass: {cyc}"
    assert "pass deadline" not in cyc, "a pass inside its deadline reported one"


@pytest.mark.heavy
def test_a_fast_pass_names_no_slow_call(tmp_path):
    sandbox(tmp_path, "echo 0")
    janitor(tmp_path, JANITOR_NOTIFY="/usr/bin/true")
    cyc = _log(tmp_path, "cycle.log")
    assert "slow call" not in cyc, cyc
    assert re.search(r"janitor pass \d+s: \d+ calls$", cyc, re.M), cyc
