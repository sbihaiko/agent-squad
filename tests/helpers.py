import copy
import json
from pathlib import Path

DESIGN = Path(__file__).resolve().parent.parent / "docs" / "design"


def load_doc(name):
    return json.loads((DESIGN / f"{name}.graph.json").read_text())


def five():
    return copy.deepcopy(load_doc("five-phases"))


def router():
    return copy.deepcopy(load_doc("router"))


def node(doc, name):
    return next(n for n in doc["nodes"] if n["name"] == name)


# The chains a launcher reads from models.json (SPEC 750). The envelope names kinds, and the engine synthesizes
# `kind_models` from these before it checks a request; a test that calls `validate_request` directly needs the same
# shape, so `route()` hands it the chains instead of a model list.
KIND_CHAINS = {
    "router": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-4-6"],
    "coding": ["claude-grok-4.7", "claude-deepseek-flash[1m]", "claude-opus-5-5", "claude-gpt-5.6-terra",
               "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-haiku-5-5"],
    "review": ["claude-opus-5-5"],
    "vision": ["claude-sonnet-5-5"],
    "probe": ["claude-grok-4.7", "claude-deepseek-flash[1m]", "claude-haiku-5-5"],
}


def route(spend_max=4):
    """The shipped dispatch node with a child spend the test owns, so a remainder the test passes still funds a child
    when the shipped graph's own number moves (ADR-0018: re-anchor, never retune). Its envelope carries the kind chains
    a launcher would record, because the authored envelope names kinds and no model."""
    d = node(router(), "route")
    d["budget"]["spend"]["max"] = spend_max
    if "kinds" in d["allow"]:
        d["allow"]["kind_models"] = {k: list(KIND_CHAINS[k]) for k in d["allow"]["kinds"]}
        d["allow"]["models"] = list(dict.fromkeys(m for k in d["allow"]["kinds"] for m in KIND_CHAINS[k]))
    return d
