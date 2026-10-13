"""Issue #80: the hub's worker row shows how much of the suite the run's gate counted.

The owner reads progression from the number, not from the colour: a red gate that counted `16 passed of 18` says the
work moved. The row draws the newest counted gate of the run and draws nothing when no gate counted -- a run whose
gate never printed a pytest summary carries no chip, never a 0%, which would read as "nothing passes".
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_run_share", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rec(tmp, events, name="record.jsonl"):
    p = tmp / name
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    return p


def _events(counts):
    out = [{"ts": 1.0, "event": "run_start", "plan": ["main"]},
           {"ts": 2.0, "event": "outcome", "node": "final_gate", "spend": 0.1}]
    if counts is not None:
        out.append({"ts": 3.0, "event": "outcome", "node": "final_gate", "spend": None, "counts": counts})
    return out


def test_the_share_is_the_newest_gate_that_counted(hub, tmp_path):
    p = _rec(tmp_path, _events({"sh -c pytest": {"passed": 16, "failed": 2, "total": 18}}))
    assert hub.scan(p)["share"] == {"passed": 16, "total": 18, "pct": 89}


def test_a_run_no_gate_counted_carries_no_share(hub, tmp_path):
    """The newest counted gate wins, and a run whose commands printed no pytest summary has none: the row then draws no
    chip. Reporting `0` here would be a claim about a suite nobody ran."""
    assert hub.scan(_rec(tmp_path, _events(None)))["share"] is None


def test_the_counts_of_several_commands_of_one_gate_add_up(hub, tmp_path):
    p = _rec(tmp_path, _events({"a": {"passed": 10, "failed": 0, "total": 10}, "b": {"passed": 6, "failed": 2, "total": 8}}))
    assert hub.scan(p)["share"] == {"passed": 16, "total": 18, "pct": 89}


def test_the_row_draws_the_share_and_its_reason(hub):
    page = hub.PAGE
    assert re.search(r"r\.share\?`<span class=\"sh\$\{r\.share\.pct===100\?\" done\":\"\"\}\"", page), "the row no longer draws the share chip"
    assert "the newest gate that counted ran ${r.share.total} tests" in page
    assert re.search(r"\$\{r\.share\.passed\}/\$\{r\.share\.total\} · \$\{r\.share\.pct\}%", page), "the chip no longer names how many of how many passed"


def test_the_full_share_and_a_partial_one_are_drawn_apart(hub):
    css = re.search(r"\.r \.sh\{([^}]*)\}\.r \.sh\.done\{([^}]*)\}", hub.PAGE)
    assert css, "the share chip lost its style"
    assert "var(--warn)" in css.group(1) and "var(--ok)" in css.group(2)
