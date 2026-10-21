"""GitHub issue #225: the Router queue stores only an id the engine can run.

The engine hands a dispatch envelope's id to the Claude Agent SDK, which checks it against the gateway's catalogue.
The panel's Add box mixes four catalogues (Anthropic, Antigravity, codex, grok) and folds the gateway spelling onto
the bare one, and the Save wrote the box's id verbatim into the graph. A run then died in 1.9 s with
`adapter_error`: "There's an issue with the selected model (gpt-6.1-sol)".

The hub is imported with a checkout of its own, so the Save runs for real against a graph and a models.json in a
temp folder. The gateway is pinned to a list: the guard runs against the module, never the live hub.
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

# A part of what the gateway's `/v1/models` served on 2026-10-13: every id spelled `claude-...`.
GATEWAY = ["claude-gpt-5.6-terra", "claude-gpt-5.6-sol", "claude-gpt-6-astra", "claude-deepseek-v4-flash[1m]",
           "claude-opus-5-5"]
# Ids the box offered for other CLIs (codex, agy, grok) that the gateway does not serve.
OTHER_CLI = ["gpt-6.1-sol", "gemini-3.8-flash-high", "grok-4.6"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_router_queue_models", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def checkout(hub, monkeypatch, tmp_path):
    d = tmp_path / "docs" / "design"
    d.mkdir(parents=True)
    graph = d / "router.graph.json"
    graph.write_text(json.dumps({"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop", "children": ["route"]},
        {"name": "route", "kind": "dispatch", "allow": {"models": ["claude-gpt-5.6-terra"], "efforts": ["low"]}}]},
        indent=2) + "\n")
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; sys.exit(0)\n")
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"coding_models": ["gpt-5.6-terra"], "review_models": ["gpt-6.1-sol"],
                                  "vision_models": ["grok-4.6"]}, indent=2) + "\n")
    monkeypatch.setattr(hub, "CHECKOUT", str(tmp_path))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(GATEWAY))
    monkeypatch.setattr(hub.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    return graph


def _envelope(graph):
    return [n for n in json.loads(graph.read_text())["nodes"] if n.get("kind") == "dispatch"][0]["allow"]["models"]


def test_a_queue_entry_is_stored_in_the_gateway_spelling(hub, checkout):
    out = hub.models_set({**hub.models_get(), "router_models": ["gpt-5.6-sol", "deepseek-v4-flash[1m]", "claude-gpt-6-astra"]})
    assert "err" not in out, out
    assert _envelope(checkout) == ["claude-gpt-5.6-sol", "claude-deepseek-v4-flash[1m]", "claude-gpt-6-astra"]


def test_a_queue_entry_the_gateway_does_not_serve_is_refused_at_save_by_name(hub, checkout):
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-gpt-5.6-terra", "gpt-6.1-sol"]})
    assert "gpt-6.1-sol" in out.get("err", ""), out
    assert _envelope(checkout) == ["claude-gpt-5.6-terra"]


def test_the_queue_add_box_offers_only_gateway_ids_and_the_role_box_keeps_the_others(hub, checkout):
    got = hub.models_get()
    assert sorted(got["router_known"]) == sorted(GATEWAY)
    assert [m for m in OTHER_CLI if m not in got["known"]] == []
    src = HUB_PY.read_text()
    assert re.search(r'<input list=mkr data-radd', src), "the Router queue Add box reads its own list"
    assert re.search(r'<datalist id=mkr>\$\{\(mdl\.router_known\|\|\[\]\)', src), "that list is router_known"


def test_an_anthropic_id_the_gateway_does_not_list_is_kept_in_the_queue(hub, checkout, monkeypatch):
    """The gateway's `/v1/models` lists no Anthropic id, and it still passes one through: the loop ran a child on
    `claude-opus-5-5` on 2026-10-13. A Save that refused it would refuse the queue the shipped graph carries."""
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [m for m in GATEWAY if not m.startswith("claude-opus")])
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-gpt-5.6-terra", "claude-opus-5-5"]})
    assert "err" not in out, out
    assert _envelope(checkout) == ["claude-gpt-5.6-terra", "claude-opus-5-5"]


def test_the_queue_add_box_offers_the_registry_live_anthropic_ids(hub, checkout, monkeypatch):
    """The owner could not add Opus 5.5 to the Router queue (#687): the gateway's `/v1/models` lists no Anthropic id,
    so the box offered none, though router_resolve passes an Anthropic id through and the server accepts it. The box
    also offers every live registry id that matches ANTHROPIC_ID; a retired one stays out."""
    served = [m for m in GATEWAY if not hub.ANTHROPIC_ID.match(m)]
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(served))
    got = hub.router_known()
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"} <= set(got)
    reg = hub.router_envelope.modelreg.default_registry()
    assert [m for m in got if m in reg.entries and reg.entries[m].retired] == []
    assert [m for m in got if hub.ANTHROPIC_ID.match(m) and m not in reg.entries] == []
