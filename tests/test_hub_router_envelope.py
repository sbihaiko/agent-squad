"""GitHub issue #95 and SPEC 750: the router's own chain lives in models.json with the three role chains.

The panel once drew a bespoke Router queue and wrote it into the run graph's dispatch envelope. SPEC 750 moved the
router's models to `chains.router`: a child request names a kind, and the router agent runs on the router chain. The
panel now draws the router row like any other chain, so a Save edits models.json and never the graph or its record.

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
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
STUBS = {"bare_id": lambda m: m.removeprefix("claude:")}


def _module(name, path):
    """A pure script of the repo, loaded by path: hub.py itself needs a profile, these do not."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _envelope_module():
    """The one envelope source (#436): still shipped for a legacy graph's launcher warning."""
    return _module("router_envelope", SCRIPTS / "router_envelope.py")


def test_the_envelope_keys_through_the_one_resolver():
    """SPEC 505 ticket 5: the envelope's key is the registry id, and its context suffix is read apart from the alias,
    so `sonnet[1m]` and `claude-sonnet-5-5[1m]` name the same model."""
    env = _envelope_module()
    assert env.envelope_blocked(["sonnet[1m]", "opus"], ["claude-sonnet-5-5[1m]", "claude:opus"]) == []


def test_the_envelope_module_carries_the_seam_header():
    """The resolver seam enforces its header on a caller, so this file must carry it (SPEC 505 ticket 5)."""
    text = (SCRIPTS / "router_envelope.py").read_text()
    assert any("modelreg: seam-clean" in line for line in text.splitlines()[:5])


def _hub(*names, stubs=None, **extra):
    """The named functions of hub.py, compiled alone, so importing the module never starts a server."""
    tree = ast.parse(HUB.read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in keep} == set(names), f"hub.py no longer ships {names}"
    ns = {"json": json, "os": __import__("os"), "pathlib": pathlib, "modelreg": _module("modelreg_shipped", SCRIPTS / "modelreg.py"),
          **{**STUBS, **(stubs or {})}, **extra}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(HUB), "exec"), ns)
    return ns


def test_the_router_chain_is_models_json_chains_router():
    """SPEC 750: the router's chain is `chains.router`, read through the same resolver seam as the three role chains."""
    ns = _hub("usable", "v1_view", "router_get", CHAINS=("coding_models", "review_models", "vision_models", "router_models"), ROUTER_CHAIN="router_models")
    doc = {"schema_version": 2, "chains": {"router": [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"},
                                                      {"id": "claude:claude-opus-5-5"}]}}
    assert ns["router_get"](doc) == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert ns["router_get"]({"schema_version": 2, "chains": {}}) == []


def test_the_router_chain_drops_an_entry_the_router_cannot_run():
    """The panel shows the entries the chain can run (usable, ADR-0147): a gateway-less id is not drawn."""
    ns = _hub("usable", "v1_view", "router_get", CHAINS=("coding_models", "review_models", "vision_models", "router_models"), ROUTER_CHAIN="router_models")
    doc = {"schema_version": 2, "chains": {"router": [{"id": "claude-opus-5-5", "route": "gateway"},
                                                      {"id": "gemini-3.8-flash", "route": "cli"},
                                                      {"id": "gpt-9-nova", "route": "gateway"}]}}
    # gemini has no gateway route, so the router chain cannot run it; an unknown id stays as unregistered
    assert ns["router_get"](doc) == ["claude-opus-5-5", "gpt-9-nova"]


def test_the_shipped_router_chain_names_models_the_router_can_run():
    """SPEC 750 and ADR-0113: `chains.router` is the only place the router agent's model is chosen, so every shipped
    entry must be one the router can run. The test pins the chain, never the graph's router node: the node's own
    `model` is not read, so dropping it changes nothing a run does."""
    models = json.loads((SCRIPTS / "models.json").read_text())
    router = [e.get("raw") or e["id"] for e in models["chains"]["router"]]
    assert router, "the shipped router chain is empty, so the router agent has no model of its own"
    reg = _module("modelreg_shipped_router", SCRIPTS / "modelreg.py")
    for m in router:
        ok, why = reg.compatible(m, "router")
        assert ok, f"the router chain holds {m}, which the router cannot run: {why}"


def test_the_shipped_envelope_holds_kinds_and_no_model_list():
    """SPEC 750: the owner edits the four chains and nothing else. The envelope names the kinds a child may be, so a
    model the owner withheld is withheld in the one chain he edits, never in a second list that drifts."""
    graph = json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text())
    got = next(n["allow"] for n in graph["nodes"] if n.get("kind") == "dispatch")
    assert "models" not in got, "the shipped envelope still names a model list"
    assert got["kinds"] == ["coding", "review", "vision"]
    assert "high" not in got["efforts"], "the engine default is medium; a child is never asked for high"


def test_the_panel_draws_the_router_as_the_fourth_chain():
    """The owner configures the router's models the way he configures Vision: rank, up, down, switch, remove, add."""
    src = HUB.read_text()
    labels = re.search(r"const MLAB=\{(.*?)\},MDESC=", src).group(1)
    assert "router_models" in labels, "the Router card is not drawn with the three role chains"
    assert "function mdlRouter()" not in src and "+mdlRouter()" not in src, "the bespoke queue survives"
    for control in ("data-mv", "data-rm", "data-sw", "data-addb"):
        assert f"{control}=" in src, f"the router row has no {control} control"


def test_the_panel_no_longer_reads_or_writes_the_run_graph():
    """SPEC 750: the router chain is one models.json chain, so the hub keeps no graph reader and no graph writer."""
    src = HUB.read_text()
    for gone in ("router_set", "envelope_of", "_graph_doc", "_graph_path", "router_envelope"):
        assert gone not in src, f"the hub still carries {gone}"


def test_no_attribute_the_handler_reads_carries_no_value():
    """`t.dataset.addb` is the empty string when the attribute is written without one, and the empty string is falsy:
    the branch never runs and the button does nothing. Measured 2026-10-13: the router queue's Add was dead this way
    while the Vision chain's Add (which carries its chain id) worked."""
    src = HUB.read_text()
    for name in set(re.findall(r"t\.dataset\.(\w+)", src)):
        for tag in re.findall(r"<button[^>]*\bdata-" + name + r"\b[^>]*>", src):
            m = re.search(r"data-" + name + r'(?:\s*=\s*(?:"([^"]*)"|([^\s>]+)))?', tag)
            assert m and (m.group(1) or m.group(2)), f"the handler reads data-{name} but this button carries no value: {tag[:70]}"


def test_the_panel_has_no_envelope_badge_and_no_envelope_field():
    """SPEC 750: nothing can be "blocked by envelope" any more, because the envelope names a kind and the chain names
    the model. The badge and the field that fed it are gone together."""
    src = HUB.read_text()
    assert "blocked by envelope" not in src
    ro = src.split("READONLY = (", 1)[1].split(")", 1)[0]
    assert "envelope" not in ro and "blocked" not in ro
    assert "router_models" not in ro, "the router chain is writable, like the other three"


def test_the_panel_says_the_router_chain_is_a_chain_of_models_json():
    src = HUB.read_text()
    card = src[src.index("const MLAB="):src.index("EFF=[")]
    assert "The models the router runs on" in card, "the Router card does not say what its chain is"
    assert "graph" not in card, "the card still tells the reader the router models live in the graph"


@pytest.mark.heavy
def test_the_stamp_script_reports_the_record_current():
    """The shipped graph and its record still ship beside each other; a stale record breaks check_record.py."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "stamp_router_record.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"the shipped router record is stale: {r.stdout}{r.stderr}"
