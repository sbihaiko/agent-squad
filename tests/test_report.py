"""Ticket #37: the adaptation report (ADR-0109, ADR-0111 rule 6)."""
import re
from pathlib import Path

import pytest

from squad import cli, report
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.flows import agent, doc
from tests.test_adapt import ADAPT, FLAG, p, prop


def looped():
    """`target` runs twice in a loop; `prop` (first in the loop) shrinks its turns before its second entry."""
    nodes = [agent("prop", output_key="x", may_propose=True, adapt={"kinds": ["set_model"], "models": ["sonnet"], "sees": []}, next={"fail": "exit:fail"}),
             agent("target", output_key="x", adapt={**ADAPT, "tools": ["Read"]}, next={"fail": "repeat"}),
             {"name": "work", "kind": "loop", "max": {"value": 3, "min": 1}, "children": ["prop", "target"]}]
    return doc(nodes, ["work"], facts=[FLAG])


SCRIPT = {"prop": [{"output": {"x": 1}, "spend": 0.05}, prop(p("shrink_turns", 5)), {"output": {"x": 1}, "spend": 0.05}],
          "target": [{"output": {"x": 1, "status": "fail", "reasons": ["too_big"]}, "spend": 0.4, "turns": 9},
                     {"output": {"x": 1}, "spend": 0.1, "turns": 4}]}


async def ran(tmp_path):
    eng = Engine(from_doc(looped()), ScriptedAdapter(SCRIPT), tmp_path)
    await eng.run()
    return eng, read(tmp_path / "record.jsonl")


@pytest.mark.anyio
async def test_the_report_joins_an_applied_proposal_to_the_previous_and_next_entry_with_deltas(tmp_path):
    eng, events = await ran(tmp_path)
    (r,) = report.build(events)
    assert (r["kind"], r["target"], r["old"], r["requested"]) == ("shrink_turns", "target", 10, 5)
    assert r["previous"]["entry"] == 1 and r["next"]["entry"] == 2
    assert r["delta"]["outcome"] == "fail -> pass" and r["delta"]["spend"] == -0.3 and r["delta"]["turns"] == -5
    assert isinstance(r["delta"]["elapsed"], float) or r["delta"]["elapsed"] is None
    assert r["previous"]["reasons"] == ["too_big"]


@pytest.mark.anyio
async def test_the_report_carries_no_label_no_threshold_and_names_its_instrument(tmp_path):
    _, events = await ran(tmp_path)
    (r,) = report.build(events)
    text = report.render([r]).lower()
    assert "not a measurement" in text and "instrument: code: outcome events" in text
    for word in ("helped", "hurt", "improved", "worse", "better", "threshold", "score", "effective"):
        assert word not in text
    assert not {"label", "verdict", "helped", "score"} & set(r)


@pytest.mark.anyio
async def test_a_missing_entry_or_a_missing_spend_shows_unavailable_never_zero(tmp_path):
    _, events = await ran(tmp_path)
    cut = [e for e in events if not (e["event"] == "outcome" and e["node"] == "target" and e["entry"] == 2)]
    (r,) = report.build(cut)
    assert r["next"] is None and set(r["delta"].values()) == {None}
    assert "unavailable" in report.render([r])
    cut2 = [dict(e, spend=None) if e["event"] == "outcome" and e["node"] == "target" and e["entry"] == 2 else e for e in events]
    assert report.build(cut2)[0]["delta"]["spend"] is None


@pytest.mark.anyio
async def test_the_overlay_shows_what_the_dashboard_needs_and_leaves_the_graph_and_hash_alone(tmp_path):
    eng, events = await ran(tmp_path)
    (r,) = report.build(events)
    assert {"proposal_id", "kind", "old", "requested", "range", "facts", "delta"} <= set(r)
    assert eng.g.hash == events[0]["graph_hash"] == from_doc(looped()).hash


def test_no_engine_module_imports_the_report():
    src = Path(__file__).resolve().parent.parent / "squad"
    users = [f.name for f in src.glob("*.py") if f.name not in ("report.py", "cli.py") and re.search(r"\breport\b", f.read_text()) and re.search(r"(import|from)\s+.*\breport\b", f.read_text())]
    assert users == []


@pytest.mark.anyio
async def test_the_cli_prints_the_report(tmp_path, capsys):
    await ran(tmp_path / "r")
    assert cli.main(["report", "x", "--run-dir", str(tmp_path / "r")]) == 0
    assert "shrink_turns on target: 10 -> 5" in capsys.readouterr().out
    assert cli.main(["report", "x", "--run-dir", str(tmp_path / "none")]) == 2
