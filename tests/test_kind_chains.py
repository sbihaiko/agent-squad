"""SPEC 750, phase 1: a child's model comes from the chain of its kind; the envelope holds kinds, not model ids.

Guards: the schema takes `allow.kinds` in place of `allow.models`; the launcher resolves the four chains once and
records them in `run_start`; a request names a `kind`, and code picks the first model of that chain; a request that names
a `model` is rejected whole; a child falls back along its own kind's chain.
"""
import json

import pytest

from squad import launch
from squad.adapter import ScriptedAdapter
from squad.envelope import validate_request
from squad.graph import InvalidGraph, canon_hash, from_doc
from squad.options import OptionError
from tests.flows import agent, doc, scope_gate
from tests.helpers import route

CHAINS = {
    "router": [{"id": "claude-opus-5-5"}],
    "coding": [{"id": "claude-sonnet-5-5"}, {"id": "claude-sonnet-4-6"}],
    "review": [{"id": "claude-opus-5-5"}, {"id": "claude-deepseek-flash[1m]"}],
    "vision": [{"id": "claude-sonnet-5-5"}],
}


def models_file(tmp_path, chains=None):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"schema_version": 2, "chains": chains or CHAINS}))
    return path


def kinds_disp(kind_models=None):
    d = route()
    d["allow"] = {k: v for k, v in d["allow"].items() if k != "models"}
    d["allow"]["kinds"] = ["coding", "review", "vision"]
    d["allow"]["kind_models"] = kind_models or {"coding": ["claude-sonnet-5-5", "claude-sonnet-4-6"], "review": ["claude-opus-5-5"], "vision": ["claude-sonnet-5-5"]}
    return d


def dispatch_of(d):
    return next(n for n in d["nodes"] if n["kind"] == "dispatch")


def kinds_graph():
    node = {"name": "route", "kind": "dispatch", "entry": "router", "max_depth": 1, "max_children": 2, "max_parallel": 1,
            "deadline": 60, "join": "all",
            "allow": {"kinds": ["coding", "review"], "efforts": ["low"], "tools": ["Read"], "skills": [{"name": "tdd"}], "workspaces": ["ws"]},
            "budget": {"stalls": {"min": 1, "max": 10}, "spend": {"min": 0.01, "max": 1.0}}, "checks": ["gate"]}
    entry = agent("router", may_spawn=True, output_key="spawns")
    del entry["next"]
    return from_doc(doc([agent("a", output_key="x"), node, entry, scope_gate(fail="exit:fail")], ["a", "route"]))


def test_the_schema_takes_kinds_in_place_of_models():
    g = kinds_graph()
    assert dispatch_of(g.doc)["allow"].get("models") is None


def test_the_schema_refuses_an_unknown_kind():
    d = kinds_graph().doc
    dispatch_of(d)["allow"]["kinds"] = ["coding", "poetry"]
    with pytest.raises(InvalidGraph):
        from_doc(d)


def test_the_launcher_resolves_the_four_chains_and_leaves_a_closed_entry_out(tmp_path, monkeypatch):
    # Hermetic: a live subscription marker (a spent weekly window, the Claude reserve) must not close an entry here,
    # or the chain falls back to its authored spellings and the closed entry leaks back in (#678 reads the shared dir).
    monkeypatch.setattr(launch.modelreg, "_shared_dir", lambda shared=None: tmp_path / "shared")
    chains = {**CHAINS, "coding": [{"id": "claude-sonnet-5-5"}, {"id": "claude-sonnet-4-6", "on": False}]}
    got = launch.kind_chains(models_file(tmp_path, chains))
    assert got["coding"] == ["claude-sonnet-5-5"] and got["router"] == ["claude-opus-5-5"]
    assert set(got) == {"router", "coding", "review", "vision"}


def test_a_chain_whose_entries_are_all_closed_stays_as_written(tmp_path):
    chains = {**CHAINS, "vision": [{"id": "claude-sonnet-5-5", "on": False}]}
    assert launch.kind_chains(models_file(tmp_path, chains))["vision"] == ["claude-sonnet-5-5"]


def test_a_graph_with_kinds_and_no_models_file_refuses_the_start(tmp_path):
    with pytest.raises(OptionError, match="chains"):
        launch.launch(kinds_graph(), tmp_path / "r", adapter=ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}))


def test_the_launcher_records_the_chains_in_run_start_and_keeps_the_authored_hash(tmp_path):
    g = kinds_graph()
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}), models=models_file(tmp_path))
    assert eng.kind_chains["coding"] == ["claude-sonnet-5-5", "claude-sonnet-4-6"]
    assert eng.g.hash == canon_hash(json.loads((tmp_path / "r" / "graph.json").read_text()))


def test_a_request_names_a_kind_and_code_picks_the_first_model_of_its_chain():
    req, errs = validate_request(kinds_disp(), {"prompt": "go", "skill": "tdd", "kind": "review"}, 1, None, 10.0)
    assert errs == [] and req["kind"] == "review" and req["model"] == "claude-opus-5-5"


def test_a_request_with_no_kind_is_coding():
    req, errs = validate_request(kinds_disp(), {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == [] and req["kind"] == "coding" and req["model"] == "claude-sonnet-5-5"


def test_a_request_that_names_a_model_is_rejected_whole_when_the_envelope_holds_kinds():
    _, errs = validate_request(kinds_disp(), {"prompt": "go", "skill": "tdd", "model": "claude-sonnet-5-5"}, 1, None, 10.0)
    assert "model_supplied" in errs


def test_a_kind_outside_the_envelope_is_rejected():
    d = kinds_disp()
    d["allow"]["kinds"] = ["coding"]
    d["allow"]["kind_models"] = {"coding": d["allow"]["kind_models"]["coding"]}   # the engine builds kind_models from allow.kinds
    _, errs = validate_request(d, {"prompt": "go", "skill": "tdd", "kind": "review"}, 1, None, 10.0)
    assert "kind_outside_allow:review" in errs


def test_a_legacy_envelope_with_models_still_takes_a_model():
    d = route()
    d["allow"] = {k: v for k, v in d["allow"].items() if k not in ("kinds", "kind_models")} | {"models": ["claude-sonnet-5-5"]}
    req, errs = validate_request(d, {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == [] and "kind" not in req


def test_the_router_effort_comes_from_the_entry_it_answers_on(tmp_path):
    """SPEC 750: the panel draws one effort per row for every chain, so the router answers on its entry's effort,
    never on the node's."""
    chains = {**CHAINS, "router": [{"id": "claude-opus-5-5", "effort": "low"}, {"id": "claude-sonnet-5-5"}]}
    got = launch.kind_efforts(models_file(tmp_path, chains))
    assert got["router"] == {"claude-opus-5-5": "low"} and got["coding"] == {}
