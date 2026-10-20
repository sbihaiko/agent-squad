#!/bin/bash
# The requeue count belongs to the issue, not the run (#532): each run that a review sent back started afresh, and one issue
# was dispatched 19 times. The third requeue of one issue stops it: its dispatch marker stays, its card keeps the reached
# column with the owner freeze marker, and a SQUAD-NOT-STARTABLE comment is queued, which both dispatch passes read. A
# requeue below the cap is unchanged, except that a refused card write is named.
# usage: bash test-wave-requeue-cap.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
exec 3>&2 2>>"$T.err"   # the suite's own stderr, read at the end: a "command not found" there is a fixture that lacks a file (#610)
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"; cp "$d/wave.sh" "$d/issue_ledger.py" "$d/address.sh" "$d/ghcall.sh" "$d/base-battery.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 BOARD=%s/board.sh\n. %s/address.sh\n' "$T/st" "$T" "$T/s" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\nif [ "${BOARD_REFUSE:-}" = 1 ]; then echo "board refused" >&2; exit 1; fi\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/gh.log"\nprintf "{}"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
# cnt <n> [loop]: the count of the issue's ledger (#673), read through its one module.
cnt() { SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" count "x/y#$1" ${2:-}; }
nsc() { cat "$T"/st/cq/* 2>/dev/null | /usr/bin/grep -c '^SQUAD-NOT-STARTABLE:'; }
for i in 1 2; do
  echo req > "$T/st/auto/disp-7"
  PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x" >/dev/null
done
[ ! -e "$T/st/auto/disp-7" ] && [ "$(/usr/bin/grep -c 'o 3 Todo 7' "$T/board.log")" = 2 ] && [ "$(nsc)" = 0 ] \
  && ok "two requeues free the issue and put it back in Todo" || ko "below the cap: marker $(/bin/ls "$T/st/auto"), board '$(tr '\n' ' ' < "$T/board.log")', stops $(nsc)"
echo req > "$T/st/auto/disp-7"
o=$(PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x")
[ -e "$T/st/auto/disp-7" ] && ok "the third requeue keeps the dispatch marker" || ko "the third requeue freed the issue"
! /usr/bin/grep -q 'o 3 Blocked 7' "$T/board.log" && ok "the third requeue does not move the card to Blocked" || ko "board: '$(tr '\n' ' ' < "$T/board.log")'"
/usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/7/labels -f labels[]=frozen:owner' "$T/gh.log" && ok "the third requeue freezes the card for the owner" || ko "freeze: '$(tr '\n' ' ' < "$T/gh.log" 2>/dev/null)'"
[ "$(nsc)" = 1 ] && ok "the third requeue queues a SQUAD-NOT-STARTABLE comment" || ko "no stop comment queued"
case "$o" in *"WAVE STOP #7"*) ok "the stop is logged on one line";; *) ko "no stop line: '$o'";; esac
echo req > "$T/st/auto/disp-8"; o8=$(PATH="$T/bin:$PATH" BOARD_REFUSE=1 bash "$T/s/wave.sh" requeue 8 "y" 2>&1)
[ ! -e "$T/st/auto/disp-8" ] && ok "another issue keeps its own count" || ko "issue 8 was stopped by issue 7's count: $o8"
case "$o8" in *"WAVE-BOARD #8 Todo refused: board refused"*) ok "a refused card write is reported";; *) ko "the refused card write was dropped: '$o8'";; esac
# A requeue the wave's age cap made (#594) is no failure of the child: the loop's own queue made it late. It never counts
# toward the cap and never stops the issue, and the count real failures left stays as it was.
echo 2 > "$T/st/auto/requeues-9"; echo req > "$T/st/auto/disp-9"   # two real failures already counted: a counted requeue now stops it
o9=$(PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 9 "the wave reached its age cap" cap)
[ ! -e "$T/st/auto/disp-9" ] && [ "$(cnt 9)" = 2 ] && case "$o9" in *"WAVE REQUEUE #9"*) true;; *) false;; esac \
  && ok "a cap requeue never counts and never stops the issue" || ko "cap requeue: count '$(cnt 9)', '$o9'"
echo req > "$T/st/auto/disp-9"; o9=$(PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 9 "the local gates failed: x")
case "$o9" in *"WAVE STOP #9"*) ok "real failures still stop the issue at the third";; *) ko "no stop at the third real failure: '$o9'";; esac
# #609: one count of failed dispatches, keyed by the run (ADR-0134). A run counts once per issue, whether a requeue or the
# janitor's `fail` (the run ended with nothing delivered) reports it; at the cap the issue is frozen with one comment that
# lists every run and its reason. A different reason is no evolution; a new `Refs #N` commit in origin/$SG_BASE is.
w() { PATH="$T/bin:$PATH" bash "$T/s/wave.sh" "$@"; }
gl() { /usr/bin/grep -c "issues/$1/labels" "$T/gh.log" 2>/dev/null; }
SG_REQUEUE_RUN=r1 w requeue 11 "the review asked for changes: a" >/dev/null
SG_REQUEUE_RUN=r1 w requeue 11 "the local gates failed: b" >/dev/null
o=$(w fail 11 r1 "ended failed: run_spend_cap")
[ "$(cnt 11)" = 1 ] && ok "one run counts once, however many times it fails the issue" || ko "run key: count '$(cnt 11)', '$o'"
o=$(w fail 11 r2 "ended failed: node_spend_cap")
case "$o" in *"WAVE STOP"*) ko "the second run stopped the issue: '$o'";; *) ok "a second failed run is below the cap";; esac
o=$(w fail 11 r3 "ended failed: the gates failed")
case "$o" in *"WAVE STOP #11"*) ok "the third failed run stops the issue, whatever its reason";; *) ko "no stop at the third run: '$o'";; esac
[ "$(gl 11)" = 1 ] && /usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/11/labels -f labels[]=frozen:owner' "$T/gh.log" && ok "the stop freezes the issue for the owner once" || ko "freeze #11: '$(tr '\n' ' ' < "$T/gh.log")'"
c11=$(cat "$T"/st/cq/*11-wavestop* 2>/dev/null)
case "$c11" in SQUAD-NOT-STARTABLE:*r1*"the review asked for changes: a"*r2*node_spend_cap*r3*"the gates failed"*) ok "the stop comment lists every run and its reason";;
  *) ko "stop comment: '$c11'";; esac
o=$(w fail 11 r3 "ended failed: the gates failed")
[ "$(gl 11)" = 1 ] && case "$o" in *"WAVE STOP"*) false;; *) true;; esac && ok "a run already counted freezes nothing again" || ko "repeat of r3: '$o', labels $(gl 11)"
o=$(w fail 11 r4 "ended failed: x")
[ "$(cnt 11)" = 1 ] && case "$o" in *"WAVE STOP"*) false;; *) true;; esac && ok "a new run after the freeze (the owner let it start) counts from one" || ko "after the freeze: count '$(cnt 11)', '$o'"
# Evolution: a new non-RED `Refs #12` commit in origin/dev restarts the count; a RED one does not.
R="$T/co"; /usr/bin/git init -q "$R"; gc() { /usr/bin/git -C "$R" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1" && /usr/bin/git -C "$R" update-ref refs/remotes/origin/dev HEAD; }
gc "start"
SG_CHECKOUT="$R" w fail 12 ra "ended failed: a" >/dev/null; SG_CHECKOUT="$R" w fail 12 rb "ended failed: b" >/dev/null
gc "TDD RED: a test for it

Refs #12"
o=$(SG_CHECKOUT="$R" w fail 12 rc "ended failed: c")
case "$o" in *"WAVE STOP #12"*) ok "a RED commit is no evolution";; *) ko "a RED commit reset the count: '$o'";; esac
SG_CHECKOUT="$R" w fail 13 ra "ended failed: a" >/dev/null; SG_CHECKOUT="$R" w fail 13 rb "ended failed: b" >/dev/null
gc "Part of the fix

Refs #13"
o=$(SG_CHECKOUT="$R" w fail 13 rc "ended failed: c")
[ "$(cnt 13)" = 1 ] && case "$o" in *"WAVE STOP"*) false;; *) true;; esac && ok "a new commit in the base restarts the count" || ko "evolution: count '$(cnt 13)', '$o'"
# #621: a requeue the loop caused -- a wave that landed without its child, an adapter error, a merge conflict with a sibling,
# a disk stop -- is no failure of the issue's work: it requeues the issue and never counts toward SG_REQUEUE_CAP. Its own
# bound, SG_LOOP_REQUEUE_CAP (6), ends the issue needs-human (ADR-0029) with needs-owner and a comment naming the loop cause.
# It writes no Blocked column (#630): the shipped board has none, and janitor rule R turns needs-owner into frozen:owner. `own` makes the janitor's verdict read (the review held the diff) count, whatever the text.
for r in l1 l2 l3 l4; do o=$(w fail 21 $r "the wave of run $r landed without its child"); done
[ "$(cnt 21)" = 0 ] && [ "$(gl 21)" = 0 ] && case "$o" in *"WAVE STOP"*) false;; *) true;; esac \
  && ok "a wave that landed without its child never counts toward the cap" || ko "loop fail: count '$(cnt 21)', '$o'"
echo req > "$T/st/auto/disp-22"
for r in a b c; do SG_REQUEUE_RUN=$r w requeue 22 "ended failed: adapter_error" >/dev/null; done
SG_REQUEUE_RUN=d w requeue 22 "the rebase on wave/x conflicts: f.py" >/dev/null; echo req > "$T/st/auto/disp-22"
o=$(SG_REQUEUE_RUN=e w requeue 22 "DISK STOP: / has 1 GiB free")
[ ! -e "$T/st/auto/disp-22" ] && [ "$(cnt 22)" = 0 ] && case "$o" in *"WAVE REQUEUE #22"*) true;; *) false;; esac \
  && ok "a loop-caused requeue still requeues the issue, with no count" || ko "loop requeue: count '$(cnt 22)', '$o'"
o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m1 "ended failed: merge_conflict"); o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m2 "ended failed: adapter_error")
o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m3 "the wave of run m3 landed without its child")
c23=$(cat "$T"/st/cq/*23-* 2>/dev/null)
case "$o" in *"WAVE NEEDS-HUMAN #23"*) ok "the loop bound ends the issue needs-human";; *) ko "no loop stop at SG_LOOP_REQUEUE_CAP: '$o'";; esac
! /usr/bin/grep -q ' Blocked 23$' "$T/board.log" && /usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/23/labels -f labels[]=needs-owner' "$T/gh.log" && ! /usr/bin/grep -q 'issues/23/labels -f labels\[\]=frozen:owner' "$T/gh.log" && ok "the loop stop adds needs-owner and writes no Blocked column (#630)" || ko "loop stop: board '$(tr '\n' ' ' < "$T/board.log")', gh '$(/usr/bin/grep 'issues/23' "$T/gh.log")'"
case "$c23" in *"landed without its child"*) ok "the loop stop comment names the loop cause";; *) ko "loop stop comment: '$c23'";; esac
case "$c23" in *SQUAD-NOT-STARTABLE*) ko "the loop stop is no owner freeze: '$c23'";; *) ok "the loop stop queues no SQUAD-NOT-STARTABLE";; esac
w fail 24 o1 "ended failed: review_held" own >/dev/null; w fail 24 o2 "ended failed: adapter_error" own >/dev/null
[ "$(cnt 24)" = 2 ] && ok "own counts toward the cap whatever the reason text" || ko "own: count '$(cnt 24)'"
SG_REQUEUE_RUN=v1 w requeue 25 "the review of its own diff in run v1 asked for changes (VERDICT: BLOCKED (every review model failed)) at abc:" >/dev/null
SG_REQUEUE_RUN=v2 w requeue 25 "the review of its own diff in run v2 asked for changes (VERDICT: BLOCKED) at abc:
- a real finding" >/dev/null
[ "$(cnt 25)" = 1 ] && ok "a BLOCKED the chain wrote (every model failed) never counts; one a model wrote does" || ko "chain BLOCKED: count '$(cnt 25)'"
# #673: the counts live in one file per issue, keyed by repository and issue, and the old counter files are written no more.
[ -f "$T/st/state/x__y__11.json" ] && [ ! -e "$T/st/auto/requeues-11" ] && [ ! -e "$T/st/auto/requeues-11.runs" ] && [ ! -e "$T/st/auto/loopqueues-23.runs" ] \
  && ok "the count is in the issue's ledger file, not in auto/requeues-N" || ko "ledger files: $(/bin/ls "$T/st/state" "$T/st/auto" 2>&1 | tr '\n' ' ')"
[ "$(cnt 23 loop)" = 3 ] && ok "the loop-caused count is the ledger's loop count" || ko "loop count #23: '$(cnt 23 loop)'"
cat "$T.err" >&3
/usr/bin/grep -Eq 'command not found|No such file' "$T.err" && ko "the run printed a missing command or file: $(/usr/bin/grep -E 'command not found|No such file' "$T.err" | tr '\n' ' ')"
rm -rf "$T" "$T.err"; exit $fail
