"""Guard: every shipped graph's dispatch node takes its models from `allow.kinds`, never `allow.models` (#870).

SPEC 750 / ADR-0113. A legacy model list is a second source of truth beside the registry: the hub picks a coding chain
head, the list refuses it, and every dispatch fails. The code keeps the legacy path; no shipped graph may use it.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = sorted([*ROOT.glob("docs/**/*.graph.json"), ROOT / "five-phases.graph.json"])


def dispatch_nodes(doc):
    return [n for n in doc.get("nodes") or [] if n.get("kind") == "dispatch"]


def test_shipped_graphs_are_found():
    names = {p.name for p in SHIPPED}
    assert {"router.graph.json", "spec-to-build.graph.json"} <= names


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_dispatch_node_uses_allow_kinds_not_allow_models(path):
    doc = json.loads(path.read_text())
    for node in dispatch_nodes(doc):
        allow = node.get("allow") or {}
        assert "models" not in allow, f"{path.name}: dispatch {node['name']!r} has a legacy allow.models"
        assert "kinds" in allow, f"{path.name}: dispatch {node['name']!r} has no allow.kinds"
