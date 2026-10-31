"""The router harness keeps the router's stopping rule, sizes its spend cap, and resumes by folder (ticket #151 review).

Guards: the instructions the harness writes keep the `## Stopping rule` of the seed graph; the run spend cap covers the router's
own maximum for every wave plus each child's share; a router run resumes by folder name; an unsound defaults file refuses the run.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from squad import cli, defaults, launch
from squad.adapter import ScriptedAdapter
from squad.graph import from_doc
from squad.options import OptionError
from squad.subject import Prepared
from tests.flows import agent, doc
from tests.test_resume import wait_doc

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design" / "router.graph.json"
# The harness loads scripts/run_router.py by path; the selector reads this list, not the dynamic import.
DEPENDS = ("scripts/run_router.py", "squad/graph.py", "squad/launch.py", "squad/subject.py")


def harness():
    spec = importlib.util.spec_from_file_location("run_router", ROOT / "scripts" / "run_router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def seed():
    return json.loads(DESIGN.read_text())


def node(g, name):
    return next(n for n in g["nodes"] if n["name"] == name)


def test_the_seed_tells_the_router_that_medium_is_the_default_effort():
    """The owner's ruling: medium is the default effort. `allow.efforts` is an order, so a child request that names no
    effort would otherwise take its first entry, and a router that names `high` on every child spends a level the
    configuration never asked for. The instruction is the guard: the engine default applies only to a request that
    leaves the field out (squad/envelope.py)."""
    text = node(seed(), "router")["instructions"]
    assert "leave it out unless the work needs more" in text and "`medium`" in text


def test_the_harness_instructions_keep_the_stopping_rule_of_the_seed():
    g = seed()
    assert "## Stopping rule" in node(g, "router")["instructions"]
    harness().patch_router(g, "The task is the issue.", 1)
    text = node(g, "router")["instructions"]
    assert text.startswith("The task is the issue.") and "## Stopping rule" in text and "return the JSON object" in text


@pytest.mark.parametrize("n", [1, 4, 9, 24])
def test_the_run_spend_cap_covers_the_router_and_its_children(n):
    m = harness()
    g = seed()
    m.patch_router(g, "x", n)
    router_max = node(g, "router")["budget"]["spend"]["max"]
    waves = 1 if n == 1 else max(3, -(-n // 3) + 2)
    assert g["run"]["spend_cap"] >= router_max * waves + n * float(m.CHILD_SPEND)


def test_a_router_run_resumes_by_folder_name_with_no_run_dir(tmp_path):
    both = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}
    out = tmp_path / "runs" / "router-17-x"
    eng = launch.launch(from_doc(wait_doc()), out, adapter=ScriptedAdapter(both, repeat_last=True))
    assert asyncio.run(eng.run()).exit_code == 10 and (out / "record.jsonl").exists()
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"script": both, "repeat_last": True}))
    argv = ["resume", "router-17-x", "--runs-dir", str(tmp_path / "runs"), "--script", str(script), "--answer", "pass"]
    assert asyncio.run(cli._resume(cli.parser().parse_args(argv))) == 0


def _prepared(out):
    out.mkdir(parents=True)
    (out / "target").mkdir()
    gp = out / "graph.json"
    gp.write_text(json.dumps(doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"])))
    return Prepared(17, "issues/17.md", out / "target", gp, out, ["true"], [])


def test_main_refuses_an_unsound_defaults_file(tmp_path, monkeypatch):
    m = harness()
    bad = tmp_path / "bad.yaml"
    bad.write_text(defaults.PATH.read_text().replace("effort: medium", "effort: nonsense"))
    monkeypatch.setattr(defaults, "PATH", bad)
    monkeypatch.setattr(sys, "argv", ["run_router.py", "17"])
    monkeypatch.setattr(m.subject, "prepare", lambda issue, home, bench, out, runs_dir=None: _prepared(tmp_path / "prep"))   # the folder `out` never exists
    with pytest.raises(OptionError, match="unsound"):
        m.main()
