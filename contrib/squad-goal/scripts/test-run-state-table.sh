#!/usr/bin/env bash
set -euo pipefail

: "${SG_CHECKOUT:?set SG_CHECKOUT to the checkout containing .venv}"
"$SG_CHECKOUT/.venv/bin/python" - <<'PY'
import json
from pathlib import Path

root = Path.cwd()
rows = json.loads((root / "config/run-states.json").read_text())
assert all({"state", "tone", "mark", "word"} <= row.keys() for row in rows)
assert len({row["state"] for row in rows}) == len(rows)
for tone in {row["tone"] for row in rows}:
    marks = [row["mark"] for row in rows if row["tone"] == tone]
    assert len(marks) == len(set(marks)), (tone, marks)
assert {row["mark"] for row in rows} <= {"filled", "hollow", "bar", "none"}
assert "stale" not in {row["state"] for row in rows}
print("ok shared run-state rows have unique marks per tone")
PY
