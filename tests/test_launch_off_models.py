"""A run never starts on a model the hub's models panel switched off (issue #60).

`launch.py` read the panel's `off` switch for one value, the request's "Coding model:" line. The nodes of the workflow
graph run another value: `route.router` pins `opus`, the dispatch `allow.models` starts at `opus` and a child with no
model of its own takes `allow.models[0]`. The switch and the node therefore never met, and a run silently used the
Anthropic models the panel had turned off. The check now reads the graph the run starts, resolves an alias (`opus`,
`sonnet`, `haiku`) to its provider id, and refuses a node whose model is off in any chain. Every case drives the real
`launch.py` with a stub `uv` and `caffeinate`, so no model and no network is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"

# The panel's state in the issue: sonnet off for coding and vision, opus off for review (spelled `claude:` there).
OFF = {"coding_models": {"claude-sonnet-5-5": True}, "vision_models": {"claude-sonnet-5-5": True},
       "review_models": {"claude:claude-opus-5-5": True}}


def graph(router="opus", allow=("opus", "sonnet", "haiku")):
    return {"schema_version": 1, "name": "g", "root": "main", "nodes": [
        {"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["route"]},
        {"name": "route", "kind": "dispatch", "entry": "router", "allow": {"models": list(allow), "efforts": ["low"]}},
        {"name": "router", "kind": "agent", "model": router, "tools": ["Read"]},
    ]}


def launch(tmp_path, doc, off=None, env=None, workflow_file="default"):
    """Run launch.py from a throwaway checkout whose workflow is `doc`; return the finished process and the run folders."""
    s, co, st, bin_ = tmp_path / "s", tmp_path / "co", tmp_path / "st", tmp_path / "bin"
    for d in (s, st, bin_, co / "runs", co / "docs/squad/workflows", tmp_path / "squad"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / "launch.py", s / "launch.py")
    models = {"coding_models": ["claude-sonnet-5-5", "test-model"], "review_models": ["claude:claude-opus-5-5"],
              "vision_models": ["claude-sonnet-5-5"], "hours": {}}
    if off:
        models["off"] = off
    (s / "models.json").write_text(json.dumps(models))
    (s / "hub.py").write_text("print('hub')\n")
    (co / f"docs/squad/workflows/{workflow_file}.graph.json").write_text(json.dumps(doc))
    stub = bin_ / "caffeinate"
    stub.write_text("#!/bin/bash\nmkdir -p runs/run-20261013-000000-aaaaaa\nexit 0\n")
    (bin_ / "uv").write_text("#!/bin/bash\necho uv-not-used\n")
    for p in (stub, bin_ / "uv"):
        p.chmod(0o755)
    req = tmp_path / "req.txt"
    # the existing gate wants the request to name the first coding entry that may run: `test-model` once sonnet is off
    req.write_text(f"Coding model: {'test-model' if off else 'claude-sonnet-5-5'}\n--- issue #60\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    e.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", SG_STATE=str(st), SG_CHECKOUT=str(co), SG_SQUAD=str(tmp_path / "squad"),
             **(env or {}))
    r = subprocess.run([sys.executable, "-I", str(s / "launch.py"), str(req)], cwd=co, env=e, capture_output=True, text=True, timeout=60)
    return r, sorted(p.name for p in (co / "runs").glob("run-*"))


def test_a_router_pinned_to_an_alias_that_resolves_to_an_off_model_is_refused(tmp_path):
    r, runs = launch(tmp_path, graph(router="opus", allow=("haiku",)), off=OFF)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "router" in out and "opus" in out and "claude-opus-5-5" in out and "switched off" in out
    assert runs == []


def test_a_dispatch_whose_default_child_model_is_off_is_refused(tmp_path):
    r, runs = launch(tmp_path, graph(router="haiku", allow=("sonnet", "haiku")), off=OFF)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "route" in out and "sonnet" in out and "claude-sonnet-5-5" in out
    assert runs == []


def test_a_full_id_that_is_off_is_refused_too(tmp_path):
    r, runs = launch(tmp_path, graph(router="claude-sonnet-5-5", allow=("haiku",)), off=OFF)
    assert r.returncode != 0 and "claude-sonnet-5-5" in r.stdout + r.stderr
    assert runs == []


def test_the_workflow_the_run_names_is_the_one_checked(tmp_path):
    r, runs = launch(tmp_path, graph(router="sonnet", allow=("haiku",)), off=OFF, env={"SG_WORKFLOW": "mine"},
                     workflow_file="mine")
    assert r.returncode != 0 and "claude-sonnet-5-5" in r.stdout + r.stderr
    assert runs == []


@pytest.mark.heavy
def test_a_graph_on_models_that_are_on_starts(tmp_path):
    r, runs = launch(tmp_path, graph(router="haiku", allow=("haiku",)), off=OFF)
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs


@pytest.mark.heavy
def test_no_switch_in_models_json_starts_the_same_graph(tmp_path):
    r, runs = launch(tmp_path, graph(), off=None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs


@pytest.mark.heavy
def test_the_deliberate_exception_starts_a_run_on_an_off_model(tmp_path):
    r, runs = launch(tmp_path, graph(), off=OFF, env={"SG_ALLOW_MODEL": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs


@pytest.mark.parametrize("alias,resolved", [("opus", "claude-opus-5-5"), ("sonnet", "claude-sonnet-5-5")])
def test_the_refusal_names_the_provider_id_an_alias_resolves_to(tmp_path, alias, resolved):
    r, _ = launch(tmp_path, graph(router=alias, allow=("haiku",)), off=OFF)
    assert f"{alias} -> {resolved}" in r.stdout + r.stderr
