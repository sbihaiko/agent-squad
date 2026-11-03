"""Two entries of one model on two routes read as two rows the owner can tell apart (#1264).

The hub's Models table draws one row per spelling the chains hold, and the registry labels two spellings of one
model the same (`claude-deepseek-flash[1m]` on the gateway, `claude:claude-deepseek-flash` through the claude CLI).
Both rows read "DeepSeek V4.1 Flash", so neither names its route. The row now carries the label it draws: the plain
registry label when one row holds the model, and the label beside its route when more than one row does.
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


def test_two_routes_of_one_model_read_as_two_distinct_labels(hub, monkeypatch, tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"coding_models": ["claude-deepseek-flash[1m]"],
                                  "review_models": ["claude:claude-deepseek-flash"],
                                  "vision_models": []}))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [])
    rows = {r["id"]: r for r in hub.models_get()["models"]}
    assert rows["claude-deepseek-flash[1m]"]["route"] == "gateway"
    assert rows["claude-deepseek-flash"]["route"] == "claude CLI"
    labels = [r["label"] for r in hub.models_get()["models"]]
    assert len(labels) == 2
    assert len(set(labels)) == 2                       # the two rows tell the owner apart (#1264)
    assert rows["claude-deepseek-flash[1m]"]["label"] == "DeepSeek V4.1 Flash · gateway"
    assert rows["claude-deepseek-flash"]["label"] == "DeepSeek V4.1 Flash · claude CLI"
