"""The subject model configuration (config/subject.yaml) and the graphs that read it.

Guards: the owner's ruling (deepseek-flash only) is the shipped file's value; a missing or
incomplete file refuses the run rather than defaulting a model; both subject graphs take every model they name from
the file, so a change of model is a change of one file.
"""
import json
from pathlib import Path

import pytest

from squad import subject

ROOT = Path(__file__).resolve().parent.parent


def cfg_file(tmp_path, **over):
    doc = {"version": 1,
           "issue": {"model": "m-issue", "effort": "eff-issue"},
           "router": {"model": "m-router", "effort": "eff-router", "child_model": "m-child", "child_effort": "eff-child"}}
    for section, keys in over.items():
        doc[section].update(keys)
    p = tmp_path / "subject.yaml"
    p.write_text(json.dumps(doc))
    return p


def test_the_shipped_config_names_the_owner_ruling():
    cfg = subject.load_config(ROOT / "config" / "subject.yaml")
    assert cfg["issue"]["model"] == "deepseek-flash"
    assert cfg["router"]["model"] == "deepseek-flash" and cfg["router"]["child_model"] == "deepseek-flash"


def test_a_missing_config_refuses(tmp_path):
    with pytest.raises(subject.Refused, match="no subject config"):
        subject.load_config(tmp_path / "absent.yaml")


def test_a_section_that_names_no_model_refuses_and_names_the_key(tmp_path):
    p = cfg_file(tmp_path, issue={"model": ""})
    with pytest.raises(subject.Refused, match="model"):
        subject.load_config(p)
    p.write_text(json.dumps({"issue": {"model": "m", "effort": "e"}}))
    with pytest.raises(subject.Refused, match="no `router` section"):
        subject.load_config(p)


def test_the_one_issue_graph_takes_every_model_from_the_config(tmp_path):
    cfg = subject.load_config(cfg_file(tmp_path))
    doc = subject.graph_doc("issues/01-x.md", ["true"], cfg)
    agents = [n for n in doc["nodes"] if n["kind"] == "agent"]
    assert agents and {n["model"] for n in agents} == {"m-issue"} and {n["effort"] for n in agents} == {"eff-issue"}


def test_the_router_graph_takes_every_model_from_the_config(tmp_path):
    cfg = subject.load_config(cfg_file(tmp_path))
    doc = json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text())
    subject.apply_router_models(doc, cfg)
    agents = [n for n in doc["nodes"] if n.get("kind") == "agent"]
    assert {n["model"] for n in agents} == {"m-router"} and {n["effort"] for n in agents} == {"eff-router"}
    disp = next(n for n in doc["nodes"] if n["kind"] == "dispatch")
    assert disp["allow"]["models"] == ["m-child"] and "eff-child" in disp["allow"]["efforts"]


def test_the_shipped_router_graph_allows_only_an_off_catalog_child_model():
    """The dispatch envelope rejects a child that asks for anything else (squad/envelope.py:47). The old allow list
    named opus and sonnet, which reach the Anthropic leg; the ruling replaces it."""
    cfg = subject.load_config(ROOT / "config" / "subject.yaml")
    doc = subject.apply_router_models(json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text()), cfg)
    disp = next(n for n in doc["nodes"] if n["kind"] == "dispatch")
    assert disp["allow"]["models"] == ["deepseek-flash"]


def test_the_spawn_example_names_the_configured_child_model(tmp_path):
    """The router reads this example literally, so the model in it is the model the children run."""
    cfg = subject.load_config(cfg_file(tmp_path))
    ex = json.loads(subject.spawn_example(cfg))["spawns"][0]
    assert (ex["model"], ex["effort"]) == ("m-child", "eff-child")


def test_the_shipped_config_sets_no_spend_cap_and_the_example_asks_for_none():
    cfg = subject.load_config(ROOT / "config" / "subject.yaml")
    assert subject.no_spend_cap(cfg)
    assert "spend" not in json.loads(subject.spawn_example(cfg))["spawns"][0]
    cfg["router"]["spend_cap"] = "on"
    assert not subject.no_spend_cap(cfg) and json.loads(subject.spawn_example(cfg))["spawns"][0]["spend"] == 1.0
