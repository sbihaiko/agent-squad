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
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
SEED = ROOT / "docs" / "design" / "router.graph.json"
# The ids the CLI resolves to an Anthropic leg: the local proxy serves every other id the chains name.
ANTHROPIC = ("sonnet", "opus", "haiku", "fable")


def _hub_functions(*names, checkout=""):
    """The named functions of hub.py, compiled alone, so importing the module never starts a server."""
    tree = ast.parse(HUB.read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert [n.name for n in keep] == list(names), "hub.py no longer ships these functions"
    ns = {"json": json, "os": __import__("os"), "pathlib": pathlib, "CHECKOUT": checkout}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(HUB), "exec"), ns)
    return ns


def _doc(envelope):
    return {"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop"}, {"name": "route", "kind": "dispatch", **envelope}]}


def test_the_envelope_is_read_from_the_run_graph_the_loop_starts(tmp_path):
    ns = _hub_functions("_graph_doc", "envelope_of", checkout=str(tmp_path))
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(_doc(
        {"allow": {"models": ["a-1"], "efforts": ["low"]}, "max_children": 3, "max_parallel": 2, "max_depth": 1})))
    got = ns["envelope_of"]()
    assert got["node"] == "route" and got["models"] == ["a-1"] and got["efforts"] == ["low"]
    assert got["max_children"] == 3 and got["max_parallel"] == 2 and got["max_depth"] == 1


def test_the_project_workflow_file_wins_over_the_seed(tmp_path):
    ns = _hub_functions("_graph_doc", "envelope_of", checkout=str(tmp_path))
    w = tmp_path / "docs" / "squad" / "workflows"; w.mkdir(parents=True)
    (w / "dynamic.graph.json").write_text(json.dumps(_doc({"allow": {"models": ["mine-1"]}})))
    assert ns["envelope_of"]()["models"] == ["mine-1"]


def test_the_shipped_envelope_names_no_anthropic_model():
    """The rule behind the measured defect: the envelope is what the router is held inside, so it names no model the
    owner withheld. A chain entry alone is not enough: the router reads this list."""
    ns = _hub_functions("_graph_doc", "envelope_of", checkout=str(ROOT))
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
