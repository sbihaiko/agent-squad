"""The four hub chains render in one shape (SPEC 505, #545).

Each entry names its subscription and its quota state. A spent pool shows as spent on every chain that uses it. A
chain drops a registered entry it cannot run, and each add dropdown offers only the models its chain can use. The
rules come from the one registry (ADR-0147); the page draws what the hub hands it (ADR-0137).
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
REGISTRY = json.loads((ROOT / "config/models.registry.json").read_text())

V2 = {"schema_version": 2, "chains": {
    "coding": [{"id": "grok-4.7", "route": "gateway"}, {"id": "gemini-3.8-flash", "route": "cli"},
               {"id": "no-such-model-9", "route": "gateway"}],
    "review": [{"id": "claude-opus-5-5", "route": "cli"}, {"id": "gemini-3.8-flash", "route": "cli"},
               {"id": "grok-4.7", "route": "cli"}],
    "vision": [{"id": "gpt-5.6-luna", "route": "cli"}, {"id": "deepseek-flash", "route": "gateway"},
               {"id": "grok-4.7", "route": "gateway"}],
    "router": []}}


@pytest.fixture(scope="module")
def hub_module():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_chain_shape", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hub(hub_module, monkeypatch, tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps(V2))
    monkeypatch.setattr(hub_module, "MODELS", models)
    monkeypatch.setattr(hub_module, "SHARED", tmp_path)
    monkeypatch.setattr(hub_module, "gateway_models", lambda ttl=120: ["gpt-5.6-terra", "gpt-5.5"])
    monkeypatch.setattr(hub_module, "router_get", lambda: ["grok-4.7", "gpt-5.6-terra"])
    return hub_module


def test_every_entry_names_its_subscription_and_its_quota_state(hub):
    got = hub.models_get()
    subs = REGISTRY["subscriptions"]
    assert got["boxes"]["coding_models"]["grok-4.7"]["sub"] == {"key": "grok", "label": subs["grok"]["label"]}
    assert got["boxes"]["review_models"]["gemini-3.8-flash"]["sub"]["label"] == subs["gemini"]["label"]
    assert got["boxes"]["vision_models"]["gpt-5.6-luna"]["sub"]["label"] == subs["gpt"]["label"]
    assert got["boxes"]["router_models"]["grok-4.7"]["sub"]["label"] == subs["grok"]["label"]
    for chain, mid in (("coding_models", "grok-4.7"), ("review_models", "gemini-3.8-flash"),
                       ("vision_models", "gpt-5.6-luna"), ("router_models", "grok-4.7")):
        assert got["boxes"][chain][mid]["quota"]["spent"] is False


def test_an_unregistered_entry_has_no_subscription_and_keeps_its_plain_box(hub):
    got = hub.models_get()
    assert got["boxes"]["coding_models"]["no-such-model-9"]["sub"] is None
    assert got["boxes"]["coding_models"]["no-such-model-9"]["quota"] is None
    assert got["boxes"]["coding_models"]["no-such-model-9"]["text"] == "unregistered"


def test_a_spent_grok_pool_shows_spent_on_every_chain_that_uses_it(hub, tmp_path):
    (tmp_path / "grok-limited").write_text("no-reset\n")
    got = hub.models_get()
    for chain in ("coding_models", "review_models", "vision_models", "router_models"):
        quota = got["boxes"][chain]["grok-4.7"]["quota"]
        assert quota["spent"] is True, chain
        assert quota["reason"] == "balance exhausted", chain


def test_a_chain_drops_a_registered_entry_it_cannot_run_and_keeps_an_unregistered_one(hub):
    got = hub.models_get()
    assert got["coding_models"] == ["grok-4.7", "no-such-model-9"]          # gemini is a cli route only: no gateway
    assert got["review_models"] == ["claude-opus-5-5", "gemini-3.8-flash", "grok-4.7"]
    assert got["vision_models"] == ["gpt-5.6-luna", "grok-4.7"]           # deepseek takes no image input


def test_a_chain_drops_a_retired_router_entry(hub, monkeypatch):
    monkeypatch.setattr(hub, "router_get", lambda: ["gpt-5.5", "gpt-5.6-terra"])
    assert hub.models_get()["router_models"] == ["gpt-5.6-terra"]


def test_each_dropdown_offers_only_the_models_its_chain_can_use(hub):
    got = hub.models_get()

    def registry_ids(chain):
        return {hub.modelreg.model(s["id"]).id for s in got["suggest"][chain]}

    coding, review, vision = (registry_ids(c) for c in ("coding_models", "review_models", "vision_models"))
    assert "gemini-3.8-flash" not in coding and "grok-4.7" in coding
    assert "deepseek-flash" in review and "gemini-3.8-flash" in review
    assert "deepseek-flash" not in vision and "gpt-oss-120b" not in vision
    assert "gpt-5.6-luna" in vision
    router = registry_ids("router_known")
    assert "gpt-5.6-terra" in router and "gpt-5.5" not in router


def test_the_page_draws_subscription_and_quota_on_all_four_chains(hub):
    src = HUB_PY.read_text()
    assert "function mdlSub(" in src
    assert src.count("${mdlSub(x)}") >= 2                        # the role rows and the router rows
    assert "list=mk-${k}" in src and "id=mk-${k}" in src          # one dropdown per role chain
    assert 'sugId("router_known"' in src
