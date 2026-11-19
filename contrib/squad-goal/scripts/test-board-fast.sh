#!/bin/bash
# seconds: 0.18
# The fast read of the open cards (SPEC 968): host-free (no hub, no GitHub client), over recorded answers only.
# Fixtures and a temp dir only: never the network, never the live hub state.
# usage: bash test-board-fast.sh [board_fast.py]
here="$(cd "$(dirname "$0")" && pwd)"; py=python3; [ -n "${SG_CHECKOUT:-}" ] && [ -x "$SG_CHECKOUT/.venv/bin/python" ] && py="$SG_CHECKOUT/.venv/bin/python"; src=${1:-$here/board_fast.py}; T=$(mktemp -d)
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
fail=0
mkdir -p "$T/s"; cp "$src" "$T/s/board_fast.py" 2>/dev/null || { ko "board_fast.py is missing"; rm -rf "$T"; exit 1; }

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests' "$T/s/board_fast.py" \
  && ko "the module imports the hub or a GitHub client" || ok "the module imports no hub and no GitHub client"

grep -q 'node(id' "$T/s/board_fast.py" && ko "the query passes an id to node()" || ok "the query never passes an id to node()"

"$py" - "$T/s" <<'PY' >"$T/out.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_fast as bf

def node(n, status, fd, state="OPEN", reason=None, repo="x/y"):
    return {"updatedAt": "u", "isArchived": False,
            "fieldValueByName": {"name": status, "updatedAt": "su"} if status else None,
            "content": {"number": n, "fullDatabaseId": str(fd), "state": state, "stateReason": reason,
                        "repository": {"nameWithOwner": repo}, "labels": {"totalCount": 0, "nodes": []}}}

def page(nodes, more=None, cost=2):
    return {"data": {"rateLimit": {"cost": cost}, "user": {"projectV2": {"items": {"pageInfo": {"hasNextPage": bool(more), "endCursor": more}, "nodes": nodes}}}}}

calls = []
answers = [page([node(11, "Doing", 911)], more="c1"), page([node(12, "Todo", 912)])]
def gql(q, v, timeout=None):
    calls.append(v)
    return answers.pop(0)
nodes = bf.read_snapshot(gql, "user", "x", 4, ("Closed", "Abandoned"), bf.Budget(100, clock=lambda: 0), deadline=10**12, clock=lambda: 0)
print("pages", len(calls), "cards", len(nodes))
print("filter", calls[0]["q"])
rows = bf.rows_of(nodes, "x/y")
print("rows", [(r["fd"], r["st"]) for r in rows])
held = [{"n": 11, "fd": 911, "st": "Todo", "s": "open", "ot": 1}, {"n": 13, "fd": 913, "st": "Todo", "s": "open", "ot": 1}]
out = bf.apply(held, rows, 50)
print("applied", [(i["n"], i.get("st"), i["ot"]) for i in out])

spent = []
def failing(q, v, timeout=None):
    spent.append(1)
    raise RuntimeError("HTTP 502")
hour = bf.Budget(1, clock=lambda: 0)
try: bf.read_snapshot(failing, "user", "x", 4, ("Closed",), hour, deadline=10**12, clock=lambda: 0)
except bf.SnapshotError as e: print("failed", str(e)[:40])
try: bf.read_snapshot(failing, "user", "x", 4, ("Closed",), hour, deadline=10**12, clock=lambda: 0)
except bf.SnapshotError as e: print("spent", str(e))
print("calls", len(spent))
PY

grep -q '^pages 2 cards 2$' "$T/out.txt" && ok "every page is read and the cards of all pages are kept" || ko "pages: $(cat "$T/out.txt")"
grep -q "^filter -status:Closed -status:Abandoned$" "$T/out.txt" && ok "the read filters out the closed statuses" || ko "filter: $(cat "$T/out.txt")"
grep -q "^rows \[(911, 'Doing'), (912, 'Todo')\]$" "$T/out.txt" && ok "rows are keyed by the database id" || ko "rows: $(cat "$T/out.txt")"
grep -q "^applied \[(11, 'Doing', 50), (13, 'Todo', 1)\]$" "$T/out.txt" && ok "a held card takes the snapshot's inputs, an absent card keeps its row" || ko "applied: $(cat "$T/out.txt")"
grep -q "^failed page failed: HTTP 502$" "$T/out.txt" && ok "a failed page raises, so the snapshot is discarded" || ko "failed: $(cat "$T/out.txt")"
grep -q "^spent fast read budget spent for this hour$" "$T/out.txt" && ok "a failed call spends the hour, so the next read waits" || ko "spent: $(cat "$T/out.txt")"
grep -q "^calls 1$" "$T/out.txt" && ok "no call is sent once the hour is spent" || ko "calls: $(cat "$T/out.txt")"

[ -s "$T/out.txt" ] && grep -q "Traceback" "$T/out.txt" && { ko "the module raised: $(tail -3 "$T/out.txt")"; }
rm -rf "$T"
[ "$fail" = 0 ] && { echo "test-board-fast: pass"; exit 0; } || { echo "test-board-fast: FAIL"; exit 1; }
