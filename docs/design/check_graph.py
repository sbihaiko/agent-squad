"""Validate a graph document with the engine's validator (squad.graph). Kept as a command-line door.

Usage: python3 docs/design/check_graph.py docs/design/graph.schema.json docs/design/<graph>.json
The schema argument is ignored: the engine reads docs/design/graph.schema.json itself.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from squad.graph import validate

if __name__ == "__main__":
    errs = validate(json.load(open(sys.argv[2])))
    print("\n".join(str(e) for e in errs) if errs else "OK")
    sys.exit(1 if errs else 0)
