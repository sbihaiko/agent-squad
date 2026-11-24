#!/bin/bash
# seconds: 12.86
# One ledger file per issue (#673): issue_ledger.py is the one writer of $SG_STATE/state/<owner>__<repo>__<n>.json, under a
# lock per issue. Two writers at once each get their own count and no write is lost; a missing file reads as todo.
# usage: bash test-issue-ledger.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st SG_REPO=x/y; L() { python3 "$d/issue_ledger.py" "$@"; }
[ "$(L read 'x/y#5' state)" = todo ] && [ "$(L count 'x/y#5')" = 0 ] && ok "a missing file reads as todo with 0 attempts" || ko "missing: '$(L read 'x/y#5')'"
# Two writers at once, 15 runs each, on one issue: 30 counts, 30 runs, and each call saw a count of its own.
for w in a b; do ( for i in $(seq 1 15); do L fail 'x/y#7' "$w$i" "" "writer $w" | cut -d' ' -f1; done > "$T/$w.out" ) & done; wait
[ "$(L count 'x/y#7')" = 30 ] && ok "two writers at once lose no write" || ko "count after 30 writes: '$(L count 'x/y#7')'"
[ "$(cat "$T/a.out" "$T/b.out" | sort -n | uniq | wc -l | tr -d ' ')" = 30 ] && ok "each write got a count of its own" || ko "counts seen: $(cat "$T/a.out" "$T/b.out" | sort -n | tr '\n' ' ')"
[ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["own"]["runs"]))' "$T/st/state/x__y__7.json")" = 30 ] \
  && ok "the file lists every run once" || ko "runs: $(cat "$T/st/state/x__y__7.json")"
[ -z "$(/bin/ls "$T/st/state" | /usr/bin/grep -v '\.json$' | /usr/bin/grep -v '\.lock$')" ] && ok "no temp file stays" || ko "left: $(/bin/ls "$T/st/state")"
L fail 'x/y#7' r1 "" x >/dev/null; [ ! -e "$T/st/auto" ] && ok "the old requeue files are not written" || ko "auto: $(/bin/ls "$T/st/auto")"
# ADR-0149: one transition function takes every event. A wave event (entered, held, refused, stuck, delivered) is keyed by its
# run and keeps its cause; a duplicate changes nothing; a delivered run is terminal; `keep` leaves a cause already recorded.
W() { L read 'x/y#9' wave | python3 -c 'import json,sys; r=json.load(sys.stdin).get(sys.argv[1]) or {}; print(r.get("state",""), r.get("cause",""))' "$1"; }
[ "$(L wave 'x/y#9' run-a held 'no review yet')" = recorded ] && [ "$(W run-a)" = "held no review yet" ] && ok "a wave hold is a ledger fact with its cause" || ko "hold: '$(L read 'x/y#9')'"
E() { L read "$1" episodes | python3 -c 'import json,sys; e=json.load(sys.stdin); print(len(e), e[-1]["count"] if e else 0)'; }
[ "$(L wave 'x/y#9' run-a held 'no review yet')" = observed ] && [ "$(E 'x/y#9')" = "1 2" ] && [ "$(W run-a)" = "held no review yet" ] \
  && ok "a repeated hold is one episode: its count rises, its fact stays" || ko "repeat hold: '$(E 'x/y#9')' '$(W run-a)'"
for i in $(seq 40); do L wave 'x/e#1' run-h stuck 'gate lock held by pid 9' >/dev/null; done
[ "$(E 'x/e#1')" = "1 40" ] && ok "forty polls of one stuck child are one episode counted forty" || ko "forty polls: '$(E 'x/e#1')'"
[ "$(L wave 'x/y#9' run-a refused 'the local gates failed: red')" = recorded ] && [ "$(W run-a)" = "refused the local gates failed: red" ] && ok "a later event of the run replaces its fact" || ko "refuse: '$(W run-a)'"
[ "$(L wave 'x/y#9' run-a refused 'the wave reached its age cap' keep)" = kept ] && [ "$(W run-a)" = "refused the local gates failed: red" ] && ok "keep leaves the cause an attempt recorded" || ko "keep: '$(W run-a)'"
L wave 'x/y#9' run-b entered 'entered wave/run-b' >/dev/null; L wave 'x/y#9' run-b delivered 'landed in dev as abc' >/dev/null
[ "$(L wave 'x/y#9' run-b refused 'late')" = terminal ] && [ "$(W run-b)" = "delivered landed in dev as abc" ] && ok "a delivered run is terminal: a late event is ignored" || ko "terminal: '$(W run-b)'"
L wave 'x/y#9' run-c stuck 'the rebase of wave/run-c on dev conflicts' >/dev/null
[ "$(W run-c)" = "stuck the rebase of wave/run-c on dev conflicts" ] && ok "a stuck landing is a ledger fact" || ko "stuck: '$(W run-c)'"
L wave 'x/y#9' run-a bogus x >/dev/null 2>&1 && ko "an unknown wave state is accepted" || ok "an unknown wave state is refused"
# A rearm is a floor: a stale event of a run from before it is ignored, and a new run records as before.
L rearm 'x/y#9' >/dev/null
[ "$(L read 'x/y#9' wave)" = "{}" ] && ok "a rearm clears the wave facts" || ko "rearm wave: '$(L read 'x/y#9' wave)'"
[ "$(L wave 'x/y#9' run-a refused 'stale')" = floor ] && [ "$(W run-a)" = " " ] && ok "a stale event after a rearm counts nothing" || ko "floor: '$(W run-a)'"
[ "$(L wave 'x/y#9' run-d held 'gate lock')" = recorded ] && ok "a run after the rearm records" || ko "after rearm: '$(L read 'x/y#9')'"
# #1301 a rearm clears the issue's notstartable claims, so a later failure with the same reason is marked again: the claim is
# health.sh's memory that the marker was posted, and the rearm queues the marker's removal. health.sh serves the profile's
# primary repository and names a claim by a bare issue number, so only that repository's rearm may clear, and only that issue's.
mkdir -p "$T/st/auto"; : > "$T/st/auto/notstartable-31-some_reason"; : > "$T/st/auto/notstartable-32-other_issue"
L rearm 'x/y#31' >/dev/null
{ [ ! -e "$T/st/auto/notstartable-31-some_reason" ] && [ -e "$T/st/auto/notstartable-32-other_issue" ]; } \
  && ok "#1301 a rearm clears the issue's notstartable claims, not another issue's" \
  || ko "#1301 rearm claims: $(/bin/ls "$T/st/auto" | tr '\n' ' ')"
: > "$T/st/auto/notstartable-33-y_reason"; L rearm 'x/w#33' >/dev/null
[ -e "$T/st/auto/notstartable-33-y_reason" ] \
  && ok "#1301 a rearm of another repository's issue touches no bare-number claim" \
  || ko "#1301 foreign rearm cleared a claim: $(/bin/ls "$T/st/auto" | tr '\n' ' ')"
# The entry of a child is a ledger fact that a later event of its run keeps (ADR-0149): the wave marker readers ask the ledger
# which runs a child entered, and a child that left the wave after its entry still entered it.
L wave 'x/z#12' run-e entered 'entered wave/run-e at s1' >/dev/null; L wave 'x/z#12' run-e refused 'the check at the wave tip was red' >/dev/null
L wave 'x/z#13' run-e entered 'entered wave/run-e at s2' >/dev/null; L wave 'x/z#14' run-f held 'no review yet' >/dev/null
[ "$(L entered x/z | sort | tr '\t\n' ': ')" = "run-e:12 run-e:13 " ] && ok "the ledger lists each run a child entered, after a later event too" || ko "entered: '$(L entered x/z)'"
[ -z "$(L entered x/q)" ] && ok "a repository with no ledger lists no entry" || ko "entered x/q: '$(L entered x/q)'"
# A landing try and an owed close are ledger facts of the run (ADR-0149): `landtry` counts the run's failed landings for the
# issue and prints the count, `owe` keeps the issue a landed wave could not close, and `owed` lists them for the run.
[ "$(L landtry 'x/z#20' run-g 'the rebase conflicts')" = 1 ] && [ "$(L landtry 'x/z#20' run-g 'the rebase conflicts')" = 2 ] \
  && ok "each failed landing counts one try, the same cause too" || ko "landtry: '$(L read 'x/z#20' wave)'"
[ "$(L read 'x/z#20' wave | python3 -c 'import json,sys; f=json.load(sys.stdin)["run-g"]; print(f["state"], f["tries"])')" = "stuck 2" ] \
  && ok "the tries are the run's stuck fact" || ko "tries fact: '$(L read 'x/z#20' wave)'"
[ "$(L owe 'x/z#21' run-g wave/run-g abc)" = recorded ] && [ "$(L owed x/z run-g | tr '\t' :)" = "abc:wave/run-g:21" ] \
  && ok "an issue a landed wave could not close is owed in its ledger" || ko "owed: '$(L owed x/z run-g)'"
L wave 'x/z#21' run-g delivered 'landed wave/run-g in dev as abc' >/dev/null
[ -z "$(L owed x/z run-g)" ] && ok "a delivered issue is owed no more" || ko "owed after delivery: '$(L owed x/z run-g)'"
# A kill in a write leaves a temp file beside the old file: the old file reads whole, and the next event writes.
printf '{"tor' > "$T/st/state/x__y__9.json.zz.tmp"
[ "$(W run-d)" = "held gate lock" ] && [ "$(L wave 'x/y#9' run-d entered 'in')" = recorded ] && ok "a write killed before its rename loses nothing" || ko "torn temp: '$(L read 'x/y#9')'"
# A corrupt ledger holds dispatch for that issue (ADR-0149 rule 4): it never reads as the seed, a writer never overwrites it,
# the hold names the cause, and a rearm, the owner's act, is what recovers it.
mkdir -p "$T/st/auto"; echo 2 > "$T/st/auto/requeues-11"; printf '{"own": {"count": 1' > "$T/st/state/x__y__11.json"
[ "$(L read 'x/y#11' state)" = held ] && ok "a corrupt ledger reads held" || ko "corrupt state: '$(L read 'x/y#11')'"
case "$(L read 'x/y#11' held)" in *corrupt*x__y__11.json*) ok "the hold names the issue's file and the cause";; *) ko "held cause: '$(L read 'x/y#11' held)'";; esac
out=$(L fail 'x/y#11' r1 "" x 2>&1); rc=$?
[ $rc != 0 ] && [ "$(cat "$T/st/state/x__y__11.json")" = '{"own": {"count": 1' ] && ok "a writer never overwrites a corrupt ledger" || ko "fail on corrupt: rc $rc '$out'"
[ "$(L wave 'x/y#11' r1 held x >/dev/null 2>&1; echo $?)" != 0 ] && ok "a wave event on a corrupt ledger is refused" || ko "wave on corrupt accepted"
L rearm 'x/y#11' >/dev/null && [ "$(L read 'x/y#11' state)" = todo ] && ok "a rearm recovers a corrupt ledger" || ko "rearm corrupt: '$(L read 'x/y#11')'"
# A missing ledger beside the old counters of the issue holds it (ADR-0149 rule 4); a new issue with no loop trace reads todo.
echo 1 > "$T/st/auto/loopqueues-30"
[ "$(L read 'x/y#30' state)" = held ] && [ "$(L read 'x/y#31' state)" = todo ] && ok "a missing ledger holds only beside old counters" || ko "missing: '$(L read 'x/y#30')' '$(L read 'x/y#31' state)'"
L migrate 'x/y#30' >/dev/null && [ "$(L read 'x/y#30' state)" = todo ] && [ "$(L count 'x/y#30' loop)" = 1 ] && ok "migrate carries the old counters in" || ko "migrate: '$(L read 'x/y#30')'"
# A landing pass records the issue's work in the base (ADR-0149, #695): `landed` is the fact rule R reads instead of a
# run's land heads. It is keyed by the run (or by the branch a branchland pass landed), a duplicate writes nothing, and a
# rearm clears it. A `requeue` fact holds the head a run requeued the issue's child on (the old auto/requeued- marker).
[ "$(L landed 'x/z#40' run-h run abc)" = recorded ] && [ "$(L landed x/z | tr '\t' :)" = "run-h:40" ] \
  && ok "a landing is a ledger fact keyed by its run" || ko "landed: '$(L read 'x/z#40' landed)'"
[ "$(L landed 'x/z#40' run-h run abc)" = duplicate ] && ok "a duplicate landing changes nothing" || ko "landed dup: '$(L read 'x/z#40' landed)'"
[ "$(L landed 'x/z#41' fix/41-x branch def)" = recorded ] && [ "$(L landed x/z | sort | tr '\t\n' ': ')" = "fix/41-x:41 run-h:40 " ] \
  && ok "a branchland landing is a fact of its branch" || ko "landed branch: '$(L landed x/z)'"
[ "$(L requeue 'x/z#40' run-h abc123)" = recorded ] && [ "$(L requeued 'x/z#40' run-h)" = abc123 ] \
  && ok "the head a run requeued the issue on is a ledger fact" || ko "requeued: '$(L read 'x/z#40' requeued)'"
[ "$(L requeue 'x/z#40' run-h abc123)" = duplicate ] && [ -z "$(L requeued 'x/z#40' run-j)" ] \
  && ok "a duplicate requeue changes nothing, and a run that did not requeue names no head" || ko "requeued dup: '$(L read 'x/z#40' requeued)'"
L rearm 'x/z#40' >/dev/null
[ "$(L read 'x/z#40' landed)" = "{}" ] && [ -z "$(L requeued 'x/z#40' run-h)" ] \
  && ok "a rearm clears the landing and requeue facts" || ko "after rearm: '$(L read 'x/z#40')'"
# The review streak is a ledger fact (ADR-0151, #702): three CHANGES verdicts in a row end the issue needs-human for the
# owner, an APPROVE starts the count again, and identical findings twice move the next attempt one place down the coder
# chain. A verdict is one fact per run and round; a rearm clears it and its floor holds.
R() { L review 'x/z#50' "$@"; }
[ "$(R r1 changes 'the same finding')" = recorded ] && [ "$(L coder 'x/z#50')" = 0 ] \
  && ok "one CHANGES verdict asks for no different coder" || ko "coder after one: '$(L read 'x/z#50' review)'"
[ "$(R r2 changes 'the same finding')" = recorded ] && [ "$(L coder 'x/z#50')" = 1 ] \
  && ok "identical findings twice move the next attempt one place down the chain" || ko "coder after two: '$(L coder 'x/z#50')'"
[ "$(R r3 changes 'the same finding')" = frozen ] && [ "$(L coder 'x/z#50')" = 1 ] \
  && ok "a third CHANGES verdict freezes the issue and moves no coder a second time" || ko "three: '$(L read 'x/z#50')'"
[ "$(L read 'x/z#50' state)" = needs-human ] && ok "a frozen review streak holds the issue's dispatch (needs-human)" || ko "state: '$(L read 'x/z#50' state)'"
[ "$(R r4 approve '')" = reset ] && [ "$(L read 'x/z#50' state)" = needs-human ] \
  && ok "an approval after the freeze does not lift the owner's freeze" || ko "approve after freeze: '$(L read 'x/z#50' state)'"
[ "$(R r5 changes 'a new finding')" = recorded ] && [ "$(L coder 'x/z#50')" = 0 ] \
  && ok "a new finding starts the coder at the top of the chain" || ko "new finding: '$(L coder 'x/z#50')'"
[ "$(R r6 changes 'another finding')" = recorded ] && [ "$(L read 'x/z#50' review | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["changes"], d["moves"])')" = "2 0" ] \
  && ok "two different findings change no coder" || ko "different: '$(L read 'x/z#50' review)'"
[ "$(L review 'x/z#51' r1 changes f1 1)" = recorded ] && [ "$(L review 'x/z#51' r1 changes f1 1)" = duplicate ] \
  && [ "$(L review 'x/z#51' r1 changes f1 2)" = recorded ] \
  && ok "a review verdict is one fact per run and round" || ko "per round: '$(L read 'x/z#51' review)'"
L review 'x/z#51' r1 blocked f1 3 >/dev/null 2>&1 && ko "an unknown verdict is accepted" || ok "an unknown verdict is refused"
L rearm 'x/z#51' >/dev/null
[ "$(L read 'x/z#51' review | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["changes"], d["moves"], len(d["verdicts"]))')" = "0 0 0" ] \
  && ok "a rearm clears the review facts" || ko "rearm review: '$(L read 'x/z#51' review)'"
[ "$(L review 'x/z#51' r1 changes f1 1)" = floor ] && ok "a review of a run from before the rearm counts nothing" || ko "floor review: '$(L read 'x/z#51' review)'"
# A replay (ADR-0149 rule 6) rebuilds the issue's state from its ledger's events and compares it with the stored state and
# with any old counter beside it; it names the issue and exits non-zero when they differ.
L fail 'x/z#60' r1 k first >/dev/null; L fail 'x/z#60' r2 k second >/dev/null; L review 'x/z#60' v1 changes f 1 >/dev/null
out=$(L replay 'x/z#60' 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *'x/z#60'*ok*) ok "a replay of a sound ledger agrees";; *) ko "replay out: '$out'";; esac || ko "replay rc $rc '$out'"
python3 - "$T/st/state/x__z__60.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d["own"]["count"]=9; json.dump(d,open(sys.argv[1],"w"))
PY
out=$(L replay 'x/z#60' 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *'x/z#60'*own.count*) ok "a replay names the issue and the field that differs";; *) ko "replay diff out: '$out'";; esac || ko "replay diff rc $rc '$out'"
L fail 'x/y#61' r1 k first >/dev/null; echo 3 > "$T/st/auto/requeues-61"
out=$(L replay 'x/y#61' 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *'x/y#61'*requeues-61*) ok "a replay names an old counter that differs from the ledger";; *) ko "replay legacy out: '$out'";; esac || ko "replay legacy rc $rc '$out'"
L fail 'x/y#62' r1 k first >/dev/null; printf '1\n' > "$T/st/auto/requeues-62"; printf 'r1\tother\tfirst\n' > "$T/st/auto/requeues-62.runs"
out=$(L replay 'x/y#62' 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *'x/y#62'*own.runs*) ok "a replay names a legacy failure whose key differs";; *) ko "replay key out: '$out'";; esac || ko "replay key rc $rc '$out'"
printf 'r1\tk\tfirst\nfrozen\n' > "$T/st/auto/requeues-62.runs"
out=$(L replay 'x/y#62' 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *'x/y#62'*own.frozen*) ok "a replay names a legacy frozen the ledger does not hold";; *) ko "replay frozen out: '$out'";; esac || ko "replay frozen rc $rc '$out'"
L review 'x/y#63' v1 changes f 1 >/dev/null
python3 - "$T/st/state/x__y__63.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d["review"]["stopped"]="3 reviews"; json.dump(d,open(sys.argv[1],"w"))
PY
out=$(L replay 'x/y#63' 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *'x/y#63'*review.stopped*) ok "a replay names a stored review stop its events do not support";; *) ko "replay stop out: '$out'";; esac || ko "replay stop rc $rc '$out'"
# ADR-0164: a board write is an event with a stable identity (its run and its column) and its own instant. The event is
# recorded pending before its card write; a retry is a duplicate; a write that lands for an event the fact no longer holds
# is superseded; an event older than the fact is stale, so a late delivery keeps the true order (criterion 3 of #1021).
B() { L read "$1" board | python3 -c 'import json,sys; b=json.load(sys.stdin); print(b.get("run",""), b.get("column",""), b.get("state",""))'; }
BI() { L read "$1" board | python3 -c 'import json,sys; print(json.load(sys.stdin)["instant"])'; }
[ "$(L board 'x/y#21' run-a Doing /req/a)" = recorded ] && [ "$(B 'x/y#21')" = "run-a Doing pending" ] \
  && ok "a board event is recorded pending, with its run and column" || ko "board event: '$(L read 'x/y#21' board)'"
[ "$(L pending 'x/y#21' | cut -f1-3)" = "21	run-a	Doing" ] && ok "pending lists the issue's unwritten board event" || ko "pending: '$(L pending 'x/y#21')'"
I1=$(BI 'x/y#21')
[ "$(L board 'x/y#21' run-a Doing /req/a)" = duplicate ] && [ "$(BI 'x/y#21')" = "$I1" ] \
  && ok "a retried board event is a duplicate and keeps its first instant" || ko "board duplicate: '$(L read 'x/y#21' board)'"
[ "$(L boardw 'x/y#21' run-a Doing "autobug dispatch")" = written ] && [ "$(B 'x/y#21')" = "run-a Doing written" ] && [ -z "$(L pending 'x/y#21')" ] \
  && ok "a landed card write marks its event written" || ko "boardw: '$(L read 'x/y#21' board)'"
[ "$(L read 'x/y#21' board | python3 -c 'import json,sys; b=json.load(sys.stdin); print(b["by"], isinstance(b["landed"], float))')" = "autobug dispatch True" ] \
  && ok "a landed card write names its actor and its landing instant" || ko "landed by: '$(L read 'x/y#21' board)'"
[ "$(L boardw 'x/y#21' run-a Doing)" = duplicate ] && ok "a repeated write confirmation is a duplicate" || ko "boardw duplicate"
L board 'x/y#22' run-b Doing /req/b 200 >/dev/null
[ "$(L board 'x/y#22' run-a Todo /req/a 100)" = stale ] && [ "$(B 'x/y#22')" = "run-b Doing pending" ] \
  && ok "an event older than the fact is stale: the true order holds" || ko "stale: '$(L read 'x/y#22' board)'"
[ "$(L boardw 'x/y#22' run-a Todo)" = superseded ] && [ "$(B 'x/y#22')" = "run-b Doing pending" ] \
  && ok "a write for an event the fact no longer holds changes nothing" || ko "superseded: '$(L read 'x/y#22' board)'"
[ "$(L board 'x/y#22' run-c Todo /req/c 300)" = recorded ] && [ "$(B 'x/y#22')" = "run-c Todo pending" ] \
  && ok "a newer board event takes the fact" || ko "newer: '$(L read 'x/y#22' board)'"
printf 'not json' > "$T/st/state/x__y__24.json"
L board 'x/y#24' run-e Doing /req/e >/dev/null 2>&1; [ $? = 3 ] && [ "$(cat "$T/st/state/x__y__24.json")" = 'not json' ] \
  && ok "a corrupt ledger holds a board event and is left as it is" || ko "corrupt board: '$(cat "$T/st/state/x__y__24.json")'"
L rearm 'x/y#21' >/dev/null
[ -z "$(L pending 'x/y#21')" ] && [ "$(L board 'x/y#21' run-a Doing /req/a)" = floor ] \
  && ok "a rearm clears a pending board write, and its run counts toward nothing" || ko "rearm floor: '$(L board 'x/y#21' run-a Doing /req/a)'"
# ADR-0164 (#1021, review): a pending event the gates no longer back is dropped, never written. A drop leaves the
# pending list, keeps the event's instant, and changes nothing once the event is not the fact any more.
L board 'x/y#25' run-f Doing /req/f >/dev/null
[ "$(L boardx 'x/y#25' run-f Doing)" = dropped ] && [ "$(B 'x/y#25')" = "run-f Doing dropped" ] && [ -z "$(L pending 'x/y#25')" ] \
  && ok "a pending board event is dropped, and it leaves the pending list" || ko "boardx: '$(L read 'x/y#25' board)'"
[ "$(L boardx 'x/y#25' run-f Doing)" = superseded ] && [ "$(B 'x/y#25')" = "run-f Doing dropped" ] \
  && ok "a drop of an event that is no longer pending changes nothing" || ko "boardx again: '$(L read 'x/y#25' board)'"
L board 'x/y#26' run-g Doing /req/g >/dev/null
[ "$(L boardx 'x/y#26' run-h Doing)" = superseded ] && [ "$(B 'x/y#26')" = "run-g Doing pending" ] \
  && ok "a drop names the run and column of the event it drops" || ko "boardx other run: '$(L read 'x/y#26' board)'"

rm -rf "$T"; exit $fail
