#!/bin/bash
# seconds: 0.34
# The board history store: host-free (no hub, no GitHub client), one file per repository, a points fact the Monitor draws, and the hub wires it.
# Fixtures and a temp file only: never the network, never the live hub state.
# usage: bash test-board-history.sh [board_history.py]
here="$(cd "$(dirname "$0")" && pwd)"; py=python3; [ -n "${SG_CHECKOUT:-}" ] && [ -x "$SG_CHECKOUT/.venv/bin/python" ] && py="$SG_CHECKOUT/.venv/bin/python"; src=${1:-$here/board_history.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/board_history.py" 2>/dev/null || { ko "board_history.py is missing"; rm -rf "$T"; exit 1; }
cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests' "$T/s/board_history.py" \
  && ko "the module imports the hub or a GitHub client" || ok "the module imports no hub and no GitHub client"

"$py" -c "import sys; sys.path.insert(0, '$T/s'); import board_history; assert 'hub' not in sys.modules" 2>/dev/null \
  && ok "the module loads with no hub" || ko "the module failed to load alone"

"$py" - "$T/s" "$T/hub-board.json" <<'PY' >"$T/out.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
board = sys.argv[2]
a = bh.history_path(board, "sbihaiko/agent-squad")
b = bh.history_path(board, "sbihaiko/squad-board")
print("path", a.name, b.name)
bh.record_points(a, 5, 1_800_000_000)
print("fact", bh.budget_fact(a, 1_800_000_001)["text"])
print("other", len(bh.load_store(b)))
PY
grep -q '^path hub-history.sbihaiko_agent-squad.jsonl hub-history.sbihaiko_squad-board.jsonl$' "$T/out.txt" \
  && ok "one history file per repository" || ko "history names: $(cat "$T/out.txt")"
grep -q "^fact GraphQL 5/" "$T/out.txt" && ok "the Monitor fact reports the counted points" || ko "fact: $(cat "$T/out.txt")"
grep -q '^other 0$' "$T/out.txt" && ok "the second repository's file holds nothing from the first" || ko "mixed: $(cat "$T/out.txt")"

grep -q "board_history" "$here/hub.py" && ok "the hub loads the module" || ko "hub.py does not load board_history"
"$py" - "$here/hubmux.py" <<'PY' && ok "the live server starts the history writer" || ko "the live server starts no history writer (hubmux read threads)"
import ast, sys
# The two reads start from one loop (#1790). The tuple may sit in the loop or in a constant it iterates, so the loop's
# iterated name is resolved to its unique binding: a stale or unrelated tuple that names both reads never answers for
# the loop that starts them, and a rebound name fails the check instead of passing it.
tree = ast.parse(open(sys.argv[1]).read())
WANT = {"refresh_board", "refresh_history"}


def tuple_names(node):
    return {e.value for e in node.elts if isinstance(e, ast.Constant)} if isinstance(node, ast.Tuple) else set()


def bound(value, targets):
    return {t.id for t in targets if isinstance(t, ast.Name)} if WANT <= tuple_names(value) else set()


holds, assigns = set(), {}
for n in ast.walk(tree):
    if isinstance(n, ast.Assign):
        holds |= bound(n.value, n.targets)
    elif isinstance(n, ast.AnnAssign) and n.value is not None:
        holds |= bound(n.value, [n.target])
for n in ast.walk(tree):     # every name bound anywhere, to count a rebinding of the one the loop reads
    targets = n.targets if isinstance(n, ast.Assign) else ([n.target] if isinstance(n, ast.AnnAssign) else [])
    for t in targets:
        if isinstance(t, ast.Name):
            assigns[t.id] = assigns.get(t.id, 0) + 1
loops = []
for n in ast.walk(tree):
    if not isinstance(n, ast.For):
        continue
    if WANT <= tuple_names(n.iter):
        loops.append(("<literal>", n))
    elif isinstance(n.iter, ast.Name) and n.iter.id in holds:
        loops.append((n.iter.id, n))
# Exactly one tuple names both reads, and the loop reads that binding alone: a rebinding makes the resolution ambiguous.
if len(holds) != 1:
    sys.exit(1)
name = next(iter(holds))
if assigns.get(name, 0) != 1 or [src for src, _ in loops] != [name]:
    sys.exit(1)
sys.exit(0)
PY
# The hub is one program: hub.py compiles its hub_theme_*.py modules into its own namespace (#2742), so the facts and
# the card are read across the entry point and every theme module.
grep -q 'budget_fact(_hist.history_path' "$here"/hub.py "$here"/hub_theme_*.py \
  && ok "the Monitor facts carry the GraphQL points" || ko "the Monitor facts do not carry the GraphQL points"
! grep -q 'instTile("GraphQL"' "$here"/hub.py "$here"/hub_theme_*.py \
  && ok "the card draws no GraphQL tile (removed by the owner)" || ko "the card draws a GraphQL tile the owner removed"

# (#1394) A record that closed before the card's last moves is re-read: the Status clock can lag the move it stamps, so a
# complete record whose last known Status is not the card's current one is stale even when both clocks agree.
"$py" - "$T/s" <<'PY' >"$T/out2.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
ev = [{"t": "2026-11-05T01:28:14Z", "type": "status", "status": "Code Complete"}]
stored = {1393: {"issue_updated": "2026-11-05T01:28:14Z", "status_updated": "2026-11-05T01:28:14Z",
                 "events": ev, "complete": True, "cursor": None}}
items = [{"n": 1393, "issue_updated": "2026-11-05T01:28:14Z", "status_updated": "2026-11-05T01:28:14Z",
          "status": "Closed", "closed": False}]
print("due", bh.due_reads(items, stored))
stored[1393]["events"] = ev + [{"t": "2026-11-05T01:28:16Z", "type": "status", "status": "Gating"},
                               {"t": "2026-11-05T01:28:41Z", "type": "status", "status": "Closed"}]
print("settled", bh.due_reads(items, stored))
PY
grep -q '^due \[1393\]$' "$T/out2.txt" \
  && ok "a complete record behind the card's current Status is re-read" || ko "a stale complete record is never re-read: $(cat "$T/out2.txt")"
grep -q '^settled \[\]$' "$T/out2.txt" \
  && ok "the re-read record is settled, so the pass does not loop" || ko "the settled record is still due: $(cat "$T/out2.txt")"

# (#1419) A close that moves no card is never read: the issue closes through the API and its card keeps its Status, so no
# clock moves. The rows below are the board's own shape: `s` is the issue's state (`.content.state`, the value the issue
# API answers), and `st` is the card's Status. A complete record that ends in Backlog is due while the issue is closed
# and the card never moved; once read it ends closed and plots its lead row. A reopen is the same rule the other way,
# and an edit that changes no state is still no reason to read.
"$py" - "$T/s" <<'PY' >"$T/out3.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
ev = [{"t": "2026-11-01T00:00:00Z", "type": "added"},
      {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"}]
base = {"issue_updated": "2026-11-01T00:00:00Z", "status_updated": "2026-11-01T00:00:00Z",
        "events": ev, "complete": True, "cursor": None}
stored = {1420: dict(base)}
row = {"n": 1420, "su": "2026-11-01T00:00:00Z", "st": "Backlog", "s": "CLOSED",
       "issue_updated": "2026-11-04T00:00:00Z"}
print("due", bh.due_items([row], stored))
print("unit", bh.due_reads([{"n": 1420, "status_updated": "2026-11-01T00:00:00Z", "status": "Backlog",
                             "closed": True}], stored))
print("edited", bh.due_items([dict(row, s="OPEN")], stored))
stored[1420]["events"] = ev + [{"t": "2026-11-02T00:00:00Z", "type": "closed"}]
print("settled", bh.due_items([row], stored))
lead = bh.board_time(bh.intervals(stored[1420]["events"]))
print("row", lead is not None, round(lead["flowing"], 3), sorted(lead["columns"].items()), lead["censored"])
reopened = {1421: dict(base, events=ev + [{"t": "2026-11-02T00:00:00Z", "type": "closed"}])}
print("reopen", bh.due_items([dict(row, n=1421, s="OPEN")], reopened))
reopened[1421]["events"] = reopened[1421]["events"] + [{"t": "2026-11-03T00:00:00Z", "type": "reopened"}]
print("resettled", bh.due_items([dict(row, n=1421, s="OPEN")], reopened))
PY
grep -q '^due \[1420\]$' "$T/out3.txt" \
  && ok "an issue closed through the API while its card keeps Status Backlog is re-read" || ko "a close that moves no card is never read: $(cat "$T/out3.txt")"
grep -q '^unit \[1420\]$' "$T/out3.txt" \
  && ok "the rule reads the item's stated closed flag" || ko "unit: $(cat "$T/out3.txt")"
grep -q '^edited \[\]$' "$T/out3.txt" \
  && ok "an edit that changes no closed state is no reason to read" || ko "an unrelated edit is a reason to read: $(cat "$T/out3.txt")"
grep -q '^settled \[\]$' "$T/out3.txt" \
  && ok "the re-read record ends closed and is settled" || ko "the closed record is still due: $(cat "$T/out3.txt")"
grep -qxF "row True 24.0 [('Backlog', 24.0)] False" "$T/out3.txt" \
  && ok "the re-read record plots its lead row from the board entry" || ko "lead row: $(cat "$T/out3.txt")"
grep -q '^reopen \[1421\]$' "$T/out3.txt" \
  && ok "an issue reopened while its record last closed is re-read" || ko "a reopen is never read: $(cat "$T/out3.txt")"
grep -q '^resettled \[\]$' "$T/out3.txt" \
  && ok "the re-read record ends reopened and is settled" || ko "the reopened record is still due: $(cat "$T/out3.txt")"

# (#1419) The production path, not a hand-built row: `hub._history_due` maps the hub's own board rows to the due set. A row
# carries the issue state under `s` (`.content.state`) and the card Status under `st` (the jq board read); the fast read adds
# `su` (the Status clock) and `iu`. So the test drives the exact mapping the bug lived in: an API-closed spec whose card
# never left Status Backlog is due even though no clock moved, an edit that changes no closed state is not, a reopen is the
# same rule the other way, and each re-read record settles.
SG_REPO=sbihaiko/agent-squad SG_OWNER=sbihaiko SG_PROJECT=5 SG_CHECKOUT="$T" \
"$py" - "$here" "$T/hub-board.json" <<'PY' >"$T/out4.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import hub
row = {"n": 1420, "fd": "I_x", "i": "PVTI_x", "t": "a spec", "s": "CLOSED", "cr": "completed",
       "c": "2026-11-04T00:00:00Z", "st": "Backlog", "k": None, "gt": None, "l": ["spec"],
       "si": {"total": 0, "completed": 0}, "o": "2026-11-01T00:00:00Z",
       "su": "2026-11-01T00:00:00Z", "iu": "2026-11-04T00:00:00Z"}
ev = [{"t": "2026-11-01T00:00:00Z", "type": "added"},
      {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"}]
base = {"issue_updated": "2026-11-01T00:00:00Z", "status_updated": "2026-11-01T00:00:00Z",
        "events": ev, "complete": True, "cursor": None}
stored = {1420: dict(base)}
print("due", hub._history_due(stored, [row]))
print("edited", hub._history_due(stored, [dict(row, s="OPEN", iu="2026-11-04T00:00:00Z")]))
stored[1420]["events"] = ev + [{"t": "2026-11-02T00:00:00Z", "type": "closed"}]
print("settled", hub._history_due(stored, [row]))
stored[1421] = dict(base, events=ev + [{"t": "2026-11-02T00:00:00Z", "type": "closed"}])
print("reopen", hub._history_due(stored, [dict(row, n=1421, s="OPEN")]))
stored[1421]["events"] = stored[1421]["events"] + [{"t": "2026-11-03T00:00:00Z", "type": "reopened"}]
print("resettled", hub._history_due(stored, [dict(row, n=1421, s="OPEN")]))
moved = dict(row, n=1422, s="OPEN", st="Review")
stored[1422] = dict(base, events=ev)
print("moved", hub._history_due(stored, [moved]))
stored[1422]["events"] = ev + [{"t": "2026-11-01T08:00:00Z", "type": "status", "status": "Review"}]
print("moved-settled", hub._history_due(stored, [moved]))
PY
grep -q '^due \[1420\]$' "$T/out4.txt" \
  && ok "the hub's own row marks an API close that moved no card as due" || ko "hub row: $(cat "$T/out4.txt")"
grep -q '^edited \[\]$' "$T/out4.txt" \
  && ok "the hub row of an unrelated edit is no read reason" || ko "hub row edit: $(cat "$T/out4.txt")"
grep -q '^settled \[\]$' "$T/out4.txt" \
  && ok "the hub row is settled once its record carries the close" || ko "hub row settled: $(cat "$T/out4.txt")"
grep -q '^reopen \[1421\]$' "$T/out4.txt" \
  && ok "the hub's own row marks a reopen of a record that last closed as due" || ko "hub row reopen: $(cat "$T/out4.txt")"
grep -q '^resettled \[\]$' "$T/out4.txt" \
  && ok "the reopened hub row settles once its record carries the reopen" || ko "hub row resettled: $(cat "$T/out4.txt")"
grep -q '^moved \[1422\]$' "$T/out4.txt" \
  && ok "a Status move the clock hides is due on the value alone" || ko "moved hub row: $(cat "$T/out4.txt")"
grep -q '^moved-settled \[\]$' "$T/out4.txt" \
  && ok "the moved hub row settles once its record carries the new Status" || ko "moved hub row settled: $(cat "$T/out4.txt")"

"$py" - "$T/s" <<'PY' >"$T/boardtime.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
# #1354: the lead-time chart measures a closed spec from its first time on the board, the parked Backlog wait included.
events = [{"t": "2026-11-01T00:00:00Z", "type": "added"}, {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"},
          {"t": "2026-11-02T00:00:00Z", "type": "status", "status": "Closed"},
          {"t": "2026-11-02T00:00:00Z", "type": "closed"}]
wait = bh.board_time(bh.intervals(events))
print("wait", round(wait["flowing"], 3), sorted(wait["columns"].items()), round(sum(wait["columns"].values()), 3))
moved = [{"t": "2026-11-01T00:00:00Z", "type": "added"}, {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"},
         {"t": "2026-11-01T06:00:00Z", "type": "status", "status": "Doing"},
         {"t": "2026-11-02T00:00:00Z", "type": "status", "status": "Closed"},
         {"t": "2026-11-02T00:00:00Z", "type": "closed"}]
span = bh.board_time(bh.intervals(moved))
print("moved", round(span["flowing"], 3), sorted(span["columns"].items()), round(sum(span["columns"].values()), 3))
print("off", bh.board_time(bh.intervals([{"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Closed"},
                                          {"t": "2026-11-01T00:00:00Z", "type": "closed"}])))
print("end", bh.board_time(bh.intervals(moved), end="Code Review"))   # the reader's end column, never replaced (#1354)
PY
grep -qxF "wait 24.0 [('Backlog', 24.0)] 24.0" "$T/boardtime.txt" \
  && ok "a closed spec's board time counts the Backlog wait" || ko "board time: $(cat "$T/boardtime.txt")"
grep -qxF "moved 24.0 [('Backlog', 6.0), ('Doing', 18.0)] 24.0" "$T/boardtime.txt" \
  && ok "the board-time stack sums the columns the card wore" || ko "board-time stack: $(cat "$T/boardtime.txt")"
grep -q "^off None$" "$T/boardtime.txt" \
  && ok "a card never on the board has no board time" || ko "off-board: $(cat "$T/boardtime.txt")"
grep -q "^end None$" "$T/boardtime.txt" \
  && ok "a card with no entry into the chosen end column has no board time" || ko "end column: $(cat "$T/boardtime.txt")"

rm -rf "$T"; exit $fail
