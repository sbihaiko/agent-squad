#!/bin/bash
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
F=$T/st/state/x__y__9.json; before=$(cksum < "$F"); sleep 1
[ "$(L wave 'x/y#9' run-a held 'no review yet')" = duplicate ] && [ "$(cksum < "$F")" = "$before" ] && ok "a duplicate event changes nothing" || ko "duplicate: '$(cat "$F")'"
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
rm -rf "$T"; exit $fail
