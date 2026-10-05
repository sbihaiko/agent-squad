"""Artifacts: what a node leaves for code to read (ADR-0102 rule 1, rule 8). One JSON file per entry.

Context travels between nodes only through artifacts. A node's outcome derives from its artifact.
"""
import json
from pathlib import Path

from squad.record import atomic_write


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.by_node, self.by_key = {}, {}

    def path_for(self, node, item, entry):
        tag = f".i{item}" if item is not None else ""
        return f"artifacts/{node}{tag}.{entry}.json"

    def put(self, node, item, entry, payload, key=None, base=None):
        """Write one artifact file and index it. Return its path relative to the run directory."""
        rel = self.path_for(node, item, entry)
        atomic_write((base or self.root.parent) / rel, json.dumps(payload, indent=1, sort_keys=True))
        self.index(node, item, payload, key)
        return rel

    def index(self, node, item, payload, key=None):
        self.by_node[(node, item)] = payload
        if key and isinstance(payload, dict) and key in payload:
            self.by_key[(key, item)] = payload[key]

    def load(self, rel, node, item, key=None, base=None):
        """Read an artifact named by an outcome event. Return False when the file is missing."""
        p = (base or self.root.parent) / rel
        if not p.exists():
            return False
        self.index(node, item, json.loads(p.read_text()), key)
        return True

    def get(self, name, item=None):
        for scope in (item, None):
            for table in (self.by_key, self.by_node):
                if (name, scope) in table:
                    return table[(name, scope)]
        return None

    def view(self, item=None):
        """Every artifact visible in an item scope, by key and by node name."""
        out = {}
        for table in (self.by_node, self.by_key):
            for (name, scope), v in table.items():
                if scope is None:
                    out[name] = v
        for table in (self.by_node, self.by_key):
            for (name, scope), v in table.items():
                if scope == item and item is not None:
                    out[name] = v
        return out
