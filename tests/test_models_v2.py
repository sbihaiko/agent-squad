"""The repository models.json is the v2 chain shape, and the readers the hub and the resolver run accept it (ADR-0147, #544)."""
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from squad import modelreg

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "contrib/squad-goal/scripts/models.json"
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
V1_KEYS = ("coding_models", "review_models", "vision_models", "router_models")


@pytest.fixture(scope="module")
def doc():
    return json.loads(MODELS.read_text())


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_models_v2", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_repository_models_json_is_v2(doc):
    assert doc["schema_version"] == 2
    assert set(doc["chains"]) == {"coding", "review", "vision", "router"}
    assert not [k for k in V1_KEYS if k in doc]


def test_the_resolver_reads_every_repository_chain(doc):
    assert [e.raw for e in modelreg.chain(doc, "coding").entries][0] == "claude-gpt-5.6-terra"
    assert [e.raw for e in modelreg.chain(doc, "review").entries][0] == "claude:claude-opus-5-5"
    assert modelreg.chain(doc, "vision").entries
    assert modelreg.chain(doc, "router").entries == []


def test_the_panel_reads_the_repository_chains_under_the_v1_keys(hub, doc):
    view = hub.v1_view(doc)
    assert view["coding_models"][0] == "claude-gpt-5.6-terra"
    assert view["review_models"] == ["claude:claude-opus-5-5", "gemini-3.8-flash-medium"]
    assert view["hours"]["review_models"] == {"claude:claude-opus-5-5": "19-07"}
    assert view["efforts"]["vision_models"] == {"gemini-3.8-flash-low": "low"}
    assert "schema_version" not in view and "chains" not in view


def test_a_v1_file_passes_through_the_panel_reader_unchanged(hub):
    v1 = {"coding_models": ["claude-sonnet-5-5"], "review_models": ["claude:claude-opus-5-5"]}
    assert hub.v1_view(v1) is v1


def test_the_panel_refuses_to_save_over_a_v2_file(hub, monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    shutil.copy(MODELS, tmp)
    before = tmp.read_text()
    monkeypatch.setattr(hub, "MODELS", tmp)
    out = hub._models_set({"coding_models": ["claude-sonnet-5-5"]})
    assert "err" in out and "v2" in out["err"]
    assert tmp.read_text() == before
