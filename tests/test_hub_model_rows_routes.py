"""Two routes of one model read as two rows the owner can tell apart (#1264).

The hub's Models view draws one row per (model, route) the chains hold, and the registry labels two routes of one
model the same (`claude-sonnet-5-5` on the gateway, `claude:claude-sonnet-5-5` through the claude CLI), so neither
row names its route. The route is read from the same `model_box` the chain rows draw (ADR-0137), and the payload is
read from `models_get`, the function behind `GET /api/models`, the call the Models view makes.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_rows_routes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(hub, monkeypatch, tmp_path, doc):
    """The Models view's own payload: `GET /api/models` serves `models_get()`, so the test reads that path."""
    models = tmp_path / "models.json"
    models.write_text(json.dumps(doc))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [])
    return hub.models_get()


def test_two_spellings_of_one_model_on_two_routes_read_as_two_distinct_labels(hub, monkeypatch, tmp_path):
    got = _payload(hub, monkeypatch, tmp_path, {
        "coding_models": ["claude-deepseek-flash[1m]"],
        "review_models": ["claude:claude-deepseek-flash"],
        "vision_models": []})
    rows = got["models"]
    assert len(rows) == 2, "one row per route spelling in the payload the view draws"
    by_id = {r["id"]: r for r in rows}
    assert by_id["claude-deepseek-flash[1m]"]["route"] == "gateway"
    assert by_id["claude-deepseek-flash"]["route"] == "claude CLI"
    labels = [r["label"] for r in rows]
    assert len(set(labels)) == 2                       # the two rows tell the owner apart (#1264)
    assert by_id["claude-deepseek-flash[1m]"]["label"] == "DeepSeek V4.1 Flash · gateway"
    assert by_id["claude-deepseek-flash"]["label"] == "DeepSeek V4.1 Flash · claude CLI"


def test_one_model_held_by_two_chains_on_two_routes_reads_as_two_labelled_rows(hub, monkeypatch, tmp_path):
    """One bare id, two routes: the first chain no longer answers for the second route's row."""
    got = _payload(hub, monkeypatch, tmp_path, {
        "coding_models": ["claude-sonnet-5-5"],
        "review_models": ["claude:claude-sonnet-5-5"],
        "vision_models": []})
    rows = got["models"]
    assert len(rows) == 2, "one row per (model, route) pair"
    assert sorted(r["route"] for r in rows) == ["claude CLI", "gateway"]
    assert sorted(r["label"] for r in rows) == ["Claude Sonnet 5.5 · claude CLI", "Claude Sonnet 5.5 · gateway"]


def test_a_model_on_one_route_keeps_the_plain_registry_label(hub, monkeypatch, tmp_path):
    got = _payload(hub, monkeypatch, tmp_path, {
        "coding_models": ["claude-sonnet-5-5"],
        "vision_models": ["claude-sonnet-5-5"],
        "review_models": []})
    rows = got["models"]
    assert len(rows) == 1
    assert rows[0]["label"] == "Claude Sonnet 5.5"
    assert rows[0]["route"] == "gateway"
