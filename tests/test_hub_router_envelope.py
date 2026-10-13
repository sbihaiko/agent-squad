"""GitHub issue #95: the models panel names the router's dispatch envelope, and where it lives.

The panel listed the three role chains of models.json. The envelope that governs what the router may ask a child for
lives in the run graph instead, so a reader who cut a model from a chain still saw the router ask for it: the envelope
allowed `sonnet` and `opus`, the router named `opus`, and a child ran `opus high` for a quarter hour while the chains
of the same machine named no Anthropic model at all.

This guard reads the two functions the hub ships (`_graph_doc`, `envelope_of`) and the panel's render call, rather
than a copy of the rule written again here. The functions are sliced out of the file with `ast`, so a test never
starts the hub: its module body needs a profile, and a test that needs one cannot run on the runner.
"""
import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
LAUNCH = ROOT / "contrib" / "squad-goal" / "scripts" / "launch.py"
SEED = ROOT / "docs" / "design" / "router.graph.json"
# The ids the CLI resolves to an Anthropic leg: the local proxy serves every other id the chains name.
ANTHROPIC = ("sonnet", "opus", "haiku", "fable")


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


def test_the_project_workflow_file_wins_over_the_seed(tmp_path, monkeypatch):
    ns = _hub_functions("_graph_doc", "envelope_of")
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "dynamic.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    _pin(monkeypatch, tmp_path)
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
