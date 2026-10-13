#!/bin/bash
# Regression test for the watch cycle's lanes (#1845): the cycle must not pay, inside itself, for work that does not have to be
# synchronous, and a step must not run twice when both lanes are up. usage: bash test-watch-cycle.sh [path/to/watch.sh]
# Fixture: a copy of watch.sh in a temp scripts dir with every step stubbed, a `sleep` that returns at once (a cycle then costs its
# steps, not 90 s of naps) and a `gh` that answers nothing. The last step of the cycle (vendor-guard) ends the run after the number of
# cycles the case asked for, so no case waits for the real cadence. No network, no model, no emulator.
# Hermetic first: the live loop exports its own SG_* to every run it dispatches — a dispatch sets SG_SINGLE_<stage> while it re-execs itself
# through single.sh — and a leaked SG_SINGLE_* would let a stub walk past the lock it is supposed to hold. Drop them, as shell-suites.sh
# does for every suite (#1410); the fixture sets what it needs below.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/watch.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; B=$T/bin
mkdir -p "$S" "$ST" "$B" "$T/co" "$T/fork"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log REC_SLEEP=5
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/"; cp "$d/models.json" "$S/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$S/"   # the cycle's reader and its throttle travel with it since #1893
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001   # every case below is its own GitHub: a list kept, or a cooldown set, by the last case would answer this one (#45)
EOF
printf '#!/bin/bash\nexit 0\n' > "$B/gh"        # no open PR, no revci summary, nothing to move
printf '#!/bin/bash\n[ -n "$REAL_SLEEP" ] && exec /bin/sleep "$@"\nexit 0\n' > "$B/sleep"   # REAL_SLEEP=1 keeps a stub slow on purpose
chmod +x "$B/gh" "$B/sleep"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
for s in retryfix.sh autohunt.sh autopilot.sh heartbeat.sh wtclean.sh janitor.sh forkclose.sh publish-landing.sh; do
  printf '#!/bin/bash\necho %s >> "%s"\n' "${s%.sh}" "$STEPS" > "$S/$s"; chmod +x "$S/$s"; done
printf 'open("%s","a").write("childcc\\n")\n' "$STEPS" > "$S/childcc.py"
for p in forkrescue forkstrand; do printf 'open("%s","a").write("%s\\n")\n' "$STEPS" "$p" > "$S/$p.py"; done
# reconcile.py blocks for REC_SLEEP: the case times the cycle around it (a detached pass costs the cycle nothing) and reads its own log
cat > "$S/reconcile.py" <<'PY'
import os, time
time.sleep(float(os.environ.get("REC_SLEEP", "5")))
open(os.environ["STEPS"], "a").write("reconcile\n")
print("MOVE #1 Todo -> Done")   # watch.sh prefixes the pass's stream with BOARD when it files it
PY
# the dispatch passes self-lock exactly as the real autofrontier.sh/autobug.sh do (single.sh <stage> around the whole pass)
for s in autofrontier autobug; do u=$(echo "$s" | tr a-z A-Z)
  cat > "$S/$s.sh" <<EOF
#!/bin/bash
here="\$(cd "\$(dirname "\$0")" && pwd)"
[ -n "\${SG_SINGLE_$u:-}" ] || exec env SG_SINGLE_$u=1 bash "\$here/single.sh" $s bash "\$here/$s.sh" "\$@"
echo "$s" >> "$STEPS"
[ -n "\${AF_HOLD:-}" ] && REAL_SLEEP=1 sleep "\$AF_HOLD"
exit 0
EOF
  chmod +x "$S/$s.sh"; done
# the cycle's last step ends the run: the case asks for N cycles and this counts the cycles the body actually ran (works with or
# without the once-mode, so a RED run against the base terminates too)
cat > "$S/vendor-guard.sh" <<EOF
#!/bin/bash
echo vendorguard >> "$STEPS"
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && kill -TERM "\$(/bin/ps -o ppid= -p "\$PPID" | tr -d ' ')" 2>/dev/null
exit 0
EOF
count() { local c; c=$(/usr/bin/grep -cx "$1" "$STEPS" 2>/dev/null); echo "${c:-0}"; }
waitfor() { local i=0; while [ $i -lt "$2" ]; do /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null && return 0; /bin/sleep 0.1; i=$((i+1)); done; /usr/bin/grep -q "$1" "$STEPS" 2>/dev/null; }
run_cycles() { # run_cycles <n> [VAR=val...]  starts watch.sh; the vendor-guard stub ends it after <n> cycles
  local n=$1; shift; : > "$STEPS"; echo 0 > "$T/cyc"
  env "$@" SG_TEST_CYCLES=$n bash "$S/watch.sh" > "$T/watch.out" 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null && [ $i -lt 400 ]; do /bin/sleep 0.1; i=$((i+1)); done
  kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null; rm -f "$ST/fastloop.pid" 2>/dev/null; return 0
}
lane_up() { printf '#!/bin/bash\n/bin/sleep 60\n' > "$T/fakefast.sh"; bash "$T/fakefast.sh" & FPID=$!; echo $FPID > "$ST/fastloop.pid"; }
lane_down() { [ -n "$FPID" ] && kill "$FPID" 2>/dev/null; FPID=; rm -f "$ST/fastloop.pid"; }

# 1) fast lane up: the cycle leaves the dispatch passes to it, so neither pass runs twice
lane_up; run_cycles 1
[ "$(count autofrontier)" = 0 ] && ok "fast lane up: the cycle does not dispatch the frontier" || ko "the cycle dispatched the frontier with the fast lane up ($(count autofrontier) passes)"
[ "$(count autobug)" = 0 ] && ok "fast lane up: the cycle does not dispatch the bugs" || ko "the cycle dispatched the bugs with the fast lane up ($(count autobug) passes)"
[ "$(count childcc)" = 0 ] && [ "$(count autopilot)" = 0 ] && ok "fast lane up: the cycle leaves childcc and autopilot to it" || ko "the cycle ran the fast lane's own steps"

# 2) fast lane down: the standalone cycle still dispatches, once per cycle
lane_down; run_cycles 1
[ "$(count autofrontier)" = 1 ] && ok "fast lane down: the cycle dispatches the frontier" || ko "no frontier dispatch standalone ($(count autofrontier))"
[ "$(count autobug)" = 1 ] && ok "fast lane down: the cycle dispatches the bugs" || ko "no bug dispatch standalone ($(count autobug))"

# 3) wtclean is not a per-cycle step: at most one run in three cycles, while a per-cycle step keeps its cadence
run_cycles 3
[ "$(count wtclean)" = 1 ] && ok "wtclean runs once in three cycles" || ko "wtclean ran $(count wtclean)× in 3 cycles"
[ "$(count janitor)" = 3 ] && ok "a per-cycle step still runs every cycle" || ko "janitor ran $(count janitor)× in 3 cycles"
[ "$(count publish-landing)" = 3 ] && ok "the landing's publish step runs every cycle (#31); it is a no-op until a green landing changed what the loop runs" || ko "publish-landing ran $(count publish-landing)× in 3 cycles"

# 4) reconcile does not hold the cycle, writes its own log, and stays out of cycle.log
t0=$(date +%s); run_cycles 1; el=$(( $(date +%s) - t0 ))
[ "$el" -lt 4 ] && ok "the cycle does not wait for the reconcile pass (${el}s with a 5 s pass)" || ko "the cycle waited for reconcile (${el}s)"
for _ in $(seq 1 150); do /usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && break; /bin/sleep 0.1; done
/usr/bin/grep -q 'BOARD MOVE' "$ST/reconcile.log" 2>/dev/null && ok "the detached pass writes its own log ($ST/reconcile.log)" || ko "no BOARD line in reconcile.log"
/usr/bin/grep -q 'BOARD' "$ST/cycle.log" 2>/dev/null && ko "reconcile output still lands in cycle.log" || ok "cycle.log no longer carries the reconcile pass"

# 5) the fast lane dispatches, and a second pass does not overlap the first
F2=$T/fastscripts; mkdir -p "$F2"; cp "$d/fastloop.sh" "$d/single.sh" "$F2/"; cp "$S/env.sh" "$S/childcc.py" "$S/autopilot.sh" "$S/autofrontier.sh" "$S/autobug.sh" "$F2/"
: > "$STEPS"; rm -f "$ST/fastloop.pid"
AF_HOLD=3 SG_FASTLOOP_ONCE=1 bash "$F2/fastloop.sh" > "$T/fast.out" 2>&1
AF_HOLD=3 SG_FASTLOOP_ONCE=1 bash "$F2/fastloop.sh" > "$T/fast2.out" 2>&1
waitfor autofrontier 100; waitfor autobug 100
[ "$(count autofrontier)" = 1 ] && ok "the fast lane dispatches the frontier" || ko "the fast lane dispatched $(count autofrontier) frontier passes"
[ "$(count autobug)" = 1 ] && ok "the fast lane dispatches the bugs" || ko "the fast lane dispatched $(count autobug) bug passes"
[ "$(count childcc)" = 2 ] && [ "$(count autopilot)" = 2 ] && ok "the fast lane keeps childcc and autopilot" || ko "childcc/autopilot: $(count childcc)/$(count autopilot)"
/bin/sleep 4
[ "$(count autofrontier)" = 1 ] && ok "a pass already running is not started again (single.sh lock)" || ko "the frontier pass overlapped itself ($(count autofrontier))"

# 6) (#1870) a refused gh api is not the open-PR list. gh writes the error body to stdout and exits non-zero, and it does
# not honour -q on a failed request, so the cycle used to read the words of the JSON message as PR numbers: one phantom
# `NEW PR #<token> {` line per word of the body, and the same body entered the set of PRs already announced for the life
# of the process. The fixture is the rate-limit body of 2026-10-12, the one behind the sibling fix in autobug (#1855).
# gh names a fresh request id in every refusal, and that is the token that became the phantom PR number: the copy the
# startup line had cached no longer matched the words of the next body.
cat > "$B/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*|*graphql*) i=\$(( \$(cat "$T/reqid" 2>/dev/null || echo 0) + 1 )); echo "\$i" > "$T/reqid"
    printf '{"message":"API rate limit exceeded for user ID 122891372. If you reach out to GitHub Support for help, please include the request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2%s and timestamp 2026-10-12 05:12:10 UTC. For more on scraping GitHub and how it may affect your rights, please review our Terms of Service (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)","documentation_url":"https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#rate-limiting","status":"403"}\n' "\$i"
    exit 1 ;;   # the rate limit: the error body on stdout, non-zero exit
  *) echo "" ;;
esac
J
chmod +x "$B/gh"; run_cycles 2
n=$(/usr/bin/grep -c '^NEW PR #' "$T/watch.out")
[ "$n" = 0 ] && ok "6 a refused gh api prints no NEW PR line" \
  || ko "6 a refused gh api printed $n phantom NEW PR line(s): '$(/usr/bin/grep '^NEW PR #' "$T/watch.out" | head -3 | tr '\n' '|')'"
# The refusal must reach the log with GitHub's own words (#1893): every call site reads the list inside `$( )`, and a
# subshell cannot set a variable, so the message travelled nowhere and the line ended in an empty tail --
# `the open-PR list is not a list: ;`. The refusal now travels in stdout, which a subshell can capture.
/usr/bin/grep -q 'the open-PR list is not readable' "$T/watch.out" \
  && ok "6 the refused open-PR list is reported" || ko "6 the refused open-PR list is silent: '$(cat "$T/watch.out")'"
/usr/bin/grep -q 'the open-PR list is not readable.*API rate limit exceeded' "$T/watch.out" \
  && ok "6 the log names GitHub's own message" || ko "6 the log does not carry GitHub's message: '$(/usr/bin/grep 'open-PR list' "$T/watch.out" | head -1)'"
/usr/bin/grep -q 'the open-PR list is not readable.*request id 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2' "$T/watch.out" \
  && ok "6 the log names GitHub's request id" || ko "6 the log does not carry the request id: '$(/usr/bin/grep 'open-PR list' "$T/watch.out" | head -1)'"
/usr/bin/grep -q 'the open-PR list is not readable: *$' "$T/watch.out" \
  && ko "6 the refusal line still ends in an empty tail (the message was lost again)" \
  || ok "6 the refusal line is never empty after the colon"

# 6b) an error body that happens to name a real open PR must not put it in the set: the refusal is not a list, so the
# number in it is not a PR number either. The stub refuses the list on the first cycle and answers PR #7 from the second,
# and the title of #7 is only served once the list has actually answered -- so the announcement can only come from a
# successful read.
# The list carries the PR's branch, title and body (#45): the announcement is read from it, with no read of its own.
# The stub answers the RAW body now (#1893): ghread.py owns the read and the caller's `jq` does the projection, so `gh`
# is stubbed at the wire and no longer at the projection (which is what `-q` used to fold into the same fake).
cat > "$B/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*|*graphql*) [ "\$(cat "$T/cyc" 2>/dev/null || echo 0)" -ge 1 ] && { echo '[{"number":7,"title":"a real open PR","body":"Refs #1870","head":{"ref":"gui/1870"}}]'; touch "$T/list-ok"; exit 0; }
    i=\$(( \$(cat "$T/reqid" 2>/dev/null || echo 0) + 1 )); echo "\$i" > "$T/reqid"
    printf '{"message":"API rate limit exceeded for user ID 122891372; retry after 7 seconds (request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2%s)","status":"403"}\n' "\$i"
    exit 1 ;;
  *pulls/7*) [ -e "$T/list-ok" ] && echo '{"head":{"ref":"gui/1870"},"title":"a real open PR","body":"Refs #1870"}' || echo "" ;;
  *) echo "" ;;
esac
J
chmod +x "$B/gh"; run_cycles 2
n=$(/usr/bin/grep '^NEW PR #' "$T/watch.out" | /usr/bin/grep -vc '^NEW PR #[0-9][0-9]* ')
[ "$n" = 0 ] && ok "6b every announced PR number is decimal" \
  || ko "6b $n announced PR number(s) are not a decimal: '$(/usr/bin/grep '^NEW PR #' "$T/watch.out" | head -3 | tr '\n' '|')'"
/usr/bin/grep -q '^NEW PR #7 gui/1870 a real open PR' "$T/watch.out" \
  && ok "6b a refused body never enters the PR set: the real open PR is still announced" \
  || ko "6b the real open PR was never announced from a successful read: '$(cat "$T/watch.out")'"
# 6c) (#1893, AC5) a refused read leaves the caller's previous knowledge intact instead of clearing it. The list refuses,
# answers #7 (announced once), refuses again, and answers #7 again: the set of PRs already announced has to survive the
# refusal between the two answers, so the second answer announces nothing. A refusal that cleared the set would announce
# #7 a second time -- which is the phantom-dispatch half of the same bug.
cat > "$B/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*|*graphql*) case "\$(cat "$T/cyc" 2>/dev/null || echo 0)" in
      1|3) echo '[{"number":7,"title":"a real open PR","body":"Refs #1893","head":{"ref":"gui/1893"}}]'; exit 0 ;;
    esac
    printf '{"message":"API rate limit exceeded for user ID 122891372 (request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A)","status":"403"}\n'
    exit 1 ;;
  *pulls/7*) echo '{"head":{"ref":"gui/1893"},"title":"a real open PR","body":"Refs #1893"}' ;;
  *) echo "" ;;
esac
J
chmod +x "$B/gh"; run_cycles 4
n=$(/usr/bin/grep -c '^NEW PR #7 gui/1893' "$T/watch.out")
[ "$n" = 1 ] && ok "6c a refusal keeps the set of PRs already announced (7 announced once across two answers and a refusal)" \
  || ko "6c the PR was announced $n×: a refusal cleared (or never kept) the caller's previous knowledge: '$(/usr/bin/grep '^NEW PR #' "$T/watch.out" | tr '\n' '|')'"
/usr/bin/grep -q 'the open-PR list is not readable' "$T/watch.out" \
  && ok "6c the refusal in between is still named in the log" || ko "6c the refusal between the two answers was silent"

# 7) One watch.sh per repository state directory. A live holder refuses another supervisor for the same repository,
# while a separate repository uses its own lock. A holder that is gone is taken over.
WL="$ST/locks/watch"
rm -rf "$WL"; mkdir -p "$WL"; echo $$ > "$WL/pid"          # held by this shell: alive, so the next start must refuse
: > "$STEPS"
env SG_TEST_CYCLES=1 bash "$S/watch.sh" > "$T/lock.out" 2>&1
case "$(cat "$T/lock.out")" in *"watch.sh already running pid=$$"*) ok "7 a second watch.sh on the same path refuses by name";; *) ko "7 the second start was not refused: '$(cat "$T/lock.out")'";; esac
[ ! -s "$STEPS" ] && ok "7 and it read nothing and ran no step" || ko "7 the refused start still ran: $(tr '\n' ' ' < "$STEPS")"
echo 999999 > "$WL/pid"                                    # a holder that is gone: the lock is stale
: > "$STEPS"
env SG_TEST_CYCLES=1 bash "$S/watch.sh" > "$T/takeover.out" 2>&1 & local_p=$! i=0
while kill -0 "$local_p" 2>/dev/null && [ $i -lt 100 ]; do /bin/sleep 0.1; i=$((i+1)); done
kill -TERM "$local_p" 2>/dev/null; wait "$local_p" 2>/dev/null
[ -s "$STEPS" ] && ok "7 a stale lock (its holder is gone) is taken over: the cycle runs" || ko "7 a stale lock stopped the loop: '$(cat "$T/takeover.out")'"
rm -rf "$WL"

rm -rf "$T"; exit $fail
