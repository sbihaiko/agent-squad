#!/bin/bash
# seconds: 9.36
# (#1735) The hang a deadline case starts sleeps a length only this run names, so the survivor probe and the cleanup
# below read this suite's own processes: `pgrep -f "sleep 2718"` read every suite on the machine, and two gates side
# by side then read each other's hang as a survivor, or ended it.
export HANG_NAP=$(( 271800000 + $$ ))
# Regression test for the gate-attempt journal (#1255, ADR-0170): every gate invocation that draws a verdict appends one
# `gate_attempt` record to the flow-sample journal -- the card, the wave tip, the ordinal, the verdict and the instant --
# and a memoized green verdict (ADR-0168) is a `gate_reuse` marker, never an attempt. A deadline stop is an `unproven`
# attempt like any other.
# usage: bash test-gate-attempts.sh [scripts dir]
unset SG_SERIAL_HELD SG_GATE_CARD SG_GATE_JOURNAL SG_OWNER SG_PROJECT   # the cases set their own; no ambient loop state decides them
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st; mkdir -p "$SG_STATE"
# The gate takes its worker slots from a throwaway budget, never the machine's live one (#1166).
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2
git init -q "$T/w"; $G -C "$T/w" commit -q --allow-empty -m one
J=$T/journal.jsonl; export SG_GATE_JOURNAL=$J SG_GATE_CARD=1255
run() { (cd "$T/w" && SG_GATE_KIND="${KIND:-fast}" SG_GATE_BOUND_SECONDS="${BOUND:-900}" bash "$d/gate.sh" "$@"); }
# These read the journal the way its own module does; a record's fields are checked by the keys the issue names.
q() { python3 - "$@" <<'P'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
recs = [json.loads(l) for l in open(path) if l.strip()] if __import__("os").path.exists(path) else []
if cmd == "kinds":
    print(" ".join(r.get("k", "-") for r in recs))
elif cmd == "attempts":
    a = [r for r in recs if r.get("k") == "gate_attempt"]
    print(len(a))
elif cmd == "field":   # one field of the Nth attempt (1-based)
    a = [r for r in recs if r.get("k") == "gate_attempt"]
    print(a[int(sys.argv[3]) - 1].get(sys.argv[4], "-"))
elif cmd == "reuses":
    print(sum(1 for r in recs if r.get("k") == "gate_reuse"))
P
}
count() { q "$J" attempts; }

# --- one attempt adds one: a pass and a red on the same tip are two attempts, the ordinals 1 and 2 ---
run true; rc=$?
[ $rc = 0 ] && [ "$(count)" = 1 ] && ok "a gate with a card journals one attempt" || ko "first attempt: rc $rc, $(count)"
[ "$(q "$J" field 1 tip)" = "$($G -C "$T/w" rev-parse HEAD)" ] && [ "$(q "$J" field 1 v)" = pass ] && [ "$(q "$J" field 1 ord)" = 1 ] \
  && ok "the attempt carries the card, the tip, the ordinal and the verdict" || ko "first record: $(cat "$J" 2>&1)"
[ "$(q "$J" field 1 n)" = 1255 ] && ok "the attempt names its card" || ko "card: $(cat "$J" 2>&1)"
run sh -c 'exit 1'; rc=$?
[ $rc = 1 ] && [ "$(count)" = 2 ] && [ "$(q "$J" field 2 v)" = fail ] && [ "$(q "$J" field 2 ord)" = 2 ] \
  && ok "a second gate on the same tip adds one attempt, ordinal 2" || ko "second attempt: rc $rc, $(cat "$J" 2>&1)"

# --- two attempts on different tips are distinguishable: the new tip starts its own ordinal ---
$G -C "$T/w" commit -q --allow-empty -m two; sha2=$($G -C "$T/w" rev-parse HEAD)
run true; rc=$?
[ $rc = 0 ] && [ "$(count)" = 3 ] && [ "$(q "$J" field 3 tip)" = "$sha2" ] && [ "$(q "$J" field 3 ord)" = 1 ] \
  && ok "an attempt on a new tip is its own record, ordinal 1" || ko "new tip: rc $rc, $(cat "$J" 2>&1)"

# --- a deadline stop counts as unproven ---
BOUND=1 run sh -c "echo \$\$ > '$T/sleeper.pid'; exec sleep $HANG_NAP"; rc=$?
[ $rc = 4 ] && [ "$(count)" = 4 ] && [ "$(q "$J" field 4 v)" = unproven ] \
  && ok "a deadline stop is journaled as an unproven attempt" || ko "deadline attempt: rc $rc, $(cat "$J" 2>&1)"
# The gate's stop kills the command's tree at the deadline; this backstop names the one recorded pid,
# never a pattern that could match an unrelated process on a shared machine.
if [ -f "$T/sleeper.pid" ]; then kill "$(cat "$T/sleeper.pid")" 2>/dev/null; fi

# --- one aid names one attempt: a landing's retry chain journals once, whatever the chain's gates say ---
C=$T/chain.jsonl
chain() { (cd "$T/w" && SG_GATE_JOURNAL="$C" SG_GATE_CARD=1257 SG_GATE_AID="chain-$1" SG_GATE_KIND=fast bash "$d/gate.sh" "$2"); }
chain one true; rc=$?
chain one false; rc2=$?
[ $rc = 0 ] && [ $rc2 = 1 ] && [ "$(q "$C" attempts)" = 1 ] && [ "$(q "$C" field 1 aid)" = chain-one ] \
  && ok "a retry chain with one aid journals one attempt" || ko "chain: rc $rc/$rc2, $(cat "$C" 2>&1)"
chain two true; rc3=$?
[ $rc3 = 0 ] && [ "$(q "$C" attempts)" = 2 ] \
  && ok "a new aid is a new attempt on the same tip, ordinal 2" || ko "chain new aid: rc $rc3, $(cat "$C" 2>&1)"
# The ruff fix commits before the re-gate, so the tip moves; the aid does not, and the chain stays one attempt.
$G -C "$T/w" commit -q --allow-empty -m ruff-fix
chain one true; rc4=$?
[ $rc4 = 0 ] && [ "$(q "$C" attempts)" = 2 ] \
  && ok "one aid is one attempt across a tip move" || ko "chain across tips: rc $rc4, $(cat "$C" 2>&1)"

# --- a replay of the same invocation answers as one attempt, whatever the pid: the identity is the start marker ---
R=$T/replay.jsonl; rb=$T/replay-w; git init -q "$rb"; $G -C "$rb" commit -q --allow-empty -m tip
rsha=$($G -C "$rb" rev-parse HEAD); mk="$SG_STATE/gate-attempt-1259-${rsha:0:12}.start"
rrun() { (cd "$rb" && SG_GATE_JOURNAL="$R" SG_GATE_CARD=1259 SG_GATE_KIND=fast bash "$d/gate.sh" "${1:-true}"); }
printf 'gate-424242\n' > "$mk"   # a start marker the runner wrote before the gate
rrun; rc=$?
[ $rc = 0 ] && [ "$(q "$R" attempts)" = 1 ] && [ "$(q "$R" field 1 aid)" = gate-424242 ] \
  && ok "the attempt identity is the start marker, not the pid" || ko "start marker: rc $rc, $(cat "$R" 2>&1)"
printf 'gate-424242\n' > "$mk"   # a crash after the append: the marker survives, so the replay reads the same aid
rrun; rc=$?
[ $rc = 0 ] && [ "$(q "$R" attempts)" = 1 ] \
  && ok "a replay of the same card and tip answers as one attempt" || ko "replay dedupe: rc $rc, attempts $(q "$R" attempts)"

# --- a memo reuse adds none: the second fast gate answers from the memo and journals a reuse marker ---
M=$T/memo.jsonl; rm -f "$M"
memo() { (cd "$T/w" && SG_GATE_CARD=1256 SG_GATE_JOURNAL="$M" SG_FAST_BASE=HEAD SG_FAST_CMD=true SG_FAST_RETRY_CMD=true \
  bash "$d/fast-gate.sh"); }
memo >/dev/null 2>&1; rc=$?
m1=$(python3 - "$M" <<'P'
import json, sys
print(sum(1 for l in open(sys.argv[1]) if json.loads(l).get("k") == "gate_attempt"))
P
)
memo >/dev/null 2>&1; rc2=$?
m2=$(python3 - "$M" <<'P'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1])]
print(sum(1 for r in recs if r.get("k") == "gate_attempt"), sum(1 for r in recs if r.get("k") == "gate_reuse"))
P
)
[ $rc = 0 ] && [ "$m1" = 1 ] && ok "a fast gate that ran its battery journals one attempt" || ko "memo first run: rc $rc, '$m1'"
[ $rc2 = 0 ] && [ "$m2" = "1 1" ] && ok "a memo reuse adds no attempt and journals one reuse marker" || ko "memo reuse: rc $rc2, '$m2'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
