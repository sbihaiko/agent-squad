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
