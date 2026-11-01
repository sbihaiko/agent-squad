#!/bin/bash
# The burndown count: host-free (no hub, no GitHub client), one point per day off the parent relation, a gap for a day with no whole read (#1047).
# Fixtures and a temp file only: never the network, never the live hub state.
# usage: bash test-board-burndown.sh [board_burndown.py]
here="$(cd "$(dirname "$0")" && pwd)"; py=python3; [ -n "${SG_CHECKOUT:-}" ] && [ -x "$SG_CHECKOUT/.venv/bin/python" ] && py="$SG_CHECKOUT/.venv/bin/python"; src=${1:-$here/board_burndown.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/board_burndown.py" 2>/dev/null || { ko "board_burndown.py is missing"; rm -rf "$T"; exit 1; }
cp "$here/board_columns.py" "$T/s/"

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests' "$T/s/board_burndown.py" \
  && ko "the module imports the hub or a GitHub client" || ok "the module imports no hub and no GitHub client"

"$py" -c "import sys; sys.path.insert(0, '$T/s'); import board_burndown; assert 'hub' not in sys.modules" 2>/dev/null \
  && ok "the module loads with no hub" || ko "the module failed to load alone"

"$py" - "$T/s" <<'PY' >"$T/out.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
from board_burndown import burndown

def at(day, hour=12):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"

def epoch(day):
    import datetime
    return datetime.datetime(2026, 10, day, 12, tzinfo=datetime.timezone.utc).timestamp()

store = {
    1: {"title": "[SPEC 1045] named, not a child", "events": [{"t": at(21), "type": "closed"}]},
    2: {"title": "a child", "events": [{"t": at(20), "type": "spec", "spec": 1045}, {"t": at(21), "type": "closed"}]},
    3: {"title": "a late child", "events": [{"t": at(22), "type": "spec", "spec": 1045}]},
    4: {"title": "a child that leaves", "events": [{"t": at(20), "type": "spec", "spec": 1045}, {"t": at(23), "type": "spec", "spec": None}]},
}
r = burndown(store, [epoch(20), epoch(21), epoch(23)], 1045)
print("series", [(p["day"], p["remaining"], p["closed"], p["added"]) for p in r["points"]])
print("gaps", r["gaps"])
print("reason", r["reason"])
print("empty", burndown({1: {"title": "other", "events": [{"t": at(20), "type": "spec", "spec": 7}]}}, [epoch(20)], 1045))
PY
grep -q "^series \[('2026-10-20', 2, 0, 2), ('2026-10-21', 1, 1, 0), ('2026-10-23', 1, 0, 1)\]$" "$T/out.txt" \
  && ok "the series counts the children of the parent relation and no titled stranger" \
  || ko "series: $(cat "$T/out.txt")"
grep -q "^gaps \['2026-10-22'\]$" "$T/out.txt" && ok "the day with no whole read is a gap" || ko "gaps: $(cat "$T/out.txt")"
grep -q "^reason None$" "$T/out.txt" && ok "a series with points has no reason" || ko "reason: $(cat "$T/out.txt")"
grep -q "^empty {'spec': 1045, 'start': None, 'points': \[\], 'gaps': \[\], 'reason': 'no child ticket'}$" "$T/out.txt" \
  && ok "a spec with no child answers an empty series with a reason" || ko "empty: $(cat "$T/out.txt")"

rm -rf "$T"; exit $fail
