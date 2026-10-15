"""The Coding chain and the router envelope are two lists, and a Coding model outside the envelope was dropped in silence.

The router only requests models in the dispatch node's `allow.models`; a request outside it is refused whole, so the
router picked an allowed id and the request's "Coding model:" line lost without a word. The loop now names the Coding
model the envelope blocks, the models panel marks it in the Coding chain, and a guard flags the shipped chain and the
shipped graph when they diverge. The comparison is one rule, `envelope_blocked`, shipped in hub.py (the panel) and
mirrored in launch.py (the loop), and a test holds the two copies to the same answers.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
ENVELOPE = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-deepseek-v4-flash", "claude-gpt-5.6-terra"]


def _fn(path, name):
    """One function of a script, compiled alone: importing hub.py or launch.py would start a server or a run."""
    tree = ast.parse(path.read_text())
    keep = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert keep, f"{path.name} no longer ships {name}"
    ns = {"ALIASES": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"}}
    exec(compile(ast.Module(body=keep[:1], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


def _launch(tmp_path, coding, envelope):
    s, co, st, bin_ = tmp_path / "s", tmp_path / "co", tmp_path / "st", tmp_path / "bin"
    for d in (s, st, bin_, co / "runs", co / "docs/squad/workflows", tmp_path / "squad"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / "launch.py", s / "launch.py")
    (s / "models.json").write_text(json.dumps({"coding_models": coding, "review_models": [], "vision_models": []}))
    (s / "hub.py").write_text("print('hub')\n")
    doc = {"schema_version": 1, "name": "g", "root": "main", "nodes": [
        {"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["route"]},
        {"name": "route", "kind": "dispatch", "entry": "router", "allow": {"models": envelope, "efforts": ["low"]}},
        {"name": "router", "kind": "agent", "model": envelope[0], "tools": ["Read"]}]}
    (co / "docs/squad/workflows/default.graph.json").write_text(json.dumps(doc))
    (bin_ / "caffeinate").write_text("#!/bin/bash\nmkdir -p runs/run-20261013-000000-aaaaaa\nexit 0\n")
    (bin_ / "uv").write_text("#!/bin/bash\necho uv-not-used\n")
    for p in (bin_ / "caffeinate", bin_ / "uv"):
        p.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text(f"Coding model: {coding[0]} (no [1m] suffix); fall back to {coding[-1]}.\n--- issue\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    e.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", SG_STATE=str(st), SG_CHECKOUT=str(co), SG_SQUAD=str(tmp_path / "squad"))
    return subprocess.run([sys.executable, "-I", str(s / "launch.py"), str(req)], cwd=co, env=e, capture_output=True, text=True, timeout=60)


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_names_a_first_coding_model_the_envelope_blocks(tmp_path):
    r = _launch(tmp_path, ["grok-4.7", "gemini-3.8-flash-medium"], ENVELOPE)
    assert "grok-4.7 is not in the router envelope" in r.stderr, r.stdout + r.stderr


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_stays_quiet_when_the_first_coding_model_fits_the_envelope(tmp_path):
    r = _launch(tmp_path, ["claude-sonnet-5-5", "grok-4.7"], ENVELOPE)
    assert "router envelope" not in r.stderr, r.stdout + r.stderr


def test_the_check_names_every_coding_model_missing_from_the_envelope():
    blocked = _fn(SCRIPTS / "hub.py", "envelope_blocked")
    assert blocked(["grok-4.7", "claude-sonnet-5-5", "gemini-3.8-flash-medium"], ENVELOPE) == ["grok-4.7", "gemini-3.8-flash-medium"]
    assert blocked(["sonnet", "claude:claude-opus-5-5"], ENVELOPE) == []   # an alias and a review-path prefix name the same model
    assert blocked(["grok-4.7"], []) == []                                  # no envelope read, nothing to compare


def test_the_panel_and_the_loop_apply_one_rule():
    cases = [(["grok-4.7", "sonnet", "claude:claude-opus-5-5", "x"], ENVELOPE), (["opus"], ["claude-opus-5-5"]), ([], ENVELOPE)]
    hub, launch = _fn(SCRIPTS / "hub.py", "envelope_blocked"), _fn(SCRIPTS / "launch.py", "envelope_blocked")
    for chain, env in cases:
        assert hub(chain, env) == launch(chain, env)


def test_the_panel_marks_a_blocked_row_and_never_stores_the_mark():
    src = (SCRIPTS / "hub.py").read_text()
    assert "blocked by envelope" in src
    assert '"blocked"' in src.split("READONLY =")[1].split("\n")[0]


def test_the_shipped_coding_chain_fits_the_shipped_envelope():
    chain = json.loads((SCRIPTS / "models.json").read_text())["coding_models"]
    graph = json.loads((ROOT / "docs/design/router.graph.json").read_text())
    env = next(n["allow"]["models"] for n in graph["nodes"] if n.get("kind") == "dispatch")
    assert _fn(SCRIPTS / "hub.py", "envelope_blocked")(chain, env) == []
