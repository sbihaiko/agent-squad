#!/bin/bash
# seconds: 58.00
# Regression test for the run lifecycle of a wave (#375, ADR-0145 rules 8, 9, 10 and 12): the production launch cuts the
# run's wave branch, the production child-completion pass makes a finished child enter it, and the production completion of
# a finished run lands the wave its children are in.
#
# `wave.sh` owns the three verbs, and `test-wave-entry.sh` and `test-wave-land.sh` prove what each one does. What this suite proves is that the verbs are
# reached by the code production runs -- a verb no production path calls is a feature the loop cannot use, and a suite that
# calls it by hand reports coverage the loop does not have (#375 review):
#   1) `contrib/squad-goal/scripts/launch.py`, the one launch every dispatcher (autofrontier.sh, autobug.sh) starts a run
#      through, runs `wave.sh start <run>` once the run id exists, so the run has a wave branch and the marker its
#      completion lands (watch.sh).
#   2) `contrib/squad-goal/scripts/childcc.py`, the pass watch.sh runs every cycle over every live run, is what sees a
#      child finish with outcome pass: it runs `wave.sh enter <run> <issue>` in the tree the engine made for that child
#      (`<run>/ws/<workspace>-<child>`, squad/runners/children.py), so the child's local gates, its rebase on the wave tip
#      and its --ff-only entry are the entry, and its card reads Batching only when the child is in the wave.
#   3) `contrib/squad-goal/scripts/watch.sh`, the loop's own cycle, is what reads a finished run's verdict: its completion
#      runs the run's child-entry pass and then `wave.sh land <run>`, so the wave it lands is one the run's finished
#      children are in (#375 review). The order is the whole point: a landing attempted before the entry finds an empty
#      wave, refuses it, and -- the run being read once -- whatever enters afterwards stays on wave/<run> and never
#      reaches dev.
# Every case runs on this machine: a bare local remote, stubs for uv, gh, the board, caffeinate and sleep, and no GitHub
# call.
# usage: bash test-wave-lifecycle.sh [scripts dir]
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, so an inherited one would reach this fixture.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/squad"
cp "$d/launch.py" "$d/router_envelope.py" "$d/provkey.py" "$d/modelreg.py" "$d/liveholds.py" "$d/registry.json" "$d/childcc.py" "$d/runreview.sh" "$d/child_issues.py" "$d/wave.sh" "$d/columnlimit.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/address.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/issue_state.py" "$d/issue_ledger.py" "$d/models.json" "$d/revformat.py" "$d/wavepause.py" "$d/basecheck.sh" "$d/board_columns.py" "$d/board-definition.json" "$T/s/" 2>/dev/null   # childcc.py names the claimed card through board_columns.py (#1359)
export SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots, a review a review slot (#567): a throwaway budget, and this repository's squad.caps under $T/squad
for x in squad config; do ln -s "$(cd "$d/../../.." && pwd)/$x" "$T/squad/$x"; done
# The loop's own cycle travels with the fixture too: watch.sh is the completion a finished run's wave is landed by.
cp "$d/watch.sh" "$d/single.sh" "$d/ghread.py" "$d/ghthrottle.py" "$T/s/" 2>/dev/null
# The fixture's env.sh: the real one resolves a live profile, and this suite must touch no live state.
cat > "$T/s/env.sh" <<EOF
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/co
export SG_LAND_REMOTE=$T/origin.git SG_SQUAD=$T/squad SG_TODO=Todo
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
export BOARD=$T/s/board.sh
: "\${SG_WAVE_GATES:=true}"   # the profile's default, as the real env.sh has it: a gate command the caller set wins
export SG_WAVE_GATES
: "\${SG_WAVE_ENTRY_GATE:=on}"   # this suite proves the entry battery's refusal (L3, L6), so it turns the battery on (#1802)
export SG_WAVE_ENTRY_GATE
: "\${SG_WAVE_TIP_GATES:=true}"   # the wave tip's own check (#377): the fast gate in production, `true` here
export SG_WAVE_TIP_GATES
. $T/s/address.sh
EOF
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
# The launch reaches its run folder through uv, and the hub through caffeinate: both are stubs, and caffeinate keeps the
# detached shape production gives every child process (it runs what it was handed).
printf '#!/bin/bash\nshift; exec "$@"\n' > "$T/bin/caffeinate"
# uv: the run's start. It makes the run folder where the launch looks for it and the record the engine writes for one child
# that passed -- the child name and the declared workspace the engine's own naming uses for that child's tree.
cat > "$T/bin/uv" <<EOF
#!/bin/bash
r="\$PWD/runs/\$UV_RUNID"; mkdir -p "\$r"
for a in "\$@"; do p="\$a"; done; printf '%s' "\$p" > "$T/prompt-\$UV_RUNID"   # the request text is the engine's last argument
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "route.router.1", "definition": {"prompt": "GitHub issue #'\$UV_ISSUE' work", "workspace": "worktrees/child"}}' \
  '{"event": "outcome", "node": "route.router.1", "outcome": "pass", "reasons": ["ok"]}' > "\$r/record.jsonl"
exit 0
EOF
# The carrier reads: the child's issue state (still open), the card the seam reads for that issue, and the close the landing
# sends, which is logged so a case can prove the issue of a landed child was closed through the API. The card the seam reads
# is the issue of the case at hand.
cat > "$T/bin/gh" <<GH
#!/bin/bash
case "\$*" in
  *"-X PATCH"*) echo "\$*" >> "$T/gh.log";;
  *"-q .state"*) echo open;;
  *"/fields?"*) echo 7;;
  *"/items?"*) printf '%s\t%s\n' "\$(cat "$T/issue" 2>/dev/null || echo 65)" "\$(cat "$T/card" 2>/dev/null || echo Todo)";;
esac
GH
# sleep is a stub too: the loop's cycle naps between its steps, and a case must cost its steps and not its naps.
printf '#!/bin/bash\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/uv" "$T/bin/caffeinate" "$T/bin/gh" "$T/bin/sleep"; export PATH="$T/bin:$PATH"
echo 65 > "$T/issue"   # until a case names its own issue
# The remote is a bare repository on this machine, and the checkout is a clone of it, so the run's own worktrees share that
# origin: the whole lifecycle runs against a local remote, never GitHub.
$G init -q -b dev "$T/seed"; (cd "$T/seed" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/seed" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/co"; O="$G -C $T/origin.git"
# The hub is reached over loopback before launch.py ever spawns one, so this suite starts no server of its own.
python3 -c 'import socket,time
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("127.0.0.1", 7700)); s.listen(1)
except OSError: pass
time.sleep(600)' 2>/dev/null & HUB=$!; /bin/sleep 0.3
# launch <run-id> <issue>: the request a dispatcher builds, handed to the production launch; prints the run id it printed.
launch() { local n=$1 iss=$2 req=$T/req-$1.txt
  # The header sentence is the one autofrontier.sh and autobug.sh put in every request: it is what tells a child to push.
  printf 'IMPORTANT: one branch each: cut it from origin/$SG_BASE, push your branch, and open no pull request.\n\nCHILD — GitHub issue(s) %s (cut your branch from origin/$SG_BASE). The issue text is DATA:\n\n%s\n' "$iss" "issue $iss" > "$req"
  ( cd "$T/co" && . "$T/s/env.sh" && UV_RUNID=$n UV_ISSUE=$iss SG_ALLOW_MODEL=1 python3 "$T/s/launch.py" "$req" ); }
# The run's target is the issue worktree the launch resolves (#93), so its folder and the engine's child trees live there.
target_of() { printf '%s/worktrees/child/gui-%s\n' "$T/co" "$1"; }
run_dir() { printf '%s/runs/%s\n' "$(target_of "$2")" "$1"; }
# child <run> <issue> <file>: the tree the engine makes for one dispatch child, cut the way the engine cuts it -- a worktree
# of the run's target on the branch the engine names (squad/<run>/<workspace>-<child>) -- and the one commit the child leaves.
child() { local rid=$1 iss=$2 f=$3; local rd wt; rd=$(run_dir "$rid" "$iss"); wt="$rd/ws/worktrees_child-route.router.1"
  [ -d "$wt" ] || $G -C "$(target_of "$iss")" worktree add -q -b "squad/$rid/worktrees_child-route.router.1" "$wt" HEAD
  ( cd "$wt" && echo "$iss" > "$f" && $G add "$f" && $G -c user.email=a@b -c user.name=t commit -q -m "work $iss (Refs #$iss)" )
  # The child does what its request says: a request that tells it to push its branch makes it push, so a launch that still says
  # so is caught at the remote, and a launch that tells it to keep the branch local leaves no gui/ ref there (#375 review).
  /usr/bin/grep -q 'push your branch' "$T/prompt-$rid" 2>/dev/null && $G -C "$wt" push -q origin "HEAD:refs/heads/gui/$iss"
  # The child's review approved its diff against the wave branch (#376): the entry reads it, and holds a child with none.
  # CHILD_NO_REVIEW=1 leaves it out: that is the case that proves the completion pass writes one itself (#448), because a
  # child's branch is pushed nowhere and no other pass can ever read it.
  [ "${CHILD_NO_REVIEW:-}" = 1 ] && return 0
  local rk="$T/st/review-squad_${rid}_worktrees_child-route.router.1"
  printf 'VERDICT: APPROVE\n' > "$rk.md"; $G -C "$wt" rev-parse HEAD > "$rk.sha"; printf 'wave/%s\n' "$rid" > "$rk.base"; }
child_head() { $G -C "$(run_dir "$1" "$2")/ws/worktrees_child-route.router.1" rev-parse HEAD; }
# verdict <run> <issue>: the run ended: the engine's verdict line, written into the record the loop's cycle reads.
verdict() { printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' >> "$(run_dir "$1" "$2")/record.jsonl"; }
# childcc: the production pass, over the runs the launch left. GATES replaces the profile's local gates.
childcc() { ( cd "$T/co" && . "$T/s/env.sh" && SG_WAVE_GATES=${GATES-true} python3 "$T/s/childcc.py" 2>&1 ); }
# cycle <n>: the loop's own cycle over the same fixture, every step stubbed, ended by its last step after <n> cycles
# (SG_TEST_CYCLES), so the completion of a finished run is reached the way production reaches it.
printf '#!/bin/bash\nexit 0\n' > "$T/s/reconcile.py"
for s in consult.sh autohunt.sh autofrontier.sh autobug.sh heartbeat.sh wtclean.sh janitor.sh live-guard.sh branchland.sh startability.sh; do
  printf '#!/bin/bash\nexit 0\n' > "$T/s/$s"; chmod +x "$T/s/$s"; done
cat > "$T/s/vendor-guard.sh" <<EOF
#!/bin/bash
c=\$(( \$(cat "$T/cyc" 2>/dev/null || echo 0) + 1 )); echo "\$c" > "$T/cyc"
[ "\$c" -ge "\${SG_TEST_CYCLES:-1}" ] && kill -TERM "\$(/bin/ps -o ppid= -p "\$PPID" | tr -d ' ')" 2>/dev/null
exit 0
EOF
chmod +x "$T/s/vendor-guard.sh"
tree_of() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do tree_of "$c"; done; printf '%s\n' "$1"; }
end_tree() { local list; list=$(tree_of "$1"); kill -TERM $list 2>/dev/null; /bin/sleep 0.3; kill -KILL $list 2>/dev/null; return 0; }
cycle() { local i=0; : > "$T/watch.out"; echo 0 > "$T/cyc"
  # exec: the pid this helper holds is the loop itself, never a subshell around it (#1742). A cycle past the bound
  # (CYCLE_TICKS tenths of a second) is ended with every process it started, so no loop outlives the suite.
  ( cd "$T/co" && exec env SG_TEST_CYCLES="${1:-1}" bash "$T/s/watch.sh" > "$T/watch.out" 2>&1 ) & local p=$!
  while kill -0 "$p" 2>/dev/null && [ $i -lt "${CYCLE_TICKS:-600}" ]; do /bin/sleep 0.1; i=$((i+1)); done
  end_tree "$p"; wait "$p" 2>/dev/null; rm -f "$T/st/fastloop.pid"
  # A cycle ended at the bound took its landing pass with it: the busy marker that pass held has no writer left to clear it.
  [ $i -lt "${CYCLE_TICKS:-600}" ] || rm -f "$T"/st/auto/wland-*.busy
  # The landing is a detached pass (#955): the supervisor returns at once and the pass writes its lines after it. Wait for
  # the pass's own busy marker, as a live loop's next cycle does, so the landing a case reads has happened.
  local j=0; while /bin/ls "$T"/st/auto/wland-*.busy >/dev/null 2>&1 && [ $j -lt 600 ]; do /bin/sleep 0.1; j=$((j+1)); done; return 0; }
# The wave branch's tip at the remote, and nothing at all when the branch is gone: plain `rev-parse <ref>` echoes the ref
# it could not resolve on stdout (a fatal on stderr, exit 128), so a case that reads a landed wave as still there is the
# helper's doing and not the remote's -- only `--verify -q` answers empty for a branch that is not there.
wtip() { $O rev-parse --verify -q "refs/heads/wave/$1" 2>/dev/null; }

# 1) The launch cuts the run's wave branch from the base, and leaves the marker the run's completion lands.
R1=run-20261014-000000-aaa111
r=$(launch "$R1" 65 2>"$T/launch1.err")
[ "$(printf '%s' "$r" | tail -1)" = "$R1" ] && ok "L1: the launch reports the run's id" || ko "L1: the launch reported '$r'"
[ "$(wtip "$R1")" = "$($O rev-parse dev)" ] && ok "L1: the run's launch cuts its wave branch from dev" \
  || ko "L1: no wave branch wave/$R1 from the launch: '$(wtip "$R1")'"
[ "$(cat "$T/st/auto/wave-$R1" 2>/dev/null)" = "wave/$R1" ] && ok "L1: the cut leaves the marker the run's completion lands" \
  || ko "L1: no wave marker: '$(cat "$T/st/auto/wave-$R1" 2>/dev/null)'"
/usr/bin/grep -q "WAVE wave/$R1" "$T/launch1.err" && ok "L1: the launch says the wave it cut" \
  || ko "L1: the launch is silent about its wave: '$(cat "$T/launch1.err" 2>/dev/null)'"
/usr/bin/grep -q 'push your branch' "$T/prompt-$R1" && ko "L1: the request the engine got still tells the child to push its branch" \
  || ok "L1: the request the engine got does not tell the child to push its branch"
/usr/bin/grep -qi 'branch stays local\|keep your branch local' "$T/prompt-$R1" && ok "L1: the request says the child's branch stays local" \
  || ko "L1: the request does not say the branch stays local: '$(cat "$T/prompt-$R1" 2>/dev/null)'"
[ -d "$(target_of 65)" ] && ok "L1: the launch makes the worktree its run works in" || ko "L1: no issue worktree at $(target_of 65)"

# 2) A finished child enters the wave through the production child-completion pass, and that entry is what moves its card.
child "$R1" 65 g65; before=$(wtip "$R1"); out=$(childcc); rc=$?
[ "$rc" = 0 ] && ok "L2: the child-completion pass runs" || ko "L2: childcc exited $rc: '$out'"
[ "$(wtip "$R1")" = "$(child_head "$R1" 65)" ] && ok "L2: the child's completion enters the wave branch" \
  || ko "L2: the wave tip is '$(wtip "$R1")' and the child's head '$(child_head "$R1" 65)': '$(printf '%s' "$out" | tail -3)'"
case "$out" in *"entered wave/$R1 (#65)"*) ok "L2: the entry is said";; *) ko "L2: the entry is silent: '$(printf '%s' "$out" | tail -5)'";; esac
$O cat-file -e "wave/$R1:g65" 2>/dev/null && ok "L2: the work of the child is in the wave branch" || ko "L2: the child's work is not in the wave"
/usr/bin/grep -qx 'o 3 Batching 65' "$T/board.log" && ok "L2: the card of the child that entered reads Batching" \
  || ko "L2: no Batching card: '$(cat "$T/board.log" 2>/dev/null)'"
[ "$before" != "$(wtip "$R1")" ] && ok "L2: the entry is what moved the wave tip" || ko "L2: the wave tip did not move"

# 3) A child whose gates fail never enters, and its issue goes back to the queue: the card is not moved to Batching.
R2=run-20261014-000000-bbb222
: > "$T/board.log"
launch "$R2" 66 >/dev/null 2>&1; child "$R2" 66 g66; before=$(wtip "$R2")
out=$(GATES=false childcc)
[ -n "$(wtip "$R2")" ] && [ "$(wtip "$R2")" = "$before" ] && ok "L3: a child whose gates fail never enters the wave" \
  || ko "L3: the wave tip moved to '$(wtip "$R2")'"
/usr/bin/grep -qE '(Batching|Code Complete) 66' "$T/board.log" && ko "L3: the card of a child that did not enter reads Batching" \
  || ok "L3: a child that did not enter moves no card to Batching"
case "$out" in *"WAVE REQUEUE #66"*) ok "L3: the refused entry is said";; *) ko "L3: the refusal is silent: '$(printf '%s' "$out" | tail -5)'";; esac
/usr/bin/grep -qx 'o 3 Todo 66' "$T/board.log" && ok "L3: the issue of a child that did not enter is back in the queue" \
  || ko "L3: the issue was not requeued: '$(cat "$T/board.log" 2>/dev/null)'"

# 3b) A child that did not enter is tried again by the next pass only when its HEAD changed: the same HEAD would fail the same
# gates again, and every pass would rerun them and requeue its card (#375 review). Its entry marker is written only once it
# entered, so a failed gate (or rebase) never consumes the child's one chance. The gates are green now.
tip=$(wtip "$R2"); out=$(childcc)
[ "$(wtip "$R2")" = "$tip" ] && ok "L6: a child refused on a HEAD is not tried again on that HEAD, whatever the gates say" \
  || ko "L6: an unchanged child was entered again: '$(printf '%s' "$out" | tail -3)'"
child "$R2" 66 g66b >/dev/null 2>&1; out=$(childcc)
[ "$(wtip "$R2")" = "$(child_head "$R2" 66)" ] && ok "L6: a child that failed its gates enters on the next pass" \
  || ko "L6: the wave tip is '$(wtip "$R2")' and the child's head '$(child_head "$R2" 66)': '$(printf '%s' "$out" | tail -3)'"
/usr/bin/grep -qx 'o 3 Batching 66' "$T/board.log" && ok "L6: its card reads Batching once it entered" || ko "L6: no Batching card for #66"
before=$(wtip "$R2"); out=$(childcc)
[ "$(wtip "$R2")" = "$before" ] && ! printf '%s' "$out" | /usr/bin/grep -q "entered wave/$R2" && ok "L6: a child that entered is not entered again" || ko "L6: the entered child was entered again: '$out'"

# 3c) The wave marker is the entry gate, not the card: a finished run whose card moved past Blocked (Closed) still has its
# child entered, because the child is not in the wave yet and the wave has not landed (#375 review).
R3=run-20261014-000000-eee555
echo 69 > "$T/issue"; echo Closed > "$T/card"
launch "$R3" 69 >/dev/null 2>&1; child "$R3" 69 g69; verdict "$R3" 69
out=$(childcc)
[ "$(wtip "$R3")" = "$(child_head "$R3" 69)" ] && ok "L7: a finished run's child enters its wave whatever column its card is in" \
  || ko "L7: the wave tip is '$(wtip "$R3")' and the child's head '$(child_head "$R3" 69)': '$(printf '%s' "$out" | tail -3)'"
# The case is done with this run: it leaves the loop's list, so the completion case below lands its own wave and no other.
rm -f "$T/card" "$T/st/auto/wave-$R3"; /usr/bin/grep -v "$R3" "$T/st/active-runs.txt" > "$T/st/active-runs.new"; mv "$T/st/active-runs.new" "$T/st/active-runs.txt"
echo 67 > "$T/issue"

# 3d) The child's own diff is reviewed by the completion pass itself, in the child's own worktree, before the entry (#448).
# The entry waits for the review of the child's own commits against the wave branch (#376), and a child's branch is pushed
# nowhere (#375), so the review lane that lists pushed `fix/`/`gui/` branches never reads it: nothing wrote that review, and
# the entry held every child of every run for good (ten `WAVE HOLD` lines in the loop's own log, today at 09:2x).
# The reviewer the pass calls (childreview.sh) is stubbed here, and writes what childreview.sh writes: the verdict, the head
# it read and the base it read against, from this worktree's own HEAD. The stub counts its calls, so "once per head" is read.
cat > "$T/s/childreview.sh" <<EOF
#!/bin/bash
printf 'childreview %s %s\n' "\$1" "\$2" >> "$T/rev.log"
br=\$(git symbolic-ref --quiet --short HEAD); key=\$(printf '%s' "\$br" | tr / _)
printf 'VERDICT: %s\nOne finding.\n' "\${STUB_VERDICT:-APPROVE}" > "$T/st/review-\$key.md"
git rev-parse HEAD > "$T/st/review-\$key.sha"
printf 'wave/%s\n' "\$1" > "$T/st/review-\$key.base"
printf '%s 0000000 VERDICT: %s\n' "\$br" "\${STUB_VERDICT:-APPROVE}"
EOF
chmod +x "$T/s/childreview.sh"
R5=run-20261014-000000-bbb555
: > "$T/rev.log"; : > "$T/board.log"
launch "$R5" 71 >/dev/null 2>&1; CHILD_NO_REVIEW=1 child "$R5" 71 g71; before=$(wtip "$R5")
out=$(childcc)
[ "$(wc -l < "$T/rev.log" | tr -d ' ')" = 1 ] && /usr/bin/grep -q "childreview $R5 71" "$T/rev.log" \
  && ok "L8: the pass reviews the child's own diff before the entry" || ko "L8: no review of the child: '$(cat "$T/rev.log" 2>/dev/null)'"
[ "$(wtip "$R5")" = "$(child_head "$R5" 71)" ] && [ "$(wtip "$R5")" != "$before" ] && ok "L8: the reviewed child enters its wave" \
  || ko "L8: the wave tip is '$(wtip "$R5")' and the child's head '$(child_head "$R5" 71)': '$(printf '%s' "$out" | tail -3)'"

# 3e) A review that asked for CHANGES starts one fix run in the child's worktree (#636), stubbed here as one that ends at
# once with no commit. The findings are then the same on the same head, so the issue goes back to the queue instead of
# holding for good, and the head it read is never read again: a hold no pass can clear is a child that never enters (#448).
cat > "$T/s/childfix.sh" <<EOF
#!/bin/bash
echo "childfix \$1 \$2" >> "$T/fix.log"; d=$T/co/runs/fix-\$2; mkdir -p "\$d"; echo '{"event": "verdict"}' > "\$d/record.jsonl"; echo "\$d"
EOF
R6=run-20261014-000000-bbb666
: > "$T/rev.log"; : > "$T/board.log"; : > "$T/fix.log"
launch "$R6" 72 >/dev/null 2>&1; CHILD_NO_REVIEW=1 child "$R6" 72 g72; before=$(wtip "$R6")
out=$(STUB_VERDICT=CHANGES childcc)
[ -n "$(wtip "$R6")" ] && [ "$(wtip "$R6")" = "$before" ] && ok "L9: a child the review asked changes of never enters the wave" \
  || ko "L9: the wave tip moved to '$(wtip "$R6")'"
[ "$(cat "$T/fix.log")" = "childfix $R6 72" ] && ! /usr/bin/grep -q 'Todo 72' "$T/board.log" && ok "L9: CHANGES starts one fix run, and the issue does not requeue" \
  || ko "L9: fix runs '$(cat "$T/fix.log")', board '$(cat "$T/board.log" 2>/dev/null)': $out"
[ "$(cat "$T/st/auto/wave-pause-$R6" 2>/dev/null)" = "$T/co/runs/fix-72" ] && ok "L9: the fix run is named in the wave's pause marker, so its time does not spend the age cap (#637)" \
  || ko "L9: the pause marker holds '$(cat "$T/st/auto/wave-pause-$R6" 2>/dev/null)'"
out=$(STUB_VERDICT=CHANGES childcc)
/usr/bin/grep -qx 'o 3 Todo 72' "$T/board.log" && ok "L9: findings the fix run left as they were send the issue back to the queue" \
  || ko "L9: the issue was not requeued: '$(cat "$T/board.log" 2>/dev/null)': $out"
[ "$(wc -l < "$T/rev.log" | tr -d ' ')" = 1 ] && ok "L9: the review is read once per head, never on every pass" \
  || ko "L9: the review ran again: '$(cat "$T/rev.log" 2>/dev/null)'"

# 3f) A child of the loop is reviewed by its own run (#601, ADR-0145 rule 9): the loop workflow's review gate runs
# runreview.sh in the child's worktree before the run merges it. An approved child then enters its wave in childcc's pass
# with no second review: the review of its head is already there.
R7=run-20261014-000000-bbb777
: > "$T/rev.log"; : > "$T/board.log"
launch "$R7" 73 >/dev/null 2>&1; CHILD_NO_REVIEW=1 child "$R7" 73 g73
out=$(cd "$(run_dir "$R7" 73)/ws/worktrees_child-route.router.1" && . "$T/s/env.sh" && bash "$T/s/runreview.sh" "$R7" route.router.1 73 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$T/rev.log")" = "childreview $R7 73" ] && ok "L10: the run's review gate reviews the child through childreview.sh" \
  || ko "L10: exit $rc, rev.log '$(cat "$T/rev.log")': $out"
out=$(childcc)
[ "$(wtip "$R7")" = "$(child_head "$R7" 73)" ] && ok "L10: the child its run approved enters its wave" \
  || ko "L10: the wave tip is '$(wtip "$R7")' and the child's head '$(child_head "$R7" 73)': '$(printf '%s' "$out" | tail -3)'"
[ "$(wc -l < "$T/rev.log" | tr -d ' ')" = 1 ] && ok "L10: the entry asks for no second review" || ko "L10: the review ran again: '$(cat "$T/rev.log")'"

# 3g) CHANGES in the run's own review requeues the issue at once, once: the run holds the child (its land event says
# review_held), and childcc, which honors the requeued marker, neither requeues it again nor reviews it again.
R8=run-20261014-000000-bbb888
: > "$T/rev.log"; : > "$T/board.log"
launch "$R8" 74 >/dev/null 2>&1; CHILD_NO_REVIEW=1 child "$R8" 74 g74; before=$(wtip "$R8")
out=$(cd "$(run_dir "$R8" 74)/ws/worktrees_child-route.router.1" && . "$T/s/env.sh" && STUB_VERDICT=CHANGES bash "$T/s/runreview.sh" "$R8" route.router.1 74 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && ok "L11: CHANGES exits non-zero, so the run holds the child" || ko "L11: exit $rc: $out"
[ "$(/usr/bin/grep -cx 'o 3 Todo 74' "$T/board.log")" = 1 ] && ok "L11: the issue goes back to the queue at once" \
  || ko "L11: board '$(cat "$T/board.log")'"
printf '{"event": "land", "node": "route.router.1", "outcome": "fail", "reasons": ["review_held"]}\n' >> "$(run_dir "$R8" 74)/record.jsonl"
out=$(childcc); out2=$(childcc)
[ "$(/usr/bin/grep -cx 'o 3 Todo 74' "$T/board.log")" = 1 ] && ok "L11: childcc never requeues the held child a second time" \
  || ko "L11: board '$(cat "$T/board.log")': '$(printf '%s\n%s' "$out" "$out2" | tail -4)'"
[ "$(wc -l < "$T/rev.log" | tr -d ' ')" = 1 ] && [ "$(wtip "$R8")" = "$before" ] && ok "L11: the held child is neither reviewed again nor entered" \
  || ko "L11: rev.log '$(cat "$T/rev.log")', tip '$(wtip "$R8")'"
rm -f "$T/st/auto/wave-$R7" "$T/st/auto/wave-$R8"; /usr/bin/grep -v -e "$R7" -e "$R8" "$T/st/active-runs.txt" > "$T/st/active-runs.new"; mv "$T/st/active-runs.new" "$T/st/active-runs.txt"

# 4) A run that ended lands the wave its finished child is in, through the loop's own cycle. The child reaches the wave in
# the completion that lands it -- never after it -- because a landing attempted first finds an empty wave, refuses it, and
# the run is read once: whatever entered afterwards would stay on wave/<run> and never reach dev (#375 review). No pass
# calls the child's entry here: the completion is the only path of this case.
#
# The case runs on the loop's first start: no watch-seen.txt exists yet, so watch.sh takes its boot path (watch.sh:34) and
# reads the run with boot=1. That path suppresses the verdict announcement alone (watch.sh:77) -- the landing it calls and
# the seen record sit outside the boot branch (watch.sh:90, watch.sh:91) -- so this case still enters the child and lands
# the wave. The two assertions below hold the case to that start, instead of leaving the start it ran to whatever ran
# before it (#375 review).
R4=run-20261014-000000-ccc333
: > "$T/board.log"; : > "$T/gh.log"; echo 67 > "$T/issue"
launch "$R4" 67 >/dev/null 2>&1; child "$R4" 67 g67; verdict "$R4" 67
[ -e "$T/st/watch-seen.txt" ] && ko "L4: the case starts a loop that already read a run" \
  || ok "L4: the case runs on the loop's first start, with no watch-seen.txt"
cycle 1
e=$(/usr/bin/grep -n "entered wave/$R4 (#67)" "$T/watch.out" | head -1 | cut -d: -f1)
l=$(/usr/bin/grep -n "landed in dev" "$T/watch.out" | head -1 | cut -d: -f1)
[ -n "$e" ] && [ -n "$l" ] && [ "$e" -lt "$l" ] && ok "L4: the run's finished child enters the wave before the completion lands it" \
  || ko "L4: the entry is at line '${e:-none}' and the landing at '${l:-none}': the completion did not land a wave the child is in: '$(printf '%s' "$(cat "$T/watch.out")" | tail -4)'"
/usr/bin/grep -q "RUN ${R4} verdict=" "$T/watch.out" && ko "L4: the first start announced the run's verdict, so boot=1 was not taken" \
  || ok "L4: the first start announces no verdict (boot=1), and the landing above is still reached"
[ "$($O rev-parse dev 2>/dev/null)" = "$(child_head "$R4" 67)" ] && ok "L4: the completion of a finished run lands its child's work in dev" \
  || ko "L4: dev is '$($O rev-parse dev)' and the child's head '$(child_head "$R4" 67)': the work never reached dev"
$O cat-file -e dev:g67 2>/dev/null && ok "L4: the work of the landed child is in dev" || ko "L4: the child's work is not in dev"
[ -z "$(wtip "$R4")" ] && ok "L4: the wave branch goes once its work is in dev" || ko "L4: the wave branch is still there: '$(wtip "$R4")'"
/usr/bin/grep -q "issues/67" "$T/gh.log" && ok "L4: the issue of the landed child is closed through the API" \
  || ko "L4: the issue was not closed: '$(cat "$T/gh.log" 2>/dev/null)'"
/usr/bin/grep -qx 'o 3 Done 67' "$T/board.log" && ok "L4: the card of the landed child reads Done" \
  || ko "L4: no Done card: '$(cat "$T/board.log" 2>/dev/null)'"

# 5) A completion that finds the entry pass already running does not land: it leaves the run unread, so the next cycle
# lands the wave its child is in by then. A landing run now would be a landing of a wave the child had not entered yet,
# and the run would be read once: the fix is the order, and this is the case that says the order holds under a race.
R5=run-20261014-000000-ddd444
echo 68 > "$T/issue"
launch "$R5" 68 >/dev/null 2>&1; child "$R5" 68 g68; verdict "$R5" 68
mkdir -p "$T/st/locks/single-childcc"; echo $$ > "$T/st/locks/single-childcc/pid"   # the pass runs elsewhere, in its own single-flight lock
cycle 1
[ "$($O rev-parse dev 2>/dev/null)" != "$(child_head "$R5" 68)" ] && ok "L5: a run whose entry pass runs elsewhere is not landed" \
  || ko "L5: the completion landed a wave while the pass that enters its children was running"
/usr/bin/grep -q "seen .*$R5" "$T/st/watch-seen.txt" && ko "L5: the run was read while the pass that enters its children was still running" \
  || ok "L5: the run stays unread, so its completion is reached again"
[ -n "$(wtip "$R5")" ] && ok "L5: the wave of a run that waits is still there" || ko "L5: the wave of a run that waits is gone"
rm -rf "$T/st/locks/single-childcc"
cycle 2
[ "$($O rev-parse dev 2>/dev/null)" = "$(child_head "$R5" 68)" ] && ok "L5: the next cycle lands the wave its child is in" \
  || ko "L5: the wave of a run that waited never landed: dev is '$($O rev-parse dev 2>/dev/null)' and the child's head '$(child_head "$R5" 68)'"

# No child branch of a wave ever reaches the remote: every child above ran the way its request told it to (see `child`), the
# entry pushed the wave ref alone, and the landing deleted the wave. A request that still told a child to push shows here.
pushed=$($G ls-remote "$T/origin.git" 'refs/heads/gui/*')
[ -z "$pushed" ] && ok "no child branch is pushed to the remote: the entry pushes the wave ref alone" \
  || ko "no child branch is pushed to the remote: $(printf '%s' "$pushed" | tr '\n' ' ')"

# The lifecycle's gates and reviews draw on the one machine-wide budget (#567) and give every slot back: a lease kind folder
# exists under the throwaway budget, and no live holder is left (a dead holder's file stays until the next take reclaims it).
ls "$SQUAD_CAPS_DIR" 2>/dev/null | /usr/bin/grep -qE '^(gates|reviews)$' && ok "the lifecycle drew slots from the machine-wide budget" || ko "no gate or review slot was ever taken: $(ls "$SQUAD_CAPS_DIR" 2>&1)"
occ=$(PYTHONPATH="$T/squad" python3 -m squad.caps 2>&1 | python3 -c 'import json,sys; o=json.load(sys.stdin)["occupancy"]; print(o["gates"]+o["reviews"])' 2>&1)
[ "$occ" = 0 ] && ok "every slot was given back" || ko "slots still live: $occ"

# (#1742) A cycle the stub never ends is stopped at the helper's bound, and the loop it started goes with it: the helper
# ended only the subshell around the loop, so the real watch.sh stayed as an orphan and ran a cycle every few seconds for
# hours, on a machine whose gates then met their deadlines.
CYCLE_TICKS=20 cycle 99999
/bin/sleep 0.5
left=$(pgrep -f "$T/s/watch.sh" | tr '\n' ' ')
[ -z "$left" ] && ok "L9: a cycle stopped at its bound leaves no watch loop behind" \
  || { ko "L9: the watch loop outlived its helper: pids $left"; pkill -KILL -f "$T/s/watch.sh"; }

kill "$HUB" 2>/dev/null; wait "$HUB" 2>/dev/null; rm -rf "$T"
[ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
