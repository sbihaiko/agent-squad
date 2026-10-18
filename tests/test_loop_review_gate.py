"""#601, ADR-0145 rule 9: the loop's workflow reviews each child inside its run, through the dispatch review gate (#599).

The gate's command is graph data. In the loop it runs runreview.sh (env.sh names it in SG_RUN_REVIEW); outside the loop no
SG_RUN_REVIEW is set and the command answers "no review here", so a run of the plugin alone lands its children as before.
"""
import json
import os
import stat
from pathlib import Path

from squad import gates, graph

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "docs" / "design" / "router.graph.json"
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"


def _review_gate():
    doc = json.loads(ROUTER.read_text())
    nodes = {n["name"]: n for n in doc["nodes"]}
    return doc, nodes[nodes["route"]["review"]]


def test_the_loop_workflow_declares_the_review_gate():
    doc, gate = _review_gate()
    assert gate["kind"] == "gate" and gate["check"] == "review-command"
    assert gate["params"]["deadline"] > 0
    assert graph.validate(doc) == []


def _argv(env, tmp_path):
    _, gate = _review_gate()
    argv = gates.review_argv(gate["params"]["argv"], {"run_id": "run-1", "child": "route.1.1", "issue": "7",
                                                      "worktree": str(tmp_path), "base": "abc"})
    old = dict(os.environ)
    try:
        os.environ.pop("SG_RUN_REVIEW", None)
        os.environ.update(env)
        return gates.review_command(argv, str(tmp_path), 10)
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_outside_the_loop_the_gate_answers_no_review_here(tmp_path):
    r = _argv({}, tmp_path)
    assert r["exit_code"] == gates.REVIEW_NONE_EXIT and r["verdict"] == "no_review"


def test_in_the_loop_the_gate_runs_the_named_review_with_the_run_child_and_issue(tmp_path):
    log, stub = tmp_path / "argv.log", tmp_path / "runreview.sh"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    r = _argv({"SG_RUN_REVIEW": str(stub)}, tmp_path)
    assert r["verdict"] == "approve"
    assert log.read_text() == "run-1 route.1.1 7\n"


def test_env_names_the_run_review_beside_it():
    text = (SCRIPTS / "env.sh").read_text()
    assert 'SG_RUN_REVIEW:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runreview.sh' in text
    assert "SG_RUN_REVIEW" in next(line for line in text.splitlines() if line.startswith("export SG_LSOF"))


def test_a_test_run_never_reaches_the_live_loops_review(monkeypatch):
    from tests.conftest import drop_live_paths
    monkeypatch.setenv("SG_RUN_REVIEW", "/live/runreview.sh")
    drop_live_paths()
    assert "SG_RUN_REVIEW" not in os.environ
