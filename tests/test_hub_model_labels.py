"""The hub's add boxes name a model by its registry label as well as its id.

The owner looked for "DeepSeek 4.1" in the chains and did not find it. The chains already held
`claude-deepseek-flash[1m]`, which the registry labels "DeepSeek V4.1 Flash no-tools", but the hub showed raw ids
only, so no id in the box carried "4.1". The panel now gets a `labels` map from the one registry (ADR-0147), and each
datalist option carries its label, so the browser matches "4.1" against it. The label is display only: the value
that is saved stays the id.
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
    spec = importlib.util.spec_from_file_location("hub_model_labels", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_deepseek_spelling_reads_as_its_v4_1_label(hub):
    labels = hub.model_labels(["claude-deepseek-flash[1m]", "claude-deepseek-v4-flash"])
    assert labels["claude-deepseek-flash[1m]"] == "DeepSeek V4.1 Flash no-tools"   # re-anchored to the #687 scheme: <Family> <version> <tier>, no parentheses
    assert labels["claude-deepseek-v4-flash"] == "DeepSeek V4.1 Flash"


def test_an_id_the_registry_does_not_know_has_no_label(hub):
    assert hub.model_labels(["no-such-model-9"]) == {}


def test_the_panel_gets_labels_and_never_stores_them(hub, monkeypatch, tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"coding_models": ["claude-deepseek-flash[1m]"], "review_models": [],
                                  "vision_models": []}))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: ["claude-deepseek-flash[1m]"])
    got = hub.models_get()
    assert got["labels"]["claude-deepseek-flash[1m]"] == "DeepSeek V4.1 Flash no-tools"   # re-anchored to the #687 scheme: <Family> <version> <tier>, no parentheses
    assert "labels" in hub.READONLY


def test_each_add_box_option_carries_its_label(hub):
    src = HUB_PY.read_text()
    # re-anchored (#687): the label is the option's primary text and the id its secondary one
    assert src.count('<option value="${esc(s.text)}" label="${esc(s.id)}">') == 2
