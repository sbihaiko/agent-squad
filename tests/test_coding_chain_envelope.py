"""The Coding chain and a legacy graph's `allow.models`, and a Coding model outside that list was dropped in silence.

The router only requests models in the dispatch node's `allow.models`; a request outside it is refused whole, so the
router picked an allowed id and the request's "Coding model:" line lost without a word. The loop names the Coding model
the envelope blocks, and a guard flags the shipped chain and the shipped graph when they diverge. The comparison is one
rule, `envelope_blocked`, shipped once, in `router_envelope.py`; the loop (launch.py) imports it for a legacy graph.

SPEC 750 retires the list: the shipped envelope names kinds, the router chain is `chains.router` in models.json, and the
panel marks nothing and appends nothing. The badge, the auto-append and the hub's own envelope read are gone; the
launcher's warning stays for a graph that still carries a model list, and is silent for the shipped one.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.gitrepo import with_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"))
import router_envelope  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
ENVELOPE = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-deepseek-v4-flash", "claude-gpt-5.6-terra"]


def _fn(path, name, **ns_extra):
    """One function of a script, compiled alone: importing hub.py or launch.py would start a server or a run."""
    tree = ast.parse(path.read_text())
    keep = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert keep, f"{path.name} no longer ships {name}"
    ns = dict(ns_extra)
    exec(compile(ast.Module(body=keep[:1], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


def _launch(tmp_path, coding, envelope, shared=None, hours=None):
    s, co, st, bin_ = tmp_path / "s", tmp_path / "co", tmp_path / "st", tmp_path / "bin"
    for d in (s, st, bin_, co / "runs", co / "docs/squad/workflows", tmp_path / "squad"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / "launch.py", s / "launch.py")
    shutil.copy(SCRIPTS / "router_envelope.py", s / "router_envelope.py")
    for name in ("provkey.py", "modelreg.py", "registry.json"):
        shutil.copy(SCRIPTS / name, s / name)
    (s / "models.json").write_text(json.dumps({"coding_models": coding, "review_models": [], "vision_models": [], "hours": {"coding_models": hours or {}}}))
    (s / "hub.py").write_text("print('hub')\n")
    doc = {"schema_version": 1, "name": "g", "root": "main", "nodes": [
        {"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["route"]},
        {"name": "route", "kind": "dispatch", "entry": "router", "allow": {"models": envelope, "efforts": ["low"]}},
        {"name": "router", "kind": "agent", "model": envelope[0], "tools": ["Read"]}]}
    (co / "docs/squad/workflows/default.graph.json").write_text(json.dumps(doc))
    (bin_ / "caffeinate").write_text(f"#!/bin/bash\nmkdir -p runs/run-20261013-000000-aaaaaa\necho \"$@\" > {tmp_path / 'argv'}\nexit 0\n")
    (bin_ / "uv").write_text("#!/bin/bash\necho uv-not-used\n")
    for p in (bin_ / "caffeinate", bin_ / "uv"):
        p.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text(f"Coding model: {coding[0]} (no [1m] suffix); fall back to {coding[-1]}.\n--- issue\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    e.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", SG_STATE=str(st), SG_CHECKOUT=str(co), SG_SQUAD=str(tmp_path / "squad"), **({"SG_SHARED": str(shared)} if shared else {}))
    with_origin(co)   # launch.py fetches origin/main before a run (#580)
    return subprocess.run([sys.executable, "-I", str(s / "launch.py"), str(req)], cwd=co, env=e, capture_output=True, text=True, timeout=60)


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_names_a_first_coding_model_the_envelope_blocks(tmp_path):
    r = _launch(tmp_path, ["grok-4.7", "gemini-3.8-flash-medium"], ENVELOPE)
    assert "grok-4.7 is not in the router envelope" in r.stderr, r.stdout + r.stderr
    assert "allow.models" in r.stderr, f"the warning offers no remedy that still exists: {r.stderr}"


def test_the_legacy_warning_offers_a_remedy_the_panel_still_has():
    """SPEC 750: the hub no longer writes the graph's dispatch envelope and has no Router queue, so the warning on a
    legacy graph must name the surface that still changes `allow.models` — the graph itself (ADR-0113)."""
    src = (SCRIPTS / "launch.py").read_text()
    assert "Router queue" not in src, "the warning offers a control the panel no longer has"
    assert "allow.models" in src, "the warning names no surface that still holds the envelope"


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_stays_quiet_when_every_coding_model_fits_the_envelope(tmp_path):
    r = _launch(tmp_path, ["claude-sonnet-5-5", "claude-opus-5-5"], ENVELOPE)
    assert "router envelope" not in r.stderr, r.stdout + r.stderr


def test_the_check_names_every_coding_model_missing_from_the_envelope():
    blocked = router_envelope.envelope_blocked
    assert blocked(["grok-4.7", "claude-sonnet-5-5", "gemini-3.8-flash-medium"], ENVELOPE) == ["grok-4.7", "gemini-3.8-flash-medium"]
    assert blocked(["sonnet", "claude:claude-opus-5-5"], ENVELOPE) == []   # an alias and a review-path prefix name the same model
    assert blocked(["grok-4.7"], []) == []                                  # no envelope read, nothing to compare


def test_the_envelope_has_one_copy_and_only_the_legacy_launcher_imports_it():
    """SPEC 750: the hub reads the router chain from models.json, so only the launcher's legacy-graph warning reads
    `allow.models`. It still does that through the one shared module, never through a copy of the rule."""
    launch = (SCRIPTS / "launch.py").read_text()
    hub = (SCRIPTS / "hub.py").read_text()
    for name, src in (("launch.py", launch), ("hub.py", hub)):
        assert "def envelope_blocked" not in src, f"{name} keeps its own copy of the rule"
    assert "router_envelope" in launch and "router_envelope" not in hub


def _graph(models):
    return {"nodes": [{"name": "r", "kind": "dispatch", "allow": {"models": models}}, {"name": "a", "kind": "agent"}]}


def test_the_hub_reads_the_router_chain_from_models_json_not_the_graph():
    """SPEC 750: the panel's router chain is `chains.router`. The graph's allow.models is the launcher's legacy path."""
    doc = {"schema_version": 2, "chains": {"router": [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}]}}
    ns = {"models_doc": lambda: hub_v1_view(doc), "bare_id": lambda m: m.removeprefix("claude:"),
          "v1_view": hub_v1_view, "usable": lambda ids, chain: ids, "ROUTER_CHAIN": "router_models"}
    assert _fn(SCRIPTS / "hub.py", "router_get", **ns)(doc) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    graph = _graph(["claude-opus-5-5"])
    assert router_envelope.envelope_models(graph) == ["claude-opus-5-5"]   # the legacy path still reads the graph
    assert router_envelope.envelope_models({}) == []


def hub_v1_view(doc):
    """The hub's own reader, sliced out of the file: `chains` under the v1 keys the page draws."""
    import sys
    sys.path.insert(0, str(SCRIPTS))
    ns = {"modelreg": _modelreg(), "CHAINS": ("coding_models", "review_models", "vision_models", "router_models"),
          "usable": lambda ids, chain: ids}
    return _fn(SCRIPTS / "hub.py", "v1_view", **ns)(doc)


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_names_each_eligible_model_the_envelope_blocks_a_fallback_too(tmp_path):
    r = _launch(tmp_path, ["grok-4.7", "claude-sonnet-5-5", "gemini-3.8-flash-medium"], ENVELOPE)
    assert "grok-4.7 is not in the router envelope" in r.stderr, r.stdout + r.stderr
    assert "gemini-3.8-flash-medium is not in the router envelope" in r.stderr, r.stdout + r.stderr
    assert "claude-sonnet-5-5 is not in the router envelope" not in r.stderr


def test_the_panel_marks_no_blocked_row_and_stores_no_mark():
    """SPEC 750 retires the badge: the envelope names a kind, so no coding row can be blocked by a model list."""
    src = (SCRIPTS / "hub.py").read_text()
    assert "blocked by envelope" not in src
    assert '"blocked"' not in src.split("READONLY =")[1].split("\n")[0]


def test_the_shipped_envelope_names_no_model_so_no_coding_entry_is_blocked():
    """SPEC 750: the owner's chains are the one list. The envelope names kinds, so no coding entry is refused for being
    outside a model list, and the hub's auto-append and its "blocked by envelope" badge have nothing to hold."""
    graph = json.loads((ROOT / "docs/design/router.graph.json").read_text())
    allow = next(n["allow"] for n in graph["nodes"] if n.get("kind") == "dispatch")
    assert "models" not in allow and allow["kinds"] == ["coding", "review", "vision"]


def _shared(tmp_path, **markers):
    d = tmp_path / "shared"
    d.mkdir()
    for name, epoch in markers.items():
        (d / name.replace("_", "-")).write_text(str(epoch))
    return d


def _modelreg():
    import importlib.util
    spec = importlib.util.spec_from_file_location("modelreg_shipped", SCRIPTS / "modelreg.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_closed_marker_names_the_graph_models_of_its_provider_as_spent(tmp_path):
    spent = _fn(SCRIPTS / "launch.py", "_spent_models", time=__import__("time"), pathlib=__import__("pathlib"), ALIASES={}, SPENT_MARKERS=(("gpt-", "codex-limited"), ("gemini-", "agy-limited")),
                provkey=__import__("types").SimpleNamespace(closed=lambda m: False),   # no keyed model here (#482)
                modelreg=_modelreg(),
                _run_models=lambda doc: [("router", "claude-gpt-5.5", True), ("route", "claude-opus-5-5", True), ("route", "claude-gemini-3.8-flash", False), ("route", "claude-gpt-5.6-terra", False)])
    sh = _shared(tmp_path, codex_limited=2000, agy_limited=500)
    assert spent({}, sh, now=1000) == ["claude-gpt-5.5", "claude-gpt-5.6-terra"]   # codex is closed until 2000, agy's epoch has passed
    assert spent({}, sh, now=3000) == []
    assert spent({}, None, now=1000) == []                                           # no shared dir: nothing is closed


def test_a_spent_quota_window_names_the_graph_models_of_its_subscription_as_spent(tmp_path):    # #678
    """The markers were the only source, so a child could be given grok while its weekly window was spent. The resolver's
    quota rule names it too; the chain leaves a spent id out and keeps the order of the rest."""
    spent = _fn(SCRIPTS / "launch.py", "_spent_models", time=__import__("time"), pathlib=__import__("pathlib"), ALIASES={}, SPENT_MARKERS=(),
                provkey=__import__("types").SimpleNamespace(closed=lambda m: False), modelreg=_modelreg(),
                _run_models=lambda doc: [("route", "claude-opus-5-5", True), ("route", "grok-4.5", False), ("route", "claude-gpt-5.6-terra", False)])
    sh = _shared(tmp_path)
    (sh / "hub-quota.json").write_text(json.dumps({"grok": {"windows": [{"label": "weekly", "pct": 100, "resets": 5000}], "asof": 900}}))
    assert spent({}, sh, now=1000) == ["grok-4.5"]
    assert spent({}, sh, now=6000) == []


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_passes_the_spent_models_to_the_run(tmp_path):
    sh = _shared(tmp_path, codex_limited=4102444800)
    r = _launch(tmp_path, ["claude-sonnet-5-5"], ENVELOPE, shared=sh)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--spent-models claude-gpt-5.6-terra " in (tmp_path / "argv").read_text()


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_passes_no_spent_models_flag_without_a_closed_marker(tmp_path):
    r = _launch(tmp_path, ["claude-sonnet-5-5"], ENVELOPE, shared=_shared(tmp_path, codex_limited=1))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--spent-models" not in (tmp_path / "argv").read_text()


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_passes_the_coding_hours_to_the_run(tmp_path):
    # launch.py reads the local hour, so the window opens at this hour and spans two: an hour that turns during the run
    # stays inside it, and the test never depends on the wall clock (the test-review.sh pattern).
    h = time.localtime().tm_hour
    w = f"{h:02d}-{(h + 2) % 24:02d}"
    r = _launch(tmp_path, ["claude-sonnet-5-5"], ENVELOPE, hours={"claude-sonnet-5-5": w})
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"--model-hours claude-sonnet-5-5={w} " in (tmp_path / "argv").read_text()


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_passes_the_coding_order_to_the_run(tmp_path):
    coding = ["claude-sonnet-5-5", "claude-gpt-5.6-terra"]   # a coding order that differs from the envelope's
    r = _launch(tmp_path, coding, ENVELOPE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"--model-order {','.join(coding)} " in (tmp_path / "argv").read_text()   # the in-run fallback follows it (#617)


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_the_loop_refuses_a_run_whose_envelope_blocks_the_first_coding_model(tmp_path):    # #778
    """The router cannot request a coding model outside `allow.models`, so it picks another one in silence. The loop
    refuses the start by name instead, and starts no run."""
    r = _launch(tmp_path, ["grok-4.7", "claude-sonnet-5-5"], ENVELOPE)
    assert r.returncode != 0, r.stdout + r.stderr
    assert not (tmp_path / "argv").exists(), "a run started with a coding model the router cannot request"
    assert "grok-4.7 is not in the router envelope" in r.stderr and "SG_ALLOW_MODEL" in r.stderr, r.stderr


@pytest.mark.heavy   # launch.py waits for the run folder, over a second
def test_a_fallback_outside_the_envelope_only_warns(tmp_path):    # #778
    r = _launch(tmp_path, ["claude-sonnet-5-5", "grok-4.7"], ENVELOPE)
    assert "grok-4.7 is not in the router envelope" in r.stderr, r.stderr
    assert (tmp_path / "argv").exists(), r.stdout + r.stderr
