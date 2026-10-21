"""SPEC 505, ticket 543: a coding or router choice resolves through the registry, and a no-tools model is never one.

ADR-0147: the resolver says whether a route takes tools; the envelope and the router envelope ask it, and neither keeps
its own table.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

from squad import modelreg
from squad.envelope import validate_request
from tests.helpers import node, router

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contrib/squad-goal/scripts"))
import router_envelope  # noqa: E402

NO_TOOLS = "claude-deepseek-flash[1m]"   # registry id deepseek-flash, marked no-tools in the test registry below


@pytest.fixture(autouse=True)
def no_tools_registry(tmp_path, monkeypatch):
    """No shipped entry says no tools since #683 (DeepSeek V4.1 Flash uses tools), but the guard stays: the test
    registry marks `deepseek-flash` tools:false, and both resolver copies read it."""
    doc = json.loads((ROOT / "config/models.registry.json").read_text())
    for m in doc["models"]:
        if m["id"] == "deepseek-flash":
            m["capabilities"]["tools"] = False
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(doc))
    for mod in (modelreg, router_envelope.modelreg):
        monkeypatch.setattr(mod, "default_registry", lambda p=path, m=mod: m.load(p))


def _errors(model, allowed):
    """The rules the shipped route envelope reports for a request that names `model`, with `allowed` as its allow list."""
    disp = copy.deepcopy(node(router(), "route"))
    disp["allow"]["models"] = allowed
    skill = disp["allow"]["skills"][0]["name"]
    raw = {"prompt": "Run it.", "skill": skill, "model": model, "effort": "medium", "tools": ["Read"]}
    return validate_request(disp, raw, 1, None, 100.0)[1]


def test_the_registry_says_the_guard_model_takes_no_tools():
    reg = modelreg.default_registry()
    ref = modelreg.normalize(NO_TOOLS, reg)
    assert ref.known and ref.route == "gateway" and not reg.entries[ref.id].capabilities["tools"]
    assert modelreg.compatible(NO_TOOLS, "coding", reg) == (False, "gateway model takes no tools")
    assert modelreg.compatible(NO_TOOLS, "router", reg) == (False, "gateway model takes no tools")


def test_a_no_tools_model_stays_a_review_choice():
    assert modelreg.compatible(NO_TOOLS, "review")[0] is True


def test_the_envelope_refuses_a_model_whose_route_takes_no_tools_even_when_the_allow_list_names_it():
    errs = _errors(NO_TOOLS, [NO_TOOLS, "claude-sonnet-5-5"])
    assert f"model_takes_no_tools:{NO_TOOLS}" in errs


def test_the_envelope_still_passes_a_tool_model_and_an_unknown_spelling_as_data():
    for model in ("claude-sonnet-5-5", "scripted-model"):
        errs = _errors(model, [model])
        assert not [e for e in errs if e.startswith("model_")], errs


def test_the_router_envelope_names_a_chain_entry_whose_route_takes_no_tools():
    chain = ["claude-sonnet-5-5", NO_TOOLS, "scripted-model"]
    assert router_envelope.takes_no_tools(chain) == [NO_TOOLS]
