"""GitHub issue #221: one model-id shape, and it accepts the context suffix the gateway serves.

The hub's Models panel refused every Save of the Router queue with `router_models: need 1-8 distinct model ids`. The
queue of the shipped graph already carries `claude-deepseek-v4-flash[1m]`, and the shape the save validates with,
`^[A-Za-z0-9][A-Za-z0-9._:\\-]{0,79}$`, has no `[` and no `]`. The same shape is written again in the panel's two Add
boxes and in `known_models`, so the id the gateway serves could not be typed, could not be saved, and was not offered
-- for any profile, because the queue always carries it.

Measured 2026-10-13 on the loop's own hub: `POST /api/models` with a correct `Origin` and the same body the panel
sends answers that error before the graph is written. The gateway's own `/v1/models` served 26 ids that day, three of
them suffixed: `claude-deepseek-flash[1m]`, `claude-deepseek-v4-flash[1m]`, `claude-deepseek-v4-pro[1m]`.

The hub is imported with a checkout of its own, as `test_hub_model_off_switch.py` does, so the Save runs for real
against a graph and a models.json in a temp folder instead of the machine's own.
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

# The three ids the gateway serves with a context suffix, read from its own catalogue on 2026-10-13.
SUFFIXED = ["claude-deepseek-flash[1m]", "claude-deepseek-v4-flash[1m]", "claude-deepseek-v4-pro[1m]"]
# An id no shape may accept: a space, a slash and an empty suffix are not a model.
REFUSED = ["no model", "../etc/passwd", "claude-gpt-5.6-terra[]", ""]


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


@pytest.fixture
def checkout(hub, monkeypatch, tmp_path):
    """A checkout of the hub's own: the run graph the Save writes, the record it restamps, and models.json."""
    d = tmp_path / "docs" / "design"
    d.mkdir(parents=True)
    graph = d / "router.graph.json"
    graph.write_text(json.dumps({"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop", "children": ["route", "final_gate"]},
        {"name": "route", "kind": "dispatch", "allow": {"models": ["claude-deepseek-v4-flash[1m]"], "efforts": ["low", "medium"]}},
        {"name": "router", "kind": "agent", "model": "claude-deepseek-v4-flash[1m]"}]}, indent=2) + "\n")
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; print('restamped'); sys.exit(0)\n")
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"coding_models": ["claude-deepseek-v4-flash[1m]"],
                                  "review_models": ["gpt-5.6-terra"],
                                  "vision_models": ["grok-4.5"]}, indent=2) + "\n")
    monkeypatch.setattr(hub, "CHECKOUT", str(tmp_path))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(SUFFIXED) + ["claude-gpt-5.6-terra"])
    return graph, models


def _panel_shape(hub):
    """The shape the panel's Add boxes test: the one the save validates with, substituted into the page when it is served.

    Read from the source that serves it, never copied into this test: the defect was a second spelling of the shape, and
    a test that spells it again would agree with the source by construction."""
    src = HUB_PY.read_text()
    assert 'const MODELRE=new RegExp("^"+__MODELID__+"$");' in src, "the page no longer carries the one shape"
    assert '.replace("__MODELID__", json.dumps(MODEL_ID_SRC))' in src, "the served page never substitutes the shape"
    assert src.count("MODELRE.test(v)") == 2, "both Add boxes test it"
    assert not re.search(r"!/\(\^\[A-Za-z0-9\]\S*?\)\.test\(v\)\)", src), "a copy of the shape survives in the page"
    return re.compile("^" + hub.MODEL_ID_SRC + "$")


def _graph_ids():
    d = json.loads(GRAPH.read_text())
    out = []
    for n in d.get("nodes") or []:
        if n.get("model"):
            out.append(n["model"])
        out += (n.get("allow") or {}).get("models") or []
    return out


def test_the_shipped_graph_names_an_id_with_a_context_suffix():
    """The guard below is about a real id, not a hypothetical one: the graph the loop starts holds this spelling."""
    assert [m for m in _graph_ids() if re.search(r"\[\w+\]$", m)], _graph_ids()


def test_the_save_accepts_a_router_queue_that_names_a_suffixed_id(hub, checkout):
    graph, _ = checkout
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-deepseek-v4-flash[1m]", "claude-gpt-5.6-terra"]})
    assert "err" not in out, out
    written = [n for n in json.loads(graph.read_text())["nodes"] if n.get("kind") == "dispatch"][0]
    assert written["allow"]["models"] == ["claude-deepseek-v4-flash[1m]", "claude-gpt-5.6-terra"]


def test_the_save_accepts_a_chain_that_names_a_suffixed_id(hub, checkout):
    _, models = checkout
    out = hub.models_set({**hub.models_get(), **{k: list(SUFFIXED) for k in ("coding_models", "review_models", "vision_models")}})
    assert "err" not in out, out
    assert json.loads(models.read_text())["coding_models"] == SUFFIXED


def test_the_add_box_offers_the_ids_the_gateway_serves_with_a_context_suffix(hub, checkout):
    assert [m for m in SUFFIXED if m not in hub.known_models(hub.models_get())] == []


def test_one_shape_answers_for_the_save_the_add_box_and_the_gateway(hub):
    """The save's shape and the shape the page tests agree on the ids the gateway serves and on the ones neither takes."""
    shapes = [hub.MODEL_ID.match, _panel_shape(hub).match]
    for m in SUFFIXED + _graph_ids():
        assert [f"refuses {m!r}" for match in shapes if not match(m)] == []
    for m in REFUSED:
        assert [f"takes {m!r}" for match in shapes if match(m)] == []
