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


def route(spend_max=4):
    """The shipped dispatch node with a child spend the test owns, so a remainder the test passes still funds a child
    when the shipped graph's own number moves (ADR-0018: re-anchor, never retune)."""
    d = node(router(), "route")
    d["budget"]["spend"]["max"] = spend_max
    return d
