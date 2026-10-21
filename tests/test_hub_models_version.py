"""A Save from a stale page never overwrites what another Save wrote (#688).

The owner's old tab posted its whole panel back and removed Router queue entries a newer Save had added. models_get
hands the page a `version`, a hash of models.json and of the graph's allow.models; models_set refuses a body whose
version differs, or that carries none, with "stale: reload the page", and writes nothing.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GATEWAY = ["claude-gpt-5.6-terra", "claude-gpt-5.6-sol", "claude-opus-5-5"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_models_version", HUB_PY)
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
    return graph, models


def test_the_panel_gets_a_version_it_never_stores(hub, checkout):
    got = hub.models_get()
    assert isinstance(got["version"], str) and got["version"]
    assert "version" in hub.READONLY
    assert "version" not in json.loads(checkout[1].read_text())


def test_a_save_from_a_stale_page_is_refused_and_writes_nothing(hub, checkout):
    graph, models = checkout
    old = hub.models_get()
    newer = hub.models_set({**hub.models_get(), "router_models": ["claude-gpt-5.6-terra", "claude-gpt-5.6-sol"]})
    assert "err" not in newer, newer
    g, m = graph.read_text(), models.read_text()
    out = hub.models_set({**old, "router_models": ["claude-gpt-5.6-terra"], "coding_models": ["grok-4.6"]})
    assert out == {"err": "stale: reload the page"}
    assert (graph.read_text(), models.read_text()) == (g, m)


def test_a_body_with_no_version_is_refused(hub, checkout):
    body = {k: v for k, v in hub.models_get().items() if k != "version"}
    assert hub.models_set(body) == {"err": "stale: reload the page"}


def test_a_change_to_models_json_by_hand_moves_the_version(hub, checkout):
    v = hub.models_get()["version"]
    checkout[1].write_text(json.dumps({"coding_models": ["grok-4.6"], "review_models": ["gpt-6.1-sol"],
                                       "vision_models": ["grok-4.6"]}))
    assert hub.models_get()["version"] != v


def test_a_fresh_save_passes_and_hands_back_the_new_version(hub, checkout):
    out = hub.models_set({**hub.models_get(), "coding_models": ["grok-4.6"]})
    assert "err" not in out, out
    assert out["version"] == hub.models_get()["version"]
    assert "err" not in hub.models_set(out)
