#!/usr/bin/env python3
"""Restamp docs/design/router.record.jsonl to the graph it ships beside (#95).

The record's `run_start` carries the graph hash `check_record.py` enforces, so a graph edit and the restamp of the
record are one change. An author runs this by hand after editing the graph: nothing in the hub calls it any more, since
a Save edits `models.json` and never the graph (SPEC 750).

usage: stamp_router_record.py [--check]   --check writes nothing and exits 1 when the record is stale.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from squad.graph import canon_hash  # noqa: E402

GRAPH = ROOT / "docs" / "design" / "router.graph.json"
RECORD = ROOT / "docs" / "design" / "router.record.jsonl"


def main():
    doc = json.loads(GRAPH.read_text())
    want = canon_hash(doc)
    out, changed = [], 0
    for line in RECORD.read_text().splitlines():
        if not line.strip():
            out.append(line)
            continue
        d = json.loads(line)
        if d.get("event") == "run_start" and d.get("graph_hash") != want:
            d["graph_hash"] = want
            changed += 1
        out.append(json.dumps(d, ensure_ascii=False))
    if "--check" in sys.argv:
        print("stale" if changed else "current")
        return 1 if changed else 0
    if changed:
        RECORD.write_text("\n".join(out) + "\n")
    print(f"{'restamped' if changed else 'current'}: {want[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
