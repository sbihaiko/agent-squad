#!/bin/bash
# The landing paths a condition outside the wave decides (#477, #656, #1710, #1961, #1684): a landing that cannot
# happen, a fetch the network or the machine fails, a red tip that names no child, and a full gate budget.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# K2 (#477): a landing that exits 2 -- the wave branch is gone, the base is gone, or the checkout failed -- is a landing
# that failed, so it counts toward the same cap. Below the cap the marker stays and the next cycle tries again; at the cap
# the marker and the wave start go, so no wave-<run> hold outlives a landing that can never happen.
mkdir -p "$T/st/auto" "$T/co/runs/run-gone"; echo wave/run-gone > "$T/st/auto/wave-run-gone"; date +%s > "$T/st/auto/wave-start-run-gone"; echo "$T/co/runs/run-gone" >> "$T/st/active-runs.txt"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #95 work"}}' > "$T/co/runs/run-gone/record.jsonl"
r1=$(land2 run-gone); rc1=$?; r2=$(land2 run-gone); rc2=$?
[ "$(tries 95 run-gone)" = 2 ] && [ ! -e "$T/st/auto/wland-tries-run-gone" ] && ok "K2: each try is counted in the ledger of the run's issue, with no wland-tries- marker" || ko "K2: tries '$(tries 95 run-gone)', $(/bin/ls "$T/st/auto")"
[ $rc1 = 2 ] && [ $rc2 = 2 ] && [ -e "$T/st/auto/wave-run-gone" ] && ok "K2: a landing with no wave branch exits 2 and keeps its marker below the cap" || ko "K2: exits $rc1 $rc2, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r1'"
r3=$(land2 run-gone); rc3=$?
[ $rc3 = 2 ] && [ ! -e "$T/st/auto/wave-run-gone" ] && [ ! -e "$T/st/auto/wave-start-run-gone" ] && ok "K2: the third exit-2 landing clears the hold" || ko "K2: exit $rc3, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r3'"
case "$r3" in *"WAVE NEEDS-HUMAN wave/run-gone"*) ok "K2: the cleared hold is named on one NEEDS-HUMAN line";; *) ko "K2: no NEEDS-HUMAN line: '$r3'";; esac
# K2b: a wave that names no issue (no Refs ahead of the base, no run record) has no ledger to count in and holds no issue,
# so its first failed landing clears its hold.
echo wave/run-none > "$T/st/auto/wave-run-none"; r=$(land2 run-none); rc=$?
[ $rc = 2 ] && [ ! -e "$T/st/auto/wave-run-none" ] && case "$r" in *"WAVE NEEDS-HUMAN wave/run-none"*"names no issue"*) true;; *) false;; esac \
  && ok "K2b: a wave with no issue clears its hold at its first failed landing" || ko "K2b: exit $rc, $(/bin/ls "$T/st/auto" | /usr/bin/grep run-none): '$r'"

# AB (#1310): a wave branch absent on the remote was never pushed. The landing names that cause and never claims the branch
# stays on the remote. A run that holds no slot (ended, dropped from active-runs.txt) stops waiting at once: its markers go.
mkdir -p "$T/co/runs/run-abs"; printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #96 work"}}' '{"event": "run_start"}' '{"event": "verdict", "verdict": "failed"}' > "$T/co/runs/run-abs/record.jsonl"
echo wave/run-abs > "$T/st/auto/wave-run-abs"; date +%s > "$T/st/auto/wave-start-run-abs"
ra=$(land2 run-abs); rca=$?
case "$ra" in *"stays on the remote"*) ko "AB: an absent wave branch is said to stay on the remote: '$ra'";; *) ok "AB: no claim that an absent wave branch stays on the remote";; esac
case "$ra" in *"wave/run-abs is not on "*"the run never pushed it"*) ok "AB: the absent wave branch is named as the cause";; *) ko "AB: the cause is not named: '$ra'";; esac
[ $rca = 2 ] && [ ! -e "$T/st/auto/wave-run-abs" ] && [ ! -e "$T/st/auto/wave-start-run-abs" ] && ok "AB: a run with no slot stops waiting at once" || ko "AB: exit $rca, markers kept for a run with no slot: '$ra'"
# AB2: a run that still holds its slot keeps the absent wave on its count: one try, the marker stays for the next cycle.
mkdir -p "$T/co/runs/run-abl"; printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #97 work"}}' '{"event": "run_start"}' > "$T/co/runs/run-abl/record.jsonl"; echo "$T/co/runs/run-abl" >> "$T/st/active-runs.txt"
echo wave/run-abl > "$T/st/auto/wave-run-abl"; date +%s > "$T/st/auto/wave-start-run-abl"
rl=$(land2 run-abl); rcl=$?
[ $rcl = 2 ] && [ -e "$T/st/auto/wave-run-abl" ] && ok "AB2: a run that holds its slot keeps its absent wave for the next cycle" || ko "AB2: exit $rcl: '$rl'"
case "$rl" in *"stays on the remote"*) ko "AB2: an absent wave branch is said to stay on the remote: '$rl'";; *) ok "AB2: a live run's absent wave is not said to stay on the remote";; esac

# K3 (#656): a fetch that fails on a network error (DNS, connect, timeout) is no landing that failed for good: the next
# cycle can land. It counts no try, freezes nothing, prints one WAVE-NETWORK line and exits 2. The remote is reached through
# a stub ssh: while $T/netdown is there it answers as a DNS outage does, else it runs the git command on the local remote.
printf '#!/bin/bash\nwhile [ $# -gt 0 ] && [ "$1" != stub ]; do shift; done; shift\n[ -e "%s/netdown" ] && { echo "ssh: Could not resolve hostname stub: nodename nor servname provided, or not known" >&2; exit 255; }\nexec sh -c "$*"\n' "$T" > "$T/bin/ssh-stub"; chmod +x "$T/bin/ssh-stub"
netland() { (cd "$T/co" && SG_LAND_REMOTE="ssh://stub$T/origin.git" GIT_SSH_COMMAND="$T/bin/ssh-stub" bash "$T/s/wave.sh" land "$1" 2>&1); }
W8=wave/run-8; start2 run-8 >/dev/null; child 80 nf "origin/$W8"; en2 run-8 80 >/dev/null
: > "$T/netdown"; : > "$T/gh.log"; before=$($O rev-parse dev); nr=""
tr=""; for i in 1 2 3; do r=$(netland run-8); rc=$?; nr="$nr$rc "; tr="$tr$(tries 80 run-8)"; done
[ "$nr" = "2 2 2 " ] && ok "K3: a landing whose fetch fails on the network exits 2" || ko "K3: exits $nr: '$r'"
case "$r" in *"WAVE-NETWORK"*"Could not resolve host"*"lands on a later cycle"*) ok "K3: the network failure is named on one WAVE-NETWORK line";; *) ko "K3: no WAVE-NETWORK line: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K3: a network failure is read as a stuck landing: '$r'";; *) ok "K3: a network failure is no stuck landing";; esac
[ -z "$tr" ] && ok "K3: three network failures count no landing try" || ko "K3: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K3: a network failure froze the issue" || ok "K3: a network failure freezes nothing"
[ -e "$T/st/auto/wave-run-8" ] && ok "K3: the run keeps its wave marker" || ko "K3: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K3: dev does not move while the network is down" || ko "K3: dev moved"
rm -f "$T/netdown"; r=$(netland run-8); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf 2>/dev/null && echo yes)" = yes ] && ok "K3: the next good landing lands the wave" || ko "K3: exit $rc: '$r'"
# A fetch that answers and lacks the ref still counts, on the same remote.
echo wave/run-gone8 > "$T/st/auto/wave-run-gone8"; mkdir -p "$T/co/runs/run-gone8"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #96 work"}}' > "$T/co/runs/run-gone8/record.jsonl"
r=$(netland run-gone8); rc=$?
[ $rc = 2 ] && [ "$(tries 96 run-gone8)" = 1 ] && ok "K3: a fetch that lacks the ref still counts a try" || ko "K3: exit $rc, tries '$(tries 96 run-gone8)': '$r'"
rm -f "$T/st/auto/wave-run-gone8"

# K6 (#1710): a local fetch fails when the machine cannot start git's transport helper, and git prints its own cause:
# `error: cannot fork() for git-upload-pack '<remote>': Resource temporarily unavailable` then `fatal: unable to fork`.
# A loaded machine produces it (a full process table, no memory); the fixture owns its repository and cannot avoid the
# fork, because a local fetch always starts git-upload-pack. It is the network class of #656: a repeat does not change it
# on this pass, so it names git's line, counts no landing try, freezes nothing, keeps the wave, and exits 2. The transport
# is stubbed the way K3 stubs ssh: the stub answers with the two lines git printed on a real fork failure.
W17=wave/run-17; start2 run-17 >/dev/null; child 170 nf17 "origin/$W17"; en2 run-17 170 >/dev/null
mkdir -p "$T/co/runs/run-17"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #170 work"}}' > "$T/co/runs/run-17/record.jsonl"
mkdir -p "$T/bin/stubexec"
printf '#!/bin/bash\necho "error: cannot fork() for git-upload-pack '"'"'$1'"'"': Resource temporarily unavailable" >&2\necho "fatal: unable to fork" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
chmod +x "$T/bin/stubexec/git-upload-pack"
noexec() { (cd "$T/co" && GIT_EXEC_PATH="$T/bin/stubexec" bash "$T/s/wave.sh" land "$1" 2>&1); }
: > "$T/gh.log"; before=$($O rev-parse dev); tr=""; nrc=""
for i in 1 2 3; do r=$(noexec run-17); nrc="$nrc$? "; tr="$tr$(tries 170 run-17)"; done
[ "$nrc" = "2 2 2 " ] && ok "K6: a landing whose fetch cannot start its transport exits 2" || ko "K6: exits $nrc: '$r'"
case "$r" in *"WAVE-NETWORK"*"fork"*"lands on a later cycle"*) ok "K6: the untransportable fetch is named on one WAVE-NETWORK line";; *) ko "K6: git's cause is not named: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K6: a machine-caused fetch is read as a stuck landing: '$r'";; *) ok "K6: a machine-caused fetch is no stuck landing";; esac
[ -z "$tr" ] && ok "K6: three machine-caused fetches count no landing try" || ko "K6: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K6: a machine-caused fetch froze the issue" || ok "K6: a machine-caused fetch freezes nothing"
[ -e "$T/st/auto/wave-run-17" ] && ok "K6: the run keeps its wave marker" || ko "K6: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K6: dev does not move while the machine cannot fork" || ko "K6: dev moved"
# A fetch failure no class names still carries git's own line, so the next one is diagnosable from the landing's own words.
printf '#!/bin/bash\necho "fatal: an unnamed transport defect" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
r=$(noexec run-17)
case "$r" in *"WAVE-ERROR the fetch of"*"an unnamed transport defect"*) ok "K6: an unnamed fetch failure carries git's own line";; *) ko "K6: git's line is dropped: '$r'";; esac
r=$(land2 run-17); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf17 2>/dev/null && echo yes)" = yes ] && ok "K6: the next landing on a machine that can fork lands the wave" || ko "K6: exit $rc: '$r'"

# K7 (#1710): the machine that refuses git-upload-pack a fork refuses the helper commands the landing's own readers fork
# too. Case L failed once in a fast gate with `WAVE-ERROR the fetch of wave/run-8 from .../origin.git failed` and named no
# cause: netfail and gitwhy read the fetch's stderr through a pipeline (`grep`, `tail`, `head`), so the same full process
# table that refused the transport refused them, the machine cause was dropped exactly when it happens, and the landing
# took the stuck path -- a try counted and, at the cap, the wave marker cleared and the issue frozen. It is the reason the
# cases behind L cascaded. The cause is read with bash's own match and read loop here: no helper to fork, so the machine
# cause is named, no try is counted, the wave stays and nothing is frozen. The transport is stubbed the way K6 stubs it,
# and the helpers that must not be forked fail as they do on a machine with a full process table.
W71=wave/run-k7; start2 run-k7 >/dev/null; child 1710 nf1710 "origin/$W71"; en2 run-k7 1710 >/dev/null
mkdir -p "$T/co/runs/run-k7"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #1710 work"}}' > "$T/co/runs/run-k7/record.jsonl"
printf '#!/bin/bash\necho "error: cannot fork() for git-upload-pack '"'"'$1'"'"': Resource temporarily unavailable" >&2\necho "fatal: unable to fork" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
chmod +x "$T/bin/stubexec/git-upload-pack"
mkdir -p "$T/bin/nofork"
for c in grep tail head; do printf '#!/bin/bash\necho "bash: fork: Resource temporarily unavailable" >&2\nexit 128\n' > "$T/bin/nofork/$c"; chmod +x "$T/bin/nofork/$c"; done
nohelper() { (cd "$T/co" && PATH="$T/bin/nofork:$PATH" GIT_EXEC_PATH="$T/bin/stubexec" bash "$T/s/wave.sh" land "$1" 2>&1); }
: > "$T/gh.log"; before=$($O rev-parse dev); tr=""; nhr=""
for i in 1 2 3; do r=$(nohelper run-k7); nhr="$nhr$? "; tr="$tr$(tries 1710 run-k7)"; done
[ "$nhr" = "2 2 2 " ] && ok "K7: a machine-caused fetch exits 2 with the helper commands unable to start" || ko "K7: exits $nhr: '$r'"
case "$r" in *"WAVE-NETWORK"*"fork"*"lands on a later cycle"*) ok "K7: the machine cause is named with no helper command to fork";; *) ko "K7: the cause is dropped when the helpers cannot start: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K7: the dropped cause took the stuck path: '$r'";; *) ok "K7: a cause read without a fork is no stuck landing";; esac
[ -z "$tr" ] && ok "K7: no landing try is counted with the helpers down" || ko "K7: tries counted with the helpers down: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K7: the machine cause froze the issue with the helpers down" || ok "K7: nothing is frozen with the helpers down"
[ -e "$T/st/auto/wave-run-k7" ] && ok "K7: the wave marker is kept with the helpers down" || ko "K7: the wave marker is gone with the helpers down"
[ "$($O rev-parse dev)" = "$before" ] && ok "K7: dev does not move with the helpers down" || ko "K7: dev moved"
# A class no name covers still carries git's own first line, read without a helper command.
printf '#!/bin/bash\necho "warning: the remote is a local path" >&2\necho "fatal: an unnamed transport defect" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
r=$(nohelper run-k7)
case "$r" in *"WAVE-ERROR the fetch of"*"failed: warning: the remote is a local path"*) ok "K7: git's first line survives with no helper command";; *) ko "K7: git's line is dropped with the helpers down: '$r'";; esac

# K8 (#1710): the fixture owns its repository, and a second writer on it is not one of the causes the issue lists. Each
# suite process makes its own mktemp tree and its bare origin.git inside it, and the remote every landing fetches is that
# tree's own default, never a fixed path a second suite or a second gate would share. Every case writes the repository one
# at a time, and a ref another process pushes takes a lock on its own ref alone: a landing beside such a writer fetches and
# lands, and its fetch names no no-cause shape.
/usr/bin/grep -qF "SG_LAND_REMOTE:=$T/origin.git" "$T/s/env.sh" && ok "K8: the fixture's remote is the suite's own temp tree, never a shared path" || ko "K8: the fixture remote is not the suite's own: '$(/usr/bin/grep -n SG_LAND_REMOTE "$T/s/env.sh" 2>&1)'"
W72=wave/run-k9; start2 run-k9 >/dev/null; child 1711 nf1711 "origin/$W72"; en2 run-k9 1711 >/dev/null
mkdir -p "$T/co/runs/run-k9"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #1711 work"}}' > "$T/co/runs/run-k9/record.jsonl"
rm -rf "$T/w18"; $G clone -q "$T/origin.git" "$T/w18" 2>/dev/null
(cd "$T/w18" && $G checkout -q dev && echo noise > noise && $G add noise && $G -c user.email=a@b -c user.name=t commit -q -m noise 2>/dev/null \
  && for i in $(seq 1 20); do $G push -q origin "HEAD:refs/heads/noise-$i" 2>/dev/null; done) &
wpid=$!
: > "$T/gh.log"; before=$($O rev-parse dev)
r=$(land2 run-k9); rc=$?
wait "$wpid" 2>/dev/null
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf1711 2>/dev/null && echo yes)" = yes ] && ok "K8: a landing fetches and lands beside a second writer on the fixture" || ko "K8: exit $rc beside a second writer: '$r'"
case "$r" in *"the fetch of"*"failed"*) ko "K8: the fetch failed beside a second writer: '$r'";; *) ok "K8: the fetched ref survives a second writer on the fixture";; esac
[ -n "$($O rev-parse -q --verify refs/heads/noise-20)" ] && [ "$($O rev-parse dev)" != "$before" ] && ok "K8: the second writer's ref and the wave's landing both reached the fixture" || ko "K8: the concurrent write did not happen: '$($O for-each-ref --format='%(refname)' | tr '\n' ' ')'"

# W1961 (#1961): a tip check that is red and names no child is a measured red, never the loop's cause. The first red on a
# tree runs the same check once more on the same tree. A green rerun releases the red: the wave lands, and the flaky test is
# filed as one bug, once per cause. A second red on the same tree with the same failing tests runs no third check until the
# tip moves: it is a counted landing try, so SG_WAVE_LAND_TRIES and the owner's alert apply. A moved tip re-arms the check.
# flaky <calls file> <reds>: a tip check that logs each call and is red, with one pytest failure line, on its first <reds> calls.

flakybugs() { /usr/bin/grep -c 'POST repos/x/y/issues -f title=.*test_flaky' "$T/gh.log" 2>/dev/null || echo 0; }
# a: red, then green on the same tree: the wave lands after two gate calls and files one bug with the failure lines.
W161=wave/run-161; start2 run-161 >/dev/null; child 1611 g1611 "origin/$W161"; en2 run-161 1611 >/dev/null
: > "$T/gh.log"; C161=$T/calls-161; rm -f "$C161"
r=$(SG_WAVE_TIP_GATES="$(flaky "$C161" 1)" SG_WAVE_CHILD_GATES=true land2 run-161 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g1611 2>/dev/null && ok "W1961: a red tip that is green on its rerun lands" || ko "W1961: red then green: exit $rc: '$r'"
[ "$(calls "$C161")" = 2 ] && ok "W1961: red then green runs the tip check twice" || ko "W1961: tip check calls: $(calls "$C161")"
case "$r" in *"released by the green run on the same tree"*) ok "W1961: the landing names the green run on the same tree that released the red";; *) ko "W1961: no release record: '$r'";; esac
[ "$(flakybugs)" = 1 ] && ok "W1961: the flake files one bug" || ko "W1961: flaky bugs filed: $(flakybugs): '$(cat "$T/gh.log")'"
/usr/bin/grep -q 'FAILED tests/test_flaky.py::test_flaky - assert 0' "$T/gh-bodies.log" && ok "W1961: the bug carries the failure lines" || ko "W1961: no failure line in the bug: '$(cat "$T/gh-bodies.log" 2>/dev/null)'"
# The same flaky test on another wave is the same cause: no second bug.
W162=wave/run-162; start2 run-162 >/dev/null; child 1621 g1621 "origin/$W162"; en2 run-162 1621 >/dev/null
C162=$T/calls-162; rm -f "$C162"
r=$(SG_WAVE_TIP_GATES="$(flaky "$C162" 1)" SG_WAVE_CHILD_GATES=true land2 run-162 2>&1); rc=$?
[ $rc = 0 ] && [ "$(flakybugs)" = 1 ] && ok "W1961: the same flaky test files no second bug" || ko "W1961: second flake: exit $rc, bugs $(flakybugs): '$r'"
# b: red twice on the same tree with the same failing test: no third call, a named hold, a counted try, and the owner's alert
# at the cap. It is never the loop's cause.
W16=wave/run-16; start2 run-16 >/dev/null; child 160 lg "origin/$W16"; en2 run-16 160 >/dev/null
: > "$T/gh.log"; before=$($O rev-parse dev); C16=$T/calls-16; rm -f "$C16"; lr=""; tr=""; cr=""; all=""
for i in 1 2 3; do r=$(SG_WAVE_TIP_GATES="$(flaky "$C16" 99)" SG_WAVE_CHILD_GATES=true land2 run-16 2>&1); rc=$?; lr="$lr$rc "; tr="$tr$(tries 160 run-16) "; cr="$cr$(calls "$C16") "; all="$all$r"; done
[ "$lr" = "1 1 1 " ] && ok "W1961: a tip red twice on the same tree does not land" || ko "W1961: exits $lr: '$r'"
[ "$cr" = "2 2 2 " ] && ok "W1961: two reds on the same tree run no third check" || ko "W1961: tip check calls per pass: $cr"
case "$all" in *"WAVE-LOOP-CAUSED"*) ko "W1961: a measured red was read as the loop's cause: '$all'";; *) ok "W1961: a measured red is never the loop's cause";; esac
case "$all" in *"red twice on the same tree"*"held until the tip moves"*) ok "W1961: the second red names the hold";; *) ko "W1961: no hold named: '$all'";; esac
[ "$tr" = "1 2 3 " ] && ok "W1961: each held pass counts a landing try" || ko "W1961: tries: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && case "$r" in *"WAVE NEEDS-HUMAN"*) true;; *) false;; esac && ok "W1961: the hold reaches the owner at the cap" || ko "W1961: no owner alert: '$r'"
[ "$($O rev-parse dev)" = "$before" ] && ok "W1961: dev does not move on a measured red" || ko "W1961: dev moved"
# c: a moved tip re-arms the check: the next pass runs it again.
W163=wave/run-163; start2 run-163 >/dev/null; child 1631 g1631 "origin/$W163"; en2 run-163 1631 >/dev/null
C163=$T/calls-163; rm -f "$C163"
for i in 1 2; do r=$(SG_WAVE_LAND_TRIES=9 SG_WAVE_TIP_GATES="$(flaky "$C163" 99)" SG_WAVE_CHILD_GATES=true land2 run-163 2>&1); done
c0=$(calls "$C163")
rm -rf "$T/w163"; $G clone -q "$T/origin.git" "$T/w163" 2>/dev/null
(cd "$T/w163" && $G checkout -q dev && echo m163 > m163 && $G add m163 && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves under run-163" && $G push -q origin HEAD:dev)
r=$(SG_WAVE_LAND_TRIES=9 SG_WAVE_TIP_GATES="$(flaky "$C163" 99)" SG_WAVE_CHILD_GATES=true land2 run-163 2>&1); rc=$?
[ "$c0" = 2 ] && [ "$(calls "$C163")" = 4 ] && ok "W1961: a moved tip re-arms the check" || ko "W1961: calls before the move $c0, after $(calls "$C163"): '$r'"

# K5 (#1684): the words a full gate budget really carries are the loop's cause. The capacity answer names "caps.gates is full",
# and read as the child's defect it counted a failed dispatch and froze the issue after three, though the child did its work.
k5=0; for why in "fast-gate.sh exited 73 on two runs: caps.gates is full (6 slots), held by gate-88923 (live)" \
                "fast_gate_not_run: fast-gate.sh exited 73 (caps.gates full, 6 slots, held by gate-88923)"; do
  r=$((cd "$T/co" && bash "$T/s/wave.sh" fail "$((170+k5))" "run-caps$k5" "$why") 2>&1); k5=$((k5+1))
  case "$r" in *"loop-caused"*) ok "K5: a full gate budget is the loop's cause, not the issue's";; *) ko "K5: capacity read as the issue's own failure: '$r'";; esac
done
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 180 run-realred "pytest the base battery failed at tests/test_caps.py::test_limit - wrong limit for caps.gates") 2>&1)
case "$r" in *"loop-caused"*) ko "K5: a real red that names the budget read as the loop's: '$r'";; *) ok "K5: a real red that names caps.gates stays the issue's own";; esac

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
