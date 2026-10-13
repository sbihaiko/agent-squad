"""GitHub issue #95: the models panel names the router's dispatch envelope, and where it lives, and the panel writes it.

The panel listed the three role chains of models.json. The envelope that governs what the router may ask a child for
lives in the run graph instead, so a reader who cut a model from a chain still saw the router ask for it: the envelope
allowed `sonnet` and `opus`, the router named `opus`, and a child ran `opus high` for a quarter hour while the chains
of the same machine named no Anthropic model at all.

The router now has a queue of its own in the same panel, edited the way Vision is. The queue is graph data (ADR-0106):
the reading comes from the graph, the write goes to the graph and restamps the record it ships beside.

This guard reads the functions the hub ships (`_graph_doc`, `envelope_of`, `router_get`, `router_set`) and the panel's
render call, sliced out of the file with `ast`, rather than a copy of the rule written again here. It never imports the
module: the module body needs a profile, and a test that needs one cannot run on the runner.
"""
import ast
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
LAUNCH = ROOT / "contrib" / "squad-goal" / "scripts" / "launch.py"
SEED = ROOT / "docs" / "design" / "router.graph.json"
# The ids the CLI resolves to an Anthropic leg: the local proxy serves every other id the chains name.
ANTHROPIC = ("sonnet", "opus", "haiku", "fable")
# `bare_id` strips a `claude:` prefix; the graph never carries one, so the identity is enough here.
STUBS = {"bare_id": lambda m: m, "subprocess": None, "sys": None}


def _functions(path, names, **globals_):
    """The named functions of a script, compiled alone, so importing the module never starts a server or a run."""
    tree = ast.parse(path.read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert [n.name for n in keep] == list(names), f"{path.name} no longer ships these functions"
    ns = {"json": json, "os": __import__("os"), "pathlib": pathlib, **globals_}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), ns)
    return ns


def _hub_functions(*names):
    return _functions(HUB, names)


def _pin(monkeypatch, base, workflow=None):
    """The graph an envelope comes from is resolved from the environment, so a test that pins the answer names every
    variable that resolution reads: an ambient SG_SQUAD or SG_WORKFLOW would otherwise decide which graph is read."""
    monkeypatch.setenv("SG_CHECKOUT", str(base))
    monkeypatch.setenv("SG_SQUAD", str(base))
    if workflow is None:
        monkeypatch.delenv("SG_WORKFLOW", raising=False)
    else:
        monkeypatch.setenv("SG_WORKFLOW", workflow)


def _hub(*names, checkout="", stubs=None):
    """The named functions of hub.py, compiled alone, so importing the module never starts a server."""
    tree = ast.parse(HUB.read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in keep} == set(names), f"hub.py no longer ships {names}"
    ns = {"json": json, "os": __import__("os"), "pathlib": pathlib, "CHECKOUT": checkout,
          **{**STUBS, "subprocess": subprocess, "sys": sys, **(stubs or {})}}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(HUB), "exec"), ns)
    return ns


def _envelope_ns(checkout):
    return _hub("_graph_path", "_graph_doc", "envelope_of", "router_get", checkout=checkout)


def _doc(envelope):
    return {"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop"}, {"name": "route", "kind": "dispatch", **envelope}]}


def test_the_envelope_is_read_from_the_run_graph_the_loop_starts(tmp_path, monkeypatch):
    ns = _hub_functions("_graph_doc", "envelope_of")
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc(
        {"allow": {"models": ["a-1"], "efforts": ["low"]}, "max_children": 3, "max_parallel": 2, "max_depth": 1})))
    _pin(monkeypatch, tmp_path)
    got = ns["envelope_of"]()
    assert got["node"] == "route" and got["models"] == ["a-1"] and got["efforts"] == ["low"]
    assert got["max_children"] == 3 and got["max_parallel"] == 2 and got["max_depth"] == 1


def test_the_envelope_is_read_from_the_run_graph_the_loop_starts_from_the_hub_seam(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc(
        {"allow": {"models": ["a-1"], "efforts": ["low"]}, "max_children": 3, "max_parallel": 2, "max_depth": 1})))
    got = ns["envelope_of"]()
    assert got["node"] == "route" and got["models"] == ["a-1"] and got["efforts"] == ["low"]
    assert got["max_children"] == 3 and got["max_parallel"] == 2 and got["max_depth"] == 1


def test_the_project_workflow_file_wins_over_the_seed(tmp_path, monkeypatch):
    ns = _hub_functions("_graph_doc", "envelope_of")
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "dynamic.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    _pin(monkeypatch, tmp_path)
    assert ns["envelope_of"]()["models"] == ["mine-1"]


def test_the_project_workflow_file_wins_over_the_seed_from_the_hub_seam(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "dynamic.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    assert ns["envelope_of"]()["models"] == ["mine-1"]


def test_the_envelope_follows_the_workflow_the_run_starts(tmp_path, monkeypatch):
    """The graph a run starts is the file SG_WORKFLOW names, and launch.py reads that file for the run's own model gate. A
    panel that reads another graph shows an envelope no child of the run is held inside (the measured defect: the router
    named a model no chain allowed, because the reader was shown the chains and not the graph the run obeyed)."""
    ns = _hub_functions("_graph_doc", "envelope_of")
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "mine.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    _pin(monkeypatch, tmp_path, "mine")
    assert ns["envelope_of"]()["models"] == ["mine-1"]


def test_the_panel_reads_the_graph_launch_py_resolves(tmp_path, monkeypatch):
    """One file, two readers: the envelope the panel shows is the graph the run obeys only if this resolution and
    launch.py's `_graph_path` land together on every input that matters. That function is read here, not the rule again."""
    base = tmp_path / "checkout"; plugin = tmp_path / "plugin"
    w = base / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (plugin / "docs" / "workflows").mkdir(parents=True); (plugin / "docs" / "design").mkdir(parents=True)
    (w / "default.graph.json").write_text(json.dumps({"name": "owner-default", "nodes": []}))
    (w / "mine.graph.json").write_text(json.dumps({"name": "mine", "nodes": []}))
    (plugin / "docs" / "workflows" / "shipped1.graph.json").write_text(json.dumps({"name": "shipped1", "nodes": []}))
    (plugin / "docs" / "design" / "router.graph.json").write_text(json.dumps({"name": "seed", "nodes": []}))
    hub = _hub_functions("_graph_doc")
    monkeypatch.setenv("SG_CHECKOUT", str(base)); monkeypatch.setenv("SG_SQUAD", str(plugin))
    for wf in (None, "default", "mine", "shipped1"):
        run = _functions(LAUNCH, ("_graph_path",), cwd=base, squad=str(plugin))["_graph_path"]
        if wf is None:
            monkeypatch.delenv("SG_WORKFLOW", raising=False)
        else:
            monkeypatch.setenv("SG_WORKFLOW", wf)
        p = run(wf)
        assert p.is_file(), f"launch.py resolves no graph file for {wf!r}"
        assert hub["_graph_doc"]() == json.loads(p.read_text()), f"the panel reads another graph than launch.py for {wf!r}"
    # no project workflow file at all: both read the graph the plugin ships, so the panel still names an envelope
    empty = tmp_path / "empty"; (empty / "docs").mkdir(parents=True)
    run = _functions(LAUNCH, ("_graph_path",), cwd=empty, squad=str(plugin))["_graph_path"]
    monkeypatch.setenv("SG_CHECKOUT", str(empty)); monkeypatch.setenv("SG_SQUAD", str(plugin))
    monkeypatch.delenv("SG_WORKFLOW", raising=False)
    p = run(None)
    assert p.is_file() and hub["_graph_doc"]() == json.loads(p.read_text()), "the panel loses the seed graph"


def test_the_shipped_envelope_names_no_anthropic_model(tmp_path, monkeypatch):
    """The rule behind the measured defect: the envelope is what the router is held inside, so it names no model the
    owner withheld. A chain entry alone is not enough: the router reads this list."""
    ns = _hub_functions("_graph_doc", "envelope_of")
    _pin(monkeypatch, ROOT)
    got = ns["envelope_of"]()
    assert got["models"], "the shipped graph has no dispatch envelope"
    for m in got["models"]:
        assert not any(m == a or m.endswith("-" + a) for a in ANTHROPIC), f"{m} is an Anthropic id"
    assert "high" not in got["efforts"], "the engine default is medium; a child is never asked for high"


def test_the_shipped_envelope_names_no_anthropic_model_from_the_hub_seam():
    """The rule behind the measured defect: the envelope is what the router is held inside, so it names no model the
    owner withheld. A chain entry alone is not enough: the router reads this list."""
    got = _envelope_ns(str(ROOT))["envelope_of"]()
    assert got["models"], "the shipped graph has no dispatch envelope"
    for m in got["models"]:
        assert not any(m == a or m.endswith("-" + a) for a in ANTHROPIC), f"{m} is an Anthropic id"
    assert "high" not in got["efforts"], "the engine default is medium; a child is never asked for high"


def test_the_panel_renders_the_router_section():
    """The pane shows the envelope beside the chains, and says where it lives: the reader who cuts a chain entry must
    see the list that still governs the router (ADR-0106: the envelope is graph data)."""
    src = HUB.read_text()
    assert "envelope" in src.split("READONLY = (", 1)[1].split(")", 1)[0], "the envelope comes back on a save"
    assert 'mdl.envelope' in src, "the panel never reads the envelope"
    seg = src.split("Router</div>", 1)
    assert len(seg) == 2, "the panel has no Router section"
    assert "graph data" in seg[1][:900], "the section does not say the envelope lives in the run graph"
    assert "e.tools" in seg[1][:900], "the section names every part of the envelope but the tools it allows"


def test_the_router_chain_is_the_envelope_in_order(tmp_path):
    ns = _envelope_ns(str(tmp_path))
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["a-1", "b-2"]}})))
    assert ns["router_get"]() == ["a-1", "b-2"]


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
