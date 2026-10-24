"""Each hub box that stands for a model shows the real LLM from the one registry, beside its route (#677).

The raw id hides the LLM: `claude-grok-4.7` is Grok 4.7 through the gateway, and `claude-deepseek-flash[1m]` is
DeepSeek V4.1 Flash. One function, `model_box`, decides what a box shows (ADR-0137): the label, the route, or
"unregistered" for an id the registry does not know. The page renders its answer and holds no provider rule.
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
    os.environ["SG_STATE"] = tempfile.mkdtemp()   # hermetic: no ledger or run state of the owner is read
    spec = importlib.util.spec_from_file_location("hub_model_boxes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("raw,chain,llm,route", [
    ("claude-grok-4.7", "coding_models", "Grok 4.7", "gateway"),
    ("claude-deepseek-flash[1m]", "coding_models", "DeepSeek V4.1 Flash", "gateway"),   # #683: the canonical V4.1 id uses tools
    ("claude:claude-opus-5-5", None, "Claude Opus 5.5", "claude CLI"),
    ("claude-opus-5-5", "review_models", "Claude Opus 5.5", "claude CLI"),   # the panel's bare id: the review chain adds claude:
    ("claude-opus-5-5", "coding_models", "Claude Opus 5.5", "gateway"),
    ("gemini-3.8-flash-medium", "review_models", "Gemini 3.8 Flash", "agy CLI"),
    ("gpt-5.6-luna", "vision_models", "GPT-5.6 Luna", "codex CLI"),
    ("opus", None, "Claude Opus 5.5", "gateway"),
    ("gpt-6-luna", "coding_models", "GPT-6 Luna", "codex CLI"),   # registered by #679
    ("claude-haiku-5-5", "coding_models", "Claude Haiku 5.5", "gateway"),
])
def test_a_registered_id_shows_its_llm_beside_its_route(hub, raw, chain, llm, route):
    box = hub.model_box(raw, chain)
    assert box == {"id": raw, "llm": llm, "route": route, "registered": True, "text": f"{llm} · {route}"}


@pytest.mark.parametrize("raw", ["gpt-9-nova", "claude-haiku-9-9", "no-such-model-9"])
def test_an_unknown_id_says_unregistered_plainly(hub, raw):
    assert hub.model_box(raw) == {"id": raw, "llm": None, "route": None, "registered": False, "text": "unregistered"}


def test_the_panel_gets_a_box_for_every_chain_id_and_never_stores_it(hub, monkeypatch, tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"coding_models": ["claude-grok-4.7"], "review_models": ["gpt-9-nova"],
                                  "vision_models": []}))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: [])
    got = hub.models_get()
    assert got["boxes"]["coding_models"]["claude-grok-4.7"]["text"] == "Grok 4.7 · gateway"
    assert got["boxes"]["review_models"]["gpt-9-nova"]["text"] == "unregistered"
    assert set(got["boxes"]["router_models"]) == set(got["router_models"])
    assert set(got["boxes"]["known"]) >= set(got["known"]) | set(got["router_known"])
    assert "boxes" in hub.READONLY


def test_the_page_renders_the_box_and_holds_no_provider_rule(hub):
    src = HUB_PY.read_text()
    assert "function mdlProv" not in src and "mdlProv(" not in src
    assert src.count("mdlBox(k,m)") >= 1 and src.count('mdlBox("coding_models",lc)') == 1   # one row form, every chain


def test_the_token_pie_names_each_model_by_its_box(hub, monkeypatch):
    run = {"spend": 1.0, "sub": 1.0, "api": 0.0, "t0": 0, "done": True, "dur": 1,
           "mod": {"claude-grok-4.7": {"in": 5, "out": 1, "cache": 0, "calls": 1, "spend": 1.0}}}
    monkeypatch.setattr(hub, "all_runs", lambda: [run])
    row = hub.stats("day")["models"][0]
    assert row["box"]["text"] == "Grok 4.7 · gateway"
