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

"$py" - "$T/s" <<'PY' >"$T/out2.txt" 2>&1
import datetime, sys
sys.path.insert(0, sys.argv[1])
from board_burndown import burndown, summary

def at(day, hour=12):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"

def epoch(day):
    return datetime.datetime(2026, 10, day, 12, tzinfo=datetime.timezone.utc).timestamp()

store = {
    2: {"events": [{"t": at(20), "type": "spec", "spec": 1045}, {"t": at(21), "type": "closed"}]},
    3: {"events": [{"t": at(22), "type": "spec", "spec": 1045}]},
    4: {"events": [{"t": at(20), "type": "spec", "spec": 1045}, {"t": at(23), "type": "spec", "spec": None}]},
}
r = burndown(store, [epoch(20), epoch(21), epoch(23)], 1045)
bad = []

def check(name, got, want):
    if got != want: bad.append(f"{name}: got {got!r}, want {want!r}")

check("a point names the tickets that closed and joined its day",
      [(p["day"], p["closed_tickets"], p["added_tickets"]) for p in r["points"]],
      [("2026-10-20", [], [2, 4]), ("2026-10-21", [2], []), ("2026-10-23", [], [3])])
s = summary(r, datetime.date(2026, 10, 23))
check("remaining today is the last whole read", (s["remaining"], s["as_of"]), (1, "2026-10-23"))
check("closed this week", s["closed_week"], 1)
check("added this week", s["added_week"], 3)
check("trailing guide runs from today to zero at the trailing rate",
      s["guide"], {"source": "trailing", "from": {"day": "2026-10-23", "remaining": 1}, "to": {"day": "2026-10-30", "remaining": 0}})
f = summary(r, datetime.date(2026, 10, 23), forecast=datetime.date(2026, 11, 15))
check("a forecast date replaces the trailing rate", (f["guide"]["source"], f["guide"]["to"]["day"]), ("forecast", "2026-11-15"))
quiet = summary(r, datetime.date(2026, 11, 10))
check("no forecast and no close in the week: no guide, and a reason", (quiet["guide"], quiet["guide_reason"] is None), (None, False))
check("a spec with no child says so", summary(burndown({}, [epoch(20)], 1045), datetime.date(2026, 10, 23))["reason"], "no child ticket")
done = burndown({2: {"events": [{"t": at(20), "type": "spec", "spec": 1045}, {"t": at(21), "type": "closed"}]}},
                [epoch(20), epoch(21)], 1045)
check("every child closed ends the line at zero", [p["remaining"] for p in done["points"]], [1, 0])
check("every child closed has its own guide state", summary(done, datetime.date(2026, 10, 21))["guide"]["source"], "done")
print("\n".join(bad) if bad else "all green")
PY
grep -q '^all green$' "$T/out2.txt" && ok "the numbers under the chart agree with the line" || { ko "burndown summary: $(cat "$T/out2.txt")"; }

rm -rf "$T"; exit $fail
