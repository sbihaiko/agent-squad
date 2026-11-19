#!/bin/bash
# seconds: 1.03
# The real-project check of a card's Testing path (SPEC 1218, #1258): board_gate_check.py reads the live board cache, the
# history store, the attempt journal, the gate's own log and the proved moves, and answers one line per rule.
# Fixtures shaped like those stores, a temp dir, no gh and no network: the check is proven here, and its live run is the
# same read against the profile's own files. A fixture that breaks one rule must fail exactly that rule.
# usage: bash test-board-gate-check.sh [board_gate_check.py]
here="$(cd "$(dirname "$0")" && pwd)"; src=${1:-$here/board_gate_check.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
py=${SG_TEST_PYTHON:-python3}
mkdir -p "$T/st"
case=9999

# fixture <dir> [scenario]: the five stores of one disposable card, shaped as the live loop writes them. The scenarios
# break one rule each: tries, nogating, lateexit, nomatch, open, backward, absent.
fixture() {
  "$py" - "$1" "${2:-good}" "$case" <<'PY'
import json, sys
from datetime import datetime, timedelta, UTC
out, scenario, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
t0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
def at(m): return (t0 + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
def ep(m): return int((t0 + timedelta(minutes=m)).timestamp())
sha1, sha2 = "a" * 40, "b" * 40
events = [{"t": at(0), "type": "added"}, {"t": at(0), "type": "status", "status": "Todo"},
          {"t": at(10), "type": "status", "status": "Doing"},
          {"t": at(20), "type": "status", "status": "Code Complete"}]
if scenario not in ("nogating", "reopen"): events.append({"t": at(75), "type": "status", "status": "Testing"})
done = 130 if scenario == "lateexit" else 100   # the card's last column change, and so its Testing exit
if scenario == "open":
    events += [{"t": at(90), "type": "status", "status": "Testing"}]
elif scenario == "reopen":   # the card landed, the issue was reopened, and its second gate wrote no Testing stretch
    events += [{"t": at(40), "type": "status", "status": "Closed"}, {"t": at(50), "type": "status", "status": "Todo"},
               {"t": at(60), "type": "status", "status": "Code Complete"}]
else:
    events += [{"t": at(done), "type": "status", "status": "Closed"}, {"t": at(done), "type": "closed"}]
rec = {"k": "issue", "n": n, "events": events, "complete": True, "issue_updated": at(100),
       "status_updated": at(100), "t": ep(100), "keys": None}
open(out + "/hist.jsonl", "w").write(json.dumps(rec) + "\n")
item = {"n": n, "r": "o/r", "st": "Closed", "s": "closed", "cr": "completed", "l": [],
        "gt": 1 if scenario == "tries" else 2, "c": at(done), "o": at(0), "t": "disposable card"}
if scenario == "open": item.update(st="Testing", s="open", c=None)
board = {"t": ep(100), "items": [] if scenario == "absent" else [item]}
json.dump(board, open(out + "/board.json", "w"))
rows = [{"k": "gate_attempt", "n": n, "tip": sha1, "ord": 1, "aid": "gate-1", "v": "fail", "t": ep(80), "w": "wave-9"},
        {"k": "gate_attempt", "n": n, "tip": sha2, "ord": 1, "aid": "gate-2", "v": "pass", "t": ep(95), "w": "wave-9"}]
if scenario == "wave0":   # the tip gate journals card 0 for the wave: the guard reads it on the cards the journal names
    rows.append({"k": "gate_attempt", "n": 0, "tip": sha2, "ord": 1, "aid": "gate-0", "v": "pass", "t": ep(40), "w": "wave-9"})
open(out + "/journal.jsonl", "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
# The gate's own log. A `nomatch` fixture answers no attempt: the lines name another tip.
log_sha1, log_sha2 = ("c" * 40, "d" * 40) if scenario == "nomatch" else (sha1, sha2)
open(out + "/gate-history", "w").write(
    f"{ep(80)}\tfast\tfail\t{log_sha1}\t{ep(70)}\t\t\tfail\t-\t-\t-\t2\t-\n"
    f"{ep(95)}\tfast\tpass\t{log_sha2}\t{ep(85)}\t\t\tpass\t-\t-\t-\t2\t-\n")
moves = [{"issue": n, "item": "I1", "from": "Code Complete", "to": "Testing", "at": at(75), "repo": "o/r"},
         {"issue": n, "item": "I1", "from": "Testing", "to": "Closed", "at": at(done), "repo": "o/r"}]
if scenario == "backward":
    moves.append({"issue": 8888, "item": "I9", "from": "Closed", "to": "Doing", "at": at(101), "repo": "o/r"})
open(out + "/transitions.jsonl", "w").write("\n".join(json.dumps(m) for m in moves) + "\n")
PY
}

run() { "$py" "$src" --board "$T/st/board.json" --history "$T/st/hist.jsonl" --journal "$T/st/journal.jsonl" \
  --gate-log "$T/st/gate-history" --transitions "$T/st/transitions.jsonl" --repo o/r --issue "$case" --wave wave-9 "$@" 2>&1; }

# --- the whole path passes, and the reader prints the six rules ---
fixture "$T/st" good; o=$(run); rc=$?
[ "$rc" = 0 ] && ok "a card through Todo, Doing, Code Complete, Testing and Closed passes" || ko "good run exit $rc: $o"
for rule in path gating_live gate_tries timing ended_closed no_backward_movement; do
  printf '%s\n' "$o" | /usr/bin/grep -q "^ok   $rule" || ko "the reader does not report $rule: $o"
done
printf '%s\n' "$o" | /usr/bin/grep -q '^gating check #9999: pass$' || ko "no pass verdict: $o"
printf '%s\n' "$o" | /usr/bin/grep -q 'Gate Tries\|the board reads 2' && ok "the reader names the live Gate Tries count" || ko "no Gate Tries reading: $o"
printf '%s\n' "$o" | /usr/bin/grep -q 'gate time 0.4167h' && ok "the reader reports gate time from the history and the chart" || ko "no gate time reading: $o"

# --- the live Gate Tries disagreeing with the journal fails only that rule ---
fixture "$T/st" tries; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL gate_tries' && ok "a live count that is not the journal's count fails" || ko "tries exit $rc: $o"

# --- no Testing interval: the path never reached Testing ---
fixture "$T/st" nogating; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL path' && ok "a path with no Testing stretch fails" || ko "nogating exit $rc: $o"

# --- the card left Testing after the settle bound past the verdict ---
fixture "$T/st" lateexit; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL timing' && ok "an exit instant outside the gate's own log fails" || ko "lateexit exit $rc: $o"

# --- the gate's own log answers no attempt ---
fixture "$T/st" nomatch; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL timing' && ok "an attempt the gate's log does not answer fails" || ko "nomatch exit $rc: $o"

# --- the card did not end Closed ---
fixture "$T/st" open; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL ended_closed' && ok "a card still in Testing fails ended_closed" || ko "open exit $rc: $o"

# --- a proved backward move on any card fails the run ---
fixture "$T/st" backward; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL no_backward_movement' && ok "a backward move on the board fails" || ko "backward exit $rc: $o"
printf '%s\n' "$o" | /usr/bin/grep -q '#8888 Closed->Doing' && ok "the backward move is named" || ko "the backward move is not named: $o"

# --- a card the live board does not carry is a failure, never a pass ---
fixture "$T/st" absent; o=$(run); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q '^FAIL ended_closed' && ok "a card absent from the live board fails" || ko "absent exit $rc: $o"

# --- an unreadable board cache is exit 2, never a pass ---
printf 'not json' > "$T/st/board.json"; o=$(run); rc=$?
[ "$rc" = 2 ] && ok "an unreadable board cache exits 2" || ko "unreadable board exit $rc: $o"

# --- a store without the card is exit 2: nothing was measured ---
fixture "$T/st" good; printf '%s\n' '{"k":"issue","n":1,"events":[],"complete":true}' > "$T/st/hist.jsonl"
o=$(run); rc=$?
[ "$rc" = 2 ] && ok "a history store without the card exits 2" || ko "missing record exit $rc: $o"

# --- the JSON form carries the same verdict ---
fixture "$T/st" good; o=$(run --json); rc=$?
[ "$rc" = 0 ] && printf '%s\n' "$o" | "$py" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["ok"] and len(d["checks"])==6 else 1)' \
  && ok "the JSON form reports six green checks" || ko "json: $o"

# --- the record guard (#1430): every attempt ahead of dev must leave a Testing stretch, read from the journal and the store ---
guard() { "$py" "$src" --record-guard --history "$T/st/hist.jsonl" --journal "$T/st/journal.jsonl" "$@" 2>&1; }
fixture "$T/st" good; o=$(guard); rc=$?
[ "$rc" = 0 ] && ok "the record guard passes when every attempt's card read Testing" || ko "record guard exit $rc: $o"
fixture "$T/st" nogating; o=$(guard); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q "^FAIL gating_recorded: #$case" \
  && ok "the record guard names an attempt whose card never read Testing" || ko "record guard nogating exit $rc: $o"
fixture "$T/st" nogating; o2=$(guard); [ "$o" = "$o2" ] && ok "the record guard answers the same on a re-read" || ko "record guard reread: $o vs $o2"
# A wave-level attempt (the tip gate, card 0) is read on the cards the journal names for its wave, never waived.
fixture "$T/st" wave0; o=$(guard); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q "^FAIL gating_recorded: #$case" \
  && ok "the record guard reads a card-0 wave attempt on the wave's own cards" || ko "record guard wave0 exit $rc: $o"
# A Closed interval that ended before the attempt does not say the card is in dev: a reopened card is still a defect.
fixture "$T/st" reopen; o=$(guard); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$o" | /usr/bin/grep -q "^FAIL gating_recorded: #$case" \
  && ok "the record guard names an attempt after the card left Closed" || ko "record guard reopen exit $rc: $o"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
