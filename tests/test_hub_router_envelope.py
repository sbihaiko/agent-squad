"""GitHub issue #95: the router's models are configured in the panel, and the panel writes them where they live.

The panel listed the three role chains of models.json. The envelope that governs what the router may ask a child for
lives in the run graph instead, so a reader who cut a model from a chain still saw the router ask for it: the envelope
allowed `sonnet` and `opus`, the router named `opus`, and a child ran `opus high` for a quarter hour while the chains
of the same machine named no Anthropic model at all.

The router now has a queue of its own in the same panel, edited the way Vision is. The queue is graph data (ADR-0106):
the reading comes from the graph, the write goes to the graph and restamps the record it ships beside.

This guard reads the functions the hub ships, sliced out of the file with `ast`, rather than a copy of the rule. It
never imports the module: the module body needs a profile, and a test that needs one cannot run on the runner.
"""
import ast
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
# The ids the CLI resolves to an Anthropic leg: the local proxy serves every other id the chains name.
ANTHROPIC = ("sonnet", "opus", "haiku", "fable")
# `bare_id` strips a `claude:` prefix; the graph never carries one, so the identity is enough here.
STUBS = {"bare_id": lambda m: m, "subprocess": None, "sys": None}

def _envelope_module():
    """The one envelope source (#436), loaded by path: it is a pure module, and hub.py's sliced functions call it."""
    spec = importlib.util.spec_from_file_location("router_envelope", ROOT / "contrib" / "squad-goal" / "scripts" / "router_envelope.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _hub(*names, checkout="", stubs=None):
    """The named functions of hub.py, compiled alone, so importing the module never starts a server."""
    tree = ast.parse(HUB.read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in keep} == set(names), f"hub.py no longer ships {names}"
    ns = {"json": json, "os": __import__("os"), "pathlib": pathlib, "CHECKOUT": checkout, "router_envelope": _envelope_module(),
          **{**STUBS, "subprocess": subprocess, "sys": sys, **(stubs or {})}}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(HUB), "exec"), ns)
    return ns


def _envelope_ns(checkout):
    return _hub("_graph_path", "_graph_doc", "envelope_of", "router_get", checkout=checkout)


def _doc(envelope):
    return {"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop"}, {"name": "route", "kind": "dispatch", **envelope}]}


def test_the_envelope_is_read_from_the_run_graph_the_loop_starts(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc(
        {"allow": {"models": ["a-1"], "efforts": ["low"]}, "max_children": 3, "max_parallel": 2, "max_depth": 1})))
    got = ns["envelope_of"]()
    assert got["node"] == "route" and got["models"] == ["a-1"] and got["efforts"] == ["low"]
    assert got["max_children"] == 3 and got["max_parallel"] == 2 and got["max_depth"] == 1


def test_the_project_workflow_file_wins_over_the_seed(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "dynamic.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    assert ns["envelope_of"]()["models"] == ["mine-1"]


def test_the_router_chain_is_the_envelope_in_order(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["a-1", "b-2"]}})))
    assert ns["router_get"]() == ["a-1", "b-2"]


def test_the_shipped_envelope_names_no_anthropic_model():
    """The rule behind the measured defect: the envelope is what the router is held inside, so it names no model the
    owner withheld. A chain entry alone is not enough: the router reads this list."""
    got = _envelope_ns(str(ROOT))["envelope_of"]()
    assert got["models"], "the shipped graph has no dispatch envelope"
    for m in got["models"]:
        assert not any(m == a or m.endswith("-" + a) for a in ANTHROPIC), f"{m} is an Anthropic id"
    assert "high" not in got["efforts"], "the engine default is medium; a child is never asked for high"


def test_the_panel_offers_the_router_as_an_editable_queue():
    """The owner configures the router's models the way he configures Vision (#95): rank, up, down, remove, add. The
    queue is written where it lives, so the panel carries the write and not only the reading."""
    src = HUB.read_text()
    assert "function mdlRouter()" in src and "mdl.router_models" in src, "the router is not rendered as a queue"
    assert "+mdlRouter()" in src, "the render never calls it"
    for control in ("data-rv", "data-rrm", "data-raddb"):
        assert control in src, f"the router queue has no {control} control"
    assert src.index("MODEL_ID.match") < src.index("router_set(rchain)"), "a save writes before it validates"


def test_no_attribute_the_handler_reads_carries_no_value():
    """`t.dataset.raddb` is the empty string when the attribute is written without one, and the empty string is falsy:
    the branch never runs and the button does nothing. Measured 2026-10-13: the router queue's Add was dead this way
    while the Vision chain's Add (which carries its chain id) worked."""
    src = HUB.read_text()
    for name in set(re.findall(r"t\.dataset\.(\w+)", src)):
        for tag in re.findall(r"<button[^>]*\bdata-" + name + r"\b[^>]*>", src):
            m = re.search(r"data-" + name + r'(?:\s*=\s*(?:"([^"]*)"|([^\s>]+)))?', tag)
            assert m and (m.group(1) or m.group(2)), f"the handler reads data-{name} but this button carries no value: {tag[:70]}"


def test_the_queue_says_it_is_graph_data():
    """ADR-0106: the reader must know the write goes to the run graph, not to models.json."""
    src = HUB.read_text()
    seg = src.split("The models the router may ask a child for", 1)
    assert len(seg) == 2, "the panel has no Router section"
    assert "run graph" in seg[1][:400], "the queue does not say where it lives"


def test_the_envelope_comes_back_on_a_save_and_is_never_stored():
    src = HUB.read_text()
    ro = src.split("READONLY = (", 1)[1].split(")", 1)[0]
    assert "envelope" in ro and "router_models" not in ro, "the envelope is read-only; the router chain is writable"


@pytest.mark.heavy
def test_the_stamp_script_reports_the_record_current():
    """The write path restamps through this script; a stale record breaks check_record.py, so the script is the seam."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "stamp_router_record.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"the shipped router record is stale: {r.stdout}{r.stderr}"


def test_writing_the_queue_goes_to_the_graph_and_restamps(tmp_path):
    """The write path: the envelope in the graph takes the new queue, and the record that ships beside it is restamped
    through the script the repo owns. The graph keeps its own formatting, so a save is a one-line change."""
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    graph = d / "router.graph.json"
    graph.write_text(json.dumps(_doc({"allow": {"models": ["a-1", "b-2"]}}), indent=2) + "\n")
    s = tmp_path / "scripts"; s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; print('restamped: test'); sys.exit(0)\n")
    ns = _hub("_graph_path", "_graph_doc", "router_set", checkout=str(tmp_path))
    assert ns["router_set"](["b-2", "c-3"]) == {}
    assert json.loads(graph.read_text())["nodes"][1]["allow"]["models"] == ["b-2", "c-3"]


def test_a_failed_restamp_is_reported_not_swallowed(tmp_path):
    """A graph written while its record stayed stale breaks check_record.py, so the caller is told."""
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["a-1"]}}), indent=2) + "\n")
    s = tmp_path / "scripts"; s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; print('boom'); sys.exit(1)\n")
    ns = _hub("_graph_path", "_graph_doc", "router_set", checkout=str(tmp_path))
    err = ns["router_set"](["a-1"])
    assert "could not be restamped" in err.get("err", ""), err
