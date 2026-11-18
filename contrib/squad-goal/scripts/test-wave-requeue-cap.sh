#!/bin/bash
# The requeue count belongs to the issue, not the run (#532): each run that a review sent back started afresh, and one issue
# was dispatched 19 times. The third requeue of one issue stops it: its dispatch marker stays, its card keeps the reached
# column with the owner freeze marker, and a SQUAD-NOT-STARTABLE comment is queued, which both dispatch passes read. A
# requeue below the cap is unchanged, except that a refused card write is named.
# usage: bash test-wave-requeue-cap.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
exec 3>&2 2>>"$T.err"   # the suite's own stderr, read at the end: a "command not found" there is a fixture that lacks a file (#610)
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"; cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/address.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/"
# The suite's own checkout (#1486): `landed_key` reads the base of $SG_CHECKOUT, so a caller that exports one -- the loop
# does -- would give the evolution key a history the suite never wrote. Pinned here, the key is the suite's and no case
# depends on what the caller has checked out; a case that wants another base names it inline.
/usr/bin/git init -q "$T/sgco"; /usr/bin/git -C "$T/sgco" -c user.name=t -c user.email=t@t commit -q --allow-empty -m start
/usr/bin/git -C "$T/sgco" update-ref refs/remotes/origin/dev HEAD
export SG_CHECKOUT="$T/sgco"
# The cases above #2053 read the stop path alone: the hand route is off for them, and on for its own cases below.
export SG_HAND_AT=off
printf 'export SG_STATE=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 BOARD=%s/board.sh\n. %s/address.sh\n' "$T/st" "$T" "$T/s" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "${SG_MOVE_REASON:-none} $*" >> "%s/board-reason.log"\nif [ "${BOARD_REFUSE:-}" = 1 ]; then echo "board refused" >&2; exit 1; fi\n' "$T" "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
# The stub gh logs every argv. A single-issue read is one GraphQL query: it answers state OPEN, so every case reads the
# live state it acts on; issue 77 answers CLOSED, issue 78 answers a body that is not JSON (the unread path, named), and
# issue 42 carries the owner freeze label. Any other route answers {}.
cat > "$T/bin/gh.tmp" <<'STUB'
#!/bin/bash
printf "%s\n" "$*" >> "@T@/gh.log"
case "$*" in
  *"issue(number: 77)"*) printf '{"data":{"repository":{"i77":{"number":77,"state":"CLOSED","labels":{"nodes":[]}}}}}' ;;
  *"issue(number: 78)"*) printf 'not json' ;;
  *"issue(number: 42)"*) printf '{"data":{"repository":{"i42":{"number":42,"state":"OPEN","labels":{"nodes":[{"name":"frozen:owner"}]}}}}}' ;;
  *"issue(number:"*)
    n=$(printf '%s' "$*" | sed -n 's/.*issue(number: \([0-9][0-9]*\)).*/\1/p')
    s=OPEN; [ -e "@T@/closed-${n:-0}" ] && s=CLOSED
    printf '{"data":{"repository":{"i":{"number":%s,"state":"%s","labels":{"nodes":[]}}}}}' "${n:-0}" "$s" ;;
  *) printf '{}' ;;
esac
STUB
sed "s|@T@|$T|g" "$T/bin/gh.tmp" > "$T/bin/gh"; rm -f "$T/bin/gh.tmp"; chmod +x "$T/bin/gh"
# cnt <n> [loop]: the count of the issue's ledger (#673), read through its one module.
cnt() { SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" count "x/y#$1" ${2:-}; }
nsc() { cat "$T"/st/cq/* 2>/dev/null | /usr/bin/grep -c '^SQUAD-NOT-STARTABLE:'; }
for i in 1 2; do
  echo req > "$T/st/auto/disp-7"
  PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x" >/dev/null
done
[ ! -e "$T/st/auto/disp-7" ] && [ "$(/usr/bin/grep -c 'o 3 Todo 7' "$T/board.log")" = 2 ] && [ "$(nsc)" = 0 ] \
  && ok "two requeues free the issue and put it back in Todo" || ko "below the cap: marker $(/bin/ls "$T/st/auto"), board '$(tr '\n' ' ' < "$T/board.log")', stops $(nsc)"
# #1340: a requeue lands nothing, so its card leaves Code Complete as a no-landing move (board.py refuses any other automated backward move).
[ "$(/usr/bin/grep -c '^no-landing o 3 Todo 7$' "$T/board-reason.log")" = 2 ] && ok "a requeue moves its card back to Todo as no-landing (#1340)" || ko "requeue move reason: '$(tr '\n' ' ' < "$T/board-reason.log")'"
echo req > "$T/st/auto/disp-7"
o=$(PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x")
[ -e "$T/st/auto/disp-7" ] && ok "the third requeue keeps the dispatch marker" || ko "the third requeue freed the issue"
! /usr/bin/grep -q 'o 3 Blocked 7' "$T/board.log" && ok "the third requeue does not move the card to Blocked" || ko "board: '$(tr '\n' ' ' < "$T/board.log")'"
/usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/7/labels -f labels[]=frozen:owner' "$T/gh.log" && ok "the third requeue freezes the card for the owner" || ko "freeze: '$(tr '\n' ' ' < "$T/gh.log" 2>/dev/null)'"
[ "$(nsc)" = 1 ] && ok "the third requeue queues a SQUAD-NOT-STARTABLE comment" || ko "no stop comment queued"
/usr/bin/grep -qE '^SQUAD-NOT-STARTABLE: build=' "$T"/st/cq/* 2>/dev/null && ok "the marker names the build that wrote it (ADR-0159)" || ko "the marker names no build"
case "$o" in *"WAVE STOP #7"*) ok "the stop is logged on one line";; *) ko "no stop line: '$o'";; esac
echo req > "$T/st/auto/disp-8"; o8=$(PATH="$T/bin:$PATH" BOARD_REFUSE=1 bash "$T/s/wave.sh" requeue 8 "y" 2>&1)
[ ! -e "$T/st/auto/disp-8" ] && ok "another issue keeps its own count" || ko "issue 8 was stopped by issue 7's count: $o8"
case "$o8" in *"WAVE-BOARD #8 Todo refused: board refused"*) ok "a refused card write is reported";; *) ko "the refused card write was dropped: '$o8'";; esac
# A requeue the wave's age cap made (#594) is no failure of the child: the loop's own queue made it late. It never counts
# toward the cap and never stops the issue, and the count real failures left stays as it was.
for r in r9a r9b; do SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" fail 'x/y#9' "$r" '' 'x' >/dev/null; done; echo req > "$T/st/auto/disp-9"   # two real failures already counted: a counted requeue now stops it
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
# #621: a requeue the loop caused -- a child a sibling's conflict or a held gate kept out of the wave, an adapter error, a merge conflict with a sibling,
# a disk stop -- is no failure of the issue's work: it requeues the issue and never counts toward SG_REQUEUE_CAP. Its own
# bound, SG_LOOP_REQUEUE_CAP (6), ends the issue needs-human (ADR-0029) with needs-owner and a comment naming the loop cause.
# It writes no Blocked column (#630): the shipped board has none, and janitor rule R turns needs-owner into frozen:owner. `own` makes the janitor's verdict read (the review held the diff) count, whatever the text.
for r in l1 l2 l3 l4; do o=$(w fail 21 $r "the child of run $r never entered its wave: the rebase on wave/$r conflicts: f.py"); done
[ "$(cnt 21)" = 0 ] && [ "$(gl 21)" = 0 ] && case "$o" in *"WAVE STOP"*) false;; *) true;; esac \
  && ok "a child a sibling's conflict kept out of the wave never counts toward the cap" || ko "loop fail: count '$(cnt 21)', '$o'"
echo req > "$T/st/auto/disp-22"
for r in a b c; do SG_REQUEUE_RUN=$r w requeue 22 "ended failed: adapter_error" >/dev/null; done
SG_REQUEUE_RUN=d w requeue 22 "the rebase on wave/x conflicts: f.py" >/dev/null; echo req > "$T/st/auto/disp-22"
o=$(SG_REQUEUE_RUN=e w requeue 22 "DISK STOP: / has 1 GiB free")
[ ! -e "$T/st/auto/disp-22" ] && [ "$(cnt 22)" = 0 ] && case "$o" in *"WAVE REQUEUE #22"*) true;; *) false;; esac \
  && ok "a loop-caused requeue still requeues the issue, with no count" || ko "loop requeue: count '$(cnt 22)', '$o'"
o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m1 "ended failed: merge_conflict"); o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m2 "ended failed: adapter_error")
o=$(SG_LOOP_REQUEUE_CAP=3 w fail 23 m3 "the child of run m3 never entered its wave: the gate lock is held by pid 9; the child enters on a later pass")
c23=$(cat "$T"/st/cq/*23-* 2>/dev/null)
case "$o" in *"WAVE NEEDS-HUMAN #23"*) ok "the loop bound ends the issue needs-human";; *) ko "no loop stop at SG_LOOP_REQUEUE_CAP: '$o'";; esac
! /usr/bin/grep -q ' Blocked 23$' "$T/board.log" && /usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/23/labels -f labels[]=needs-owner' "$T/gh.log" && ! /usr/bin/grep -q 'issues/23/labels -f labels\[\]=frozen:owner' "$T/gh.log" && ok "the loop stop adds needs-owner and writes no Blocked column (#630)" || ko "loop stop: board '$(tr '\n' ' ' < "$T/board.log")', gh '$(/usr/bin/grep 'issues/23' "$T/gh.log")'"
case "$c23" in *"the gate lock is held by pid 9"*) ok "the loop stop comment names the loop cause";; *) ko "loop stop comment: '$c23'";; esac
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
# #682: the cause of a child the wave landed without decides, never the generic text. The janitor names the cause wave.sh
# recorded, or "unknown"; the old generic text and an unknown cause are no loop cause, and a held gate lock and the age cap are.
w fail 26 u1 "the wave of run u1 landed without its child" >/dev/null
w fail 26 u2 "the child of run u2 never entered its wave: unknown" >/dev/null
[ "$(cnt 26)" = 2 ] && [ "$(cnt 26 loop)" = 0 ] && ok "the generic text and an unknown cause count toward the cap" || ko "unknown cause: own '$(cnt 26)', loop '$(cnt 26 loop)'"
w fail 27 g1 "the child of run g1 never entered its wave: the gate slots (caps.gates) are full, held by 1; the child enters on a later pass" >/dev/null
w fail 27 g2 "the child of run g2 never entered its wave: the wave reached its age cap (7200 s): the child's issue goes back to the queue" >/dev/null
[ "$(cnt 27)" = 0 ] && [ "$(cnt 27 loop)" = 2 ] && ok "a held gate and the age cap are loop causes" || ko "loop causes: own '$(cnt 27)', loop '$(cnt 27 loop)'"
# #682: an age-cap requeue reads the stop first: an issue its ledger stopped, or one that carries frozen:owner or needs-owner,
# keeps its dispatch marker and its card; only a free issue goes back to Todo.
SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" stop 'x/y#41' "the gates failed" own >/dev/null; echo req > "$T/st/auto/disp-41"
o=$(w requeue 41 "the wave reached its age cap" cap)
[ -e "$T/st/auto/disp-41" ] && ! /usr/bin/grep -q ' Todo 41$' "$T/board.log" && case "$o" in *"WAVE STOP #41"*) true;; *) false;; esac \
  && ok "an age-cap requeue leaves an issue the ledger stopped where it is" || ko "stopped #41: '$o', board '$(/usr/bin/grep ' 41$' "$T/board.log")'"
SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" stop 'x/y#43' "adapter_error" loop >/dev/null; echo req > "$T/st/auto/disp-43"
o=$(w requeue 43 "the wave reached its age cap" cap)
[ -e "$T/st/auto/disp-43" ] && ! /usr/bin/grep -q ' Todo 43$' "$T/board.log" && ok "an age-cap requeue leaves an issue the loop ledger stopped where it is" || ko "loop-stopped #43: '$o'"
echo req > "$T/st/auto/disp-42"; o=$(w requeue 42 "the wave reached its age cap" cap)
[ -e "$T/st/auto/disp-42" ] && ! /usr/bin/grep -q ' Todo 42$' "$T/board.log" && case "$o" in *"WAVE STOP #42"*frozen:owner*) true;; *) false;; esac \
  && ok "an age-cap requeue leaves an issue frozen for the owner where it is" || ko "frozen #42: '$o', board '$(/usr/bin/grep ' 42$' "$T/board.log")'"
echo req > "$T/st/auto/disp-44"; o=$(w requeue 44 "the wave reached its age cap" cap)
[ ! -e "$T/st/auto/disp-44" ] && /usr/bin/grep -q ' Todo 44$' "$T/board.log" && ok "an age-cap requeue still frees an issue nothing stopped" || ko "free #44: '$o'"
# #1335: a closed issue never goes back to the queue. A capped empty wave landed without a child the owner has closed (no code):
# the requeue names it on one line, leaves its marker and its card alone, and returns 0. An open issue still goes back.
echo req > "$T/st/auto/disp-77"; o77=$(w requeue 77 "the wave reached its age cap (7200 s) and landed without this child: it is back in the queue" cap); rc77=$?
[ "$rc77" = 0 ] && [ -e "$T/st/auto/disp-77" ] && ! /usr/bin/grep -q ' Todo 77$' "$T/board.log" && [ ! -e "$T/st/cq/77-wave$$.md" ] && [ "$(cnt 77)" = 0 ] \
  && ok "a closed issue is not requeued by the age cap: marker and card stay" || ko "closed #77 (rc $rc77): marker $(/bin/ls "$T/st/auto" | tr '\n' ' '), board '$(/usr/bin/grep ' 77$' "$T/board.log")', '$o77'"
case "$o77" in "WAVE KEEP #77: the issue is closed, so "*" does not put it back in the queue"*) ok "the keep is named on one line";; *) ko "no keep line for #77: '$o77'";; esac
echo req > "$T/st/auto/disp-77"; o77=$(w requeue 77 "the local gates failed: x")
[ -e "$T/st/auto/disp-77" ] && [ "$(cnt 77)" = 0 ] && ! /usr/bin/grep -q ' Todo 77$' "$T/board.log" && case "$o77" in *"WAVE KEEP #77"*) true;; *) false;; esac \
  && ok "a closed issue is not requeued by a failure either, and counts nothing" || ko "closed #77, failure: '$o77', count $(cnt 77)"
echo req > "$T/st/auto/disp-78"; o78=$(w requeue 78 "the local gates failed: y")
[ ! -e "$T/st/auto/disp-78" ] && /usr/bin/grep -q ' Todo 78$' "$T/board.log" && case "$o78" in *"WAVE REQUEUE #78"*) true;; *) false;; esac \
  && ok "an issue whose state cannot be read is requeued as before" || ko "unread #78: '$o78', board '$(/usr/bin/grep ' 78$' "$T/board.log")'"
case "$o78" in *"state could not be read"*) ok "the unread state is named";; *) ko "no note that #78's state was not read: '$o78'";; esac
# #2053: the issue's own second failed dispatch hands it to HAL9K. The hand hold (squad.handhold) writes ready-for-HAL9K,
# one SQUAD-HAND comment lists the runs counted, and one WAVE HAND line is logged. A loop-caused requeue counts nothing
# toward it, the route is off by SG_HAND_AT=off, two open hal9k holds fill it, and the next failure after it reaches the stop.
hw() { SG_HAND_AT=${HA:-2} SG_SQUAD="$(cd "$d/../../.." && pwd)" PATH="$T/bin:$PATH" bash "$T/s/wave.sh" "$@"; }
held() { [ -e "$T/st/auto/hold-$1" ] && /usr/bin/grep -q '^by hal9k' "$T/st/auto/hold-$1"; }
hlabel() { /usr/bin/grep -Fq -- "repos/x/y/issues/$1/labels -f labels[]=ready-for-HAL9K" "$T/gh.log"; }
o1=$(hw requeue 61 "the review asked for changes: a"); o2=$(hw requeue 61 "the review asked for changes: b")
case "$o1" in *"WAVE HAND #61"*) ko "the first failure handed #61 over: '$o1'";; *) ok "the first failure does not hand the issue over";; esac
case "$o2" in *"WAVE HAND #61: 2 failed dispatches"*) ok "the second failure logs one WAVE HAND line";; *) ko "no hand line for #61: '$o2'";; esac
held 61 && hlabel 61 && ok "the second failure takes a hal9k hold and writes ready-for-HAL9K" || ko "no hal9k hold on #61: $(cat "$T/st/auto/hold-61" 2>/dev/null)"
hc=$(/usr/bin/grep -l '^SQUAD-HAND:' "$T"/st/cq/*#61-wavehand* 2>/dev/null | head -1)
[ -n "$hc" ] && [ "$(/usr/bin/grep -c '^- ' "$hc")" = 2 ] && /usr/bin/grep -q 'changes: a' "$hc" && ok "one SQUAD-HAND comment lists both runs and their reasons" || ko "hand comment: '$(cat "$hc" 2>/dev/null)'"
o3=$(hw requeue 61 "the review asked for changes: c")
case "$o3" in *"WAVE STOP #61"*) ok "a failure after the hand route reaches the stop at the cap";; *) ko "#61 past the hand route: '$o3'";; esac
hh() { SG_STATE="$T/st" SG_REPO=x/y PYTHONPATH="$(cd "$d/../../.." && pwd)" PATH="$T/bin:$PATH" python3 -m squad.handhold "$@" >/dev/null 2>&1; }
hh release --by hal9k --to Todo 61 && ! held 61 && ok "the hand hold releases (the expiry path frees the issue the same way)" || ko "#61 is still held after its release"
for i in 1 2; do o=$(hw requeue 62 "caps.gates is full (3 slots), held by x"); done
case "$o" in *"WAVE HAND #62"*) ko "a loop-caused requeue handed #62 over";; *) ! held 62 && ok "a loop-caused requeue never counts toward the hand route" || ko "#62 is held";; esac
for i in 1 2; do o=$(HA=off hw requeue 63 "the local gates failed: z"); done
case "$o" in *"WAVE HAND"*) ko "SG_HAND_AT=off handed #63 over";; *) ! held 63 && ok "SG_HAND_AT=off turns the hand route off" || ko "#63 is held with the route off";; esac
o1=$(hw fail 64 run-64a "the review asked for changes: f"); o2=$(hw fail 64 run-64b "the review asked for changes: g")
case "$o2" in *"WAVE HAND #64"*) held 64 && ok "the janitor's fail path hands the issue over at two" || ko "#64 logged a hand line with no hold";; *) ko "fail path: '$o2'";; esac
hh hold --by hal9k 66; held 64 && held 66 || ko "the fixture needs two open hal9k holds (64, 66)"
hw requeue 65 "the local gates failed: p" >/dev/null; o=$(hw requeue 65 "the local gates failed: q")
case "$o" in *"WAVE HAND-FULL #65"*) ! held 65 && ok "two open hal9k holds fill the hand route, and the issue stays in the queue" || ko "#65 was held past the bound";; *) ko "full hand route: '$o'";; esac
hw requeue 65 "the local gates failed: r" | /usr/bin/grep -q "WAVE STOP #65" && ok "with the hand route full, the cap still stops the issue" || ko "#65 did not stop at the cap"
# #2220: a stale hold or a hold on a closed issue never fills the hand route; only a live hold counts.
hh release --by hal9k --to Todo 64 66
for s in 70 71; do printf 'by hal9k\nsince 2020-01-01T00:00:00Z\nuntil 2020-01-01T01:00:00Z\n' > "$T/st/auto/hold-$s"; done
hw requeue 72 "the local gates failed: s" >/dev/null; o72=$(hw requeue 72 "the local gates failed: t")
case "$o72" in *"WAVE HAND #72"*) held 72 && ok "two stale hal9k holds leave the hand route open, and the issue is handed over" || ko "#72 logged a hand line with no hold";; *) ko "two stale holds filled the hand route: '$o72'";; esac
rm -f "$T/st/auto/hold-70" "$T/st/auto/hold-71"; hh release --by hal9k --to Todo 72
hh hold --by hal9k 73 77; : > "$T/closed-73"
olive=$(SG_STATE="$T/st" PYTHONPATH="$(cd "$d/../../.." && pwd)" python3 -m squad.handhold holds --live --closed 73 77 2>/dev/null)
hw requeue 79 "the local gates failed: u" >/dev/null; o79=$(hw requeue 79 "the local gates failed: v")
case "$o79" in *"WAVE HAND #79"*) held 79 && [ -z "$olive" ] && ok "a hold on a closed issue does not fill the hand route" || ko "#79 logged a hand line with no hold, or a closed hold is live: '$olive'";; *) ko "a closed hold filled the hand route: '$o79'";; esac
rm -f "$T/closed-73"; hh release --by hal9k --to Todo 73 77 79
hh hold --by hal9k 73 74
hw requeue 75 "the local gates failed: w" >/dev/null; o75=$(hw requeue 75 "the local gates failed: x")
case "$o75" in *"WAVE HAND-FULL #75"*) ! held 75 && ok "two live hal9k holds print HAND-FULL and the issue stays in the queue" || ko "#75 was held past the bound";; *) ko "two live holds did not fill the route: '$o75'";; esac
hh release --by hal9k --to Todo 73 74
# The wave's age cap sends a child's issue back to the queue, and the loop then builds it again from the base: the commits
# the child made reach no branch, so the cap pushes the child's head to salvage/<n> first and names that branch (#2030).
o="$T/orig2030.git"; /usr/bin/git init -q --bare "$o"
r="$T/co2030/run2030"; /usr/bin/git clone -q "$o" "$r"; /usr/bin/git -C "$r" checkout -q -b dev
/usr/bin/git -C "$r" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "Refs #2030 base"; /usr/bin/git -C "$r" push -q origin dev
wt="$r/ws/target-c"; /usr/bin/git -C "$r" worktree add -q -b child/2030 "$wt" dev
/usr/bin/git -C "$wt" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "Refs #2030 the child's work"
h2030=$(/usr/bin/git -C "$wt" rev-parse HEAD); mkdir -p "$r"; echo "$r" > "$T/st/active-runs.txt"
printf '%s\n' '{"event": "spawn_accepted", "child": "c", "envelope": "route", "definition": "GitHub issue #2030"}' > "$r/record.jsonl"
out=$(SG_STATE="$T/st" SG_BASE=dev SG_REPO=x/y bash "$T/s/wave.sh" salvage run2030 2030 2>&1)
[ "$out" = "salvage/2030" ] && ok "a capped child's head is pushed and its branch named (#2030)" || ko "salvage named '$out'"
[ "$(/usr/bin/git -C "$o" rev-parse -q --verify refs/heads/salvage/2030)" = "$h2030" ] && ok "the salvage branch stands on the child's head (#2030)" || ko "salvage/2030 is not the child's head"
/usr/bin/git -C "$wt" -c user.name=t -c user.email=t@t commit -q --allow-empty --amend -m "Refs #2030 base"
out=$(SG_STATE="$T/st" SG_BASE=dev SG_REPO=x/y bash "$T/s/wave.sh" salvage run2031 2031 2>&1)
[ -z "$out" ] && ok "a run that names no child of the issue salvages nothing (#2030)" || ko "a child of another issue: '$out'"
cat "$T.err" >&3
/usr/bin/grep -Eq 'command not found|No such file' "$T.err" && ko "the run printed a missing command or file: $(/usr/bin/grep -E 'command not found|No such file' "$T.err" | tr '\n' ' ')"
rm -rf "$T" "$T.err"; exit $fail
