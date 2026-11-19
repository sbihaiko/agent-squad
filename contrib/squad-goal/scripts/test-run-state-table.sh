#!/usr/bin/env bash
# seconds: 0.03
set -euo pipefail

d="$(cd "$(dirname "$0")" && pwd)"
python3 - "$d" <<'PY'
import json
import sys
from pathlib import Path

rows = json.loads((Path(sys.argv[1]) / "run-states.json").read_text())
assert all({"state", "tone", "mark", "word"} <= row.keys() for row in rows)
assert len({row["state"] for row in rows}) == len(rows)
for tone in {row["tone"] for row in rows}:
    marks = [row["mark"] for row in rows if row["tone"] == tone]
    assert len(marks) == len(set(marks)), (tone, marks)
assert {row["mark"] for row in rows} <= {"filled", "hollow", "bar", "none"}
assert "stale" not in {row["state"] for row in rows}
print("ok shared run-state rows have unique marks per tone")
PY
