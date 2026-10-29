#!/bin/bash
# Guard for #815: the owner's arrow adds the card it moves. `board_pass` draws an open issue that never reached the board as a
# Backlog row (`st` null, no item id), and `board.sh` answers a move for that row `#<n> not on the board` and writes nothing,
# so the arrow failed on every card filed outside the board. The door now asks the seam for `--add` exactly when the row holds no
# Project item, and the closed refusal still comes first. A spec moves with its Backlog tickets (ADR-0142, #838); its sub-issue
# read is stubbed here, so the suite makes no network call. No server.
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
    return got, list(calls)
card = {"n": 7, "i": 123, "s": "open", "st": "Backlog", "l": [], "t": "t"}
loose = {"n": 8, "s": "open", "st": None, "l": [], "t": "t"}          # an open issue the board read never carried: no item id
spec = {"n": 9, "s": "open", "st": None, "l": ["spec"], "t": "t"}
doing = {"n": 10, "i": 124, "s": "open", "st": "Doing", "l": [], "t": "t"}
closed = {"n": 11, "i": 125, "s": "closed", "st": "Backlog", "l": [], "t": "t"}
def check(label, ok, got=""):
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got}"))
got, c = run(card)
check("a card that holds a Project item moves with the plain write", got.get("ok") and c == [([7], False)], (got, c))
got, c = run(loose)
check("a card the board read never carried is added and moved", got.get("ok") and c == [([8], True)], (got, c))
got, c = run(spec)
check("a spec with no ticket moves alone", got.get("ok") and got.get("moved") == [9] and c == [([9], True)], (got, c))
subs["answer"] = None
got, c = run(spec)
check("a spec whose ticket read fails moves nothing", "err" in got and c == [], (got, c))
subs["answer"] = []

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
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
