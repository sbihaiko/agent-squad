"""GitHub issue #221: one model-id shape, so the id the gateway serves with a context suffix can be saved.

The hub's Models panel kept the shape in three places -- the two Add boxes carried the literal, and the Save
validated with a copy of it. The shape allowed no `[` or `]`, so `claude-deepseek-v4-flash[1m]`, the id the
gateway at http://localhost:8016/v1/models serves and the id the shipped run graph already names in its dispatch
envelope, was refused. Every Save of the Router queue was refused for that reason (`router_models: need 1-8
distinct model ids`), and `known_models` dropped the gateway's suffixed ids from the box.

The shape now lives once (`MODEL_ID_SRC` in hub.py): the Save validates with it and the page's Add boxes use it,
injected into the page as the `MID` literal. This suite reads the shape the panel ships, so a copy that drifts is
caught (ADR-0137: a GUI keeps its rules out of the view).
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
GRAPH = ROOT / "docs" / "design" / "router.graph.json"
# What the gateway serves, and what the shipped graph already names in its dispatch envelope.
SUFFIXED = "claude-deepseek-v4-flash[1m]"
NO_CHAINS = {"coding_models": [], "review_models": [], "vision_models": []}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_id_shape", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page_model_id(page):
    """The model-id shape the page ships, compiled: the one literal the Add boxes test with, `MID`."""
    m = re.search(r"const MID=/\^(.*?)\$/;", page)
    assert m, "the page ships no single model-id shape (`const MID=/^…$/`)"
    return re.compile("^" + m.group(1) + "$")


@pytest.fixture
def checkout(hub, tmp_path, monkeypatch):
    """A checkout of its own: the models file, the run graph and the restamp script the save path reaches."""
    model_file = tmp_path / "models.json"
    model_file.write_text(json.dumps(NO_CHAINS, indent=2) + "\n")
    monkeypatch.setattr(hub, "MODELS", model_file)
    d = tmp_path / "docs" / "design"; d.mkdir(parents=True)
    (d / "router.graph.json").write_text(json.dumps(
        {"schema_version": 1, "name": "router", "nodes": [
            {"name": "main", "kind": "loop"},
            {"name": "route", "kind": "dispatch", "allow": {"models": ["a-1"]}}]}, indent=2) + "\n")
    s = tmp_path / "scripts"; s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; sys.exit(0)\n")
    monkeypatch.setattr(hub, "CHECKOUT", str(tmp_path))
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [])   # no live gateway in a test
    return tmp_path


def test_a_save_accepts_the_suffixed_id_the_gateway_serves(hub, checkout):
    """Acceptance 1: the Save takes the Router queue and a chain naming `claude-deepseek-v4-flash[1m]`, and writes
    it into the graph."""
    out = hub.models_set({"coding_models": [SUFFIXED, "gpt-5.6-terra"], "review_models": ["gpt-5.6-terra"],
                          "vision_models": ["gpt-5.6-terra"], "router_models": [SUFFIXED]})
    assert "err" not in out, out
    assert out["coding_models"] == [SUFFIXED, "gpt-5.6-terra"]
    graph = json.loads((checkout / "docs" / "design" / "router.graph.json").read_text())
    disp = next(n for n in graph["nodes"] if n["kind"] == "dispatch")
    assert disp["allow"]["models"] == [SUFFIXED]


def test_both_add_boxes_use_the_one_shape_and_accept_the_id(hub):
    """Acceptance 2, first half: the panel's Add boxes use the shape and not a copy, and the shape takes the id."""
    page = hub.PAGE
    assert page.count("MID.test(v)") == 2, "the Add boxes do not both use the one model-id shape"
    assert page_model_id(page).match(SUFFIXED), "the panel's shape refuses the id the gateway serves"


def test_the_shape_lives_in_one_place(hub):
    """One shape, in one place: the source carries it once, and the page reaches it by injection, never a copy."""
    src = HUB_PY.read_text()
    assert src.count("[A-Za-z0-9][A-Za-z0-9._:") == 1, "the model-id shape is written more than once"


def test_the_box_offers_the_gateways_suffixed_ids(hub, monkeypatch, tmp_path):
    """Acceptance 2, second half: `known_models` offers the gateway's `[1m]` ids."""
    monkeypatch.setattr(hub.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [SUFFIXED, "claude-gpt-5.6-terra"])
    offered = hub.known_models(dict(NO_CHAINS))
    assert SUFFIXED in offered


def test_every_model_id_the_shipped_graph_names_is_one_the_panel_accepts(hub):
    """Acceptance 3: the graph and the panel agree on the shape, read through the shape the page ships."""
    doc = json.loads(GRAPH.read_text())
    ids = [m for n in doc["nodes"] if n["kind"] == "dispatch" for m in (n.get("allow") or {}).get("models", [])]
    assert ids, "the shipped graph has no dispatch envelope"
    rx = page_model_id(hub.PAGE)
    assert [m for m in ids if not rx.match(m)] == [], "the panel refuses an id the shipped graph names"
    assert [m for m in ids if not hub.MODEL_ID.match(m)] == [], "the Save validation refuses a shipped graph id"
