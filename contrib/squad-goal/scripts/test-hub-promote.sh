#!/bin/bash
# seconds: 0.28
# Guard for #815: the owner's arrow adds the card it moves. `board_pass` draws an open issue that never reached the board as a
# Backlog row (`st` null, no item id), and `board.sh` answers a move for that row `#<n> not on the board` and writes nothing,
# so the arrow failed on every card filed outside the board. The door now asks the seam for `--add` exactly when the row holds no
# Project item, and the closed refusal still comes first. A spec moves with its Backlog tickets (ADR-0142, #838); its sub-issue
# read is stubbed here, so the suite makes no network call. A ticket of a spec never moves alone (#829): the route asks the one
# rule the board serves as `pm`, so a card the board marks not promotable is refused before any write. No server.
# usage: bash test-hub-promote.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src
T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
calls = []
real_write = hub.promote_write
hub.promote_write = lambda issues, add=False: calls.append((issues, add)) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
hub.save_board = lambda: None
subs = {"answer": []}   # the sub-issue read a spec makes (ghread): a list answer, or a failure the hub must not act on
hub.ghread.read = lambda route, transport=None: hub.ghread.Read(kind=hub.ghread.ANSWER, answer=subs["answer"]) if subs["answer"] is not None else hub.ghread.Read(kind=hub.ghread.FAILED, detail="gh timed out")
def run(item, issue=None, specs=()):
    calls.clear(); hub.board_cache["items"] = [item]
    got = hub.promote(item["n"] if issue is None else issue)
    hub.wait_for_moves()   # the writer answers in the background (#1300): the call is read after it
    return got, list(calls)
card = {"n": 7, "i": 123, "s": "open", "st": "Backlog", "l": [], "t": "t"}
loose = {"n": 8, "s": "open", "st": None, "l": [], "t": "t"}          # an open issue the board read never carried: no item id
spec = {"n": 9, "s": "open", "st": None, "l": ["spec"], "t": "t"}
doing = {"n": 10, "i": 124, "s": "open", "st": "Doing", "l": [], "t": "t"}
closed = {"n": 11, "i": 125, "s": "closed", "st": "Backlog", "l": [], "t": "t"}
ticket = {"n": 12, "i": 126, "s": "open", "st": "Backlog", "l": [], "pa": 9, "t": "t"}      # a ticket of spec #9 (#829)
subspec = {"n": 13, "s": "open", "st": None, "l": ["spec"], "pa": 9, "t": "t"}             # a spec that a spec holds
def check(label, ok, got=""):
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got}"))
got, c = run(card)
check("a card that holds a Project item moves with the plain write", got.get("ok") and c == [([7], False)], (got, c))
got, c = run(loose)
check("a card the board read never carried is added and moved", got.get("ok") and c == [([8], True)], (got, c))
got, c = run(spec)
check("a spec with no ticket moves alone", got.get("ok") and got.get("pending") == [9] and c == [([9], True)], (got, c))
subs["answer"] = None
got, c = run(spec)
check("a spec whose ticket read fails moves nothing", "err" in got and c == [], (got, c))
subs["answer"] = []

got, c = run(ticket)
check("a ticket of a spec is refused before any write", "err" in got and "spec" in got["err"] and c == [], (got, c))
got, c = run(subspec)
check("a spec that a spec holds still moves", got.get("ok") and got.get("pending") == [13] and c == [([13], True)], (got, c))
hub.board_cache = {**hub.board_cache, "items": [ticket, dict(card, st="Backlog")]}   # the write above moved `card`; a fresh row is the board's own
rows = {i["n"]: i for g in hub.board()["groups"] for i in g["items"]}
check("the board marks no ticket promotable and names the column the move goes to",
      not rows[12].get("pm") and rows[7].get("pm") is True and rows[7].get("pmt") == hub.PROMOTE_TO, rows)
got, c = run(doing)
check("a card past Backlog is refused before any write", "err" in got and c == [], (got, c))
got, c = run(closed)
check("a closed card is refused before any write", "err" in got and c == [], (got, c))
got, c = run(card, issue="7")
check("an issue given as a string is refused", "err" in got and c == [], (got, c))
seen = {}
hub.subprocess.run = lambda argv, **kw: seen.update(env=kw.get("env") or {}) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
real_write([7])
check("the owner's arrow names the owner as its dispatch context", seen["env"].get("SG_MOVE_BY") == "owner", seen)
import json, pathlib
hub.BATCH_AUDIT = pathlib.Path(os.environ["SG_CHECKOUT"]) / "batch.jsonl"
rc = lambda code, out, err: type("R", (), {"returncode": code, "stdout": out, "stderr": err})()
batch_spec = {"n": 30, "i": 130, "s": "open", "st": "Backlog", "l": ["spec"], "t": "t"}
batch_ticket = {"n": 31, "i": 131, "s": "open", "st": "Backlog", "l": [], "pa": 30, "t": "t"}
def batch(confirm):
    hub.board_cache["items"] = [dict(batch_spec), dict(batch_ticket)]
    subs["answer"] = [{"number": 31, "state": "open", "labels": []}]
    calls.clear()
    got = hub.promote_batch({"batch": True, "spec": 30, "confirm": confirm}, "owner")
    hub.wait_for_moves()
    return got
hub.promote_write = lambda issues, add=False: calls.append((issues, add)) or rc(0, "", "")
got = batch(2)
check("a confirmed spec batch moves the spec and its ticket in one write", got == {"ok": True, "count": 2, "pending": [30, 31]} and calls == [([30, 31], False)], (got, calls))
got = batch(5)
check("a spec batch with a changed count moves nothing", got.get("count") == 2 and "err" in got and calls == [], (got, calls))
hub.promote_write = lambda issues, add=False: calls.append((issues, add)) or rc(1, "#30 -> Todo\n", "#31: refused\n")
got = batch(2)
check("a spec batch that fails part way names the cards that moved and those that did not",
      got.get("pending") == [30, 31] and "refused" in hub.MOVE_REFUSED.get(31, "") and 30 not in hub.MOVE_REFUSED, (got, calls))
lines = [json.loads(x) for x in hub.BATCH_AUDIT.read_text().splitlines()]
check("each confirmed spec batch writes one audit line with its outcome",
      [x["outcome"] for x in lines] == ["moved", "refused", "partial"] and all(x["scope"] == 30 for x in lines), lines)
# #1909: the owner's move of a spec carries its Backlog ticket in the same write, and that write is the owner's.
spec_b = {"n": 40, "i": 140, "s": "open", "st": None, "l": ["spec"], "t": "t"}
ticket_b = {"n": 41, "i": 141, "s": "open", "st": "Backlog", "l": [], "pa": 40, "t": "t"}
hub.board_cache["items"] = [dict(spec_b), dict(ticket_b)]
subs["answer"] = [{"number": 41, "state": "open", "labels": []}]
calls.clear()
got = hub.promote(40)
hub.wait_for_moves()
check("a spec with a Backlog ticket moves with it in one write", got.get("pending") == [40, 41] and [c[0] for c in calls] == [[40, 41]], (got, calls))
seen2 = {}
hub.subprocess.run = lambda argv, **kw: seen2.update(env=kw.get("env") or {}, argv=argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
real_write([40, 41])
check("the write carries the spec and its ticket, by owner", seen2["env"].get("SG_MOVE_BY") == "owner" and seen2["argv"][-2:] == ["40", "41"], seen2)
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
