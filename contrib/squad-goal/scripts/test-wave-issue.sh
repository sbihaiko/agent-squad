#!/bin/bash
# seconds: 32.89
# The issues a wave carries (#836, #869, #872, #1106, #1833): which commit delivers which issue, what an orphan child
# commit holds, and the one-commit-one-issue rule the landing reads.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# X (#836): the empty landing names the issues of the run's own request (request.txt, its CHILD header), before the record: the
# record's #710 is not named when the request names other issues.
W21=wave/run-836a; start2 run-836a >/dev/null
mkdir -p "$T/co/runs/run-836a"
printf '%s\n%s\n' 'CHILD — GitHub issue(s) 712, 713, 714 (bug; one root cause, one branch):' '--- issue #712' > "$T/co/runs/run-836a/request.txt"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #710 work"}}' > "$T/co/runs/run-836a/record.jsonl"
r=$(land2 run-836a); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y1: the wave of a bundled request lands empty (exit 0)";; *) ko "Y1: not an empty landing: '$r'";; esac || ko "Y1: exit $rc: '$r'"
case "$r" in *"dispatched for #712, #713, #714"*) ok "Y1: the landing names every issue of the request's CHILD header";; *) ko "Y1: the request's issues are not named: '$r'";; esac
case "$r" in *"#710"*) ko "Y1: the record's #710 is named beside the request's issues: '$r'";; *) ok "Y1: the landing names no record-only issue";; esac

# Y2: a child commit of this run (Refs #721) on the run's own branch only, in neither the wave nor dev: the wave is refused and kept.
W22=wave/run-836b; start2 run-836b >/dev/null
mkdir -p "$T/co/runs/run-836b"; printf '%s\n' 'CHILD — GitHub issue #721 (bug; one root cause, one branch):' > "$T/co/runs/run-836b/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-836b/record.jsonl"
x2=$(mktemp -d); $G clone -q "$T/origin.git" "$x2/c" 2>/dev/null
(cd "$x2/c" && $G checkout -q -b tmp dev && echo fix > f721 && $G add f721 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 721" -m "Refs #721")
X2SHA=$($G -C "$x2/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x2/c" HEAD:refs/heads/squad/run/run-836b 2>/dev/null
: > "$T/pushes.log"
r=$(land2 run-836b 2>&1); rc=$?
[ $rc = 1 ] && ok "Y2: a child commit on the run's branch refuses the empty landing (exit 1)" || ko "Y2: exit $rc: '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^WAVE REFUSE')" = 1 ] && ok "Y2: one WAVE REFUSE line" || ko "Y2: no single WAVE REFUSE line: '$r'"
case "$r" in *"${X2SHA:0:9}"*) ok "Y2: the refusal names the child commit";; *) ko "Y2: the commit is not named: '$r'";; esac
case "$r" in *"landed empty"*) ko "Y2: the wave landed empty: '$r'";; *) ok "Y2: the wave does not land empty";; esac
$O rev-parse -q --verify "refs/heads/$W22" >/dev/null && ok "Y2: the wave branch is kept on the remote" || ko "Y2: the wave branch was deleted"
/usr/bin/grep -q ":refs/heads/$W22" "$T/pushes.log" && ko "Y2: the wave branch was pushed for deletion" || ok "Y2: no deletion push for the wave branch"

# Y2b (#836): the same commit already on a remote fix/ branch is salvaged, so it holds nothing: the wave lands empty.
W25=wave/run-836e; start2 run-836e >/dev/null
mkdir -p "$T/co/runs/run-836e"; printf '%s\n' 'CHILD — GitHub issue #722 (bug; one root cause, one branch):' > "$T/co/runs/run-836e/request.txt"
x5=$(mktemp -d); $G clone -q "$T/origin.git" "$x5/c" 2>/dev/null
(cd "$x5/c" && $G checkout -q -b tmp dev && echo salv > f722 && $G add f722 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 722" -m "Refs #722" && $G push -q origin HEAD:refs/heads/fix/722-salvage 2>/dev/null)
X5SHA=$($G -C "$x5/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x5/c" HEAD:refs/heads/squad/run/run-836e 2>/dev/null
$G -C "$T/co" fetch -q "$T/origin.git" '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null
[ "$($G -C "$T/co" branch -r --contains "$X5SHA" 2>/dev/null | /usr/bin/grep -c .)" -ge 1 ] && ok "Y2b: the commit is on a remote fix/ branch" || ko "Y2b: the setup did not put the commit on a remote branch"
r=$(land2 run-836e 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y2b: a commit already on a remote fix/ branch does not hold the wave";; *) ko "Y2b: not an empty landing: '$r'";; esac || ko "Y2b: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y2b: a remote-reachable commit was refused: '$r'";; *) ok "Y2b: no refusal names the salvaged commit";; esac

# Y3 (#836): a leftover squad/ branch of another run, holding a commit that names an issue of this run's request, does not hold
# this run's wave: only the run's own branch and trees are read, and the commit is local to that other branch only.
W23=wave/run-836c; start2 run-836c >/dev/null
mkdir -p "$T/co/runs/run-836c"; printf '%s\n' 'CHILD — GitHub issue #731 (bug; one root cause, one branch):' > "$T/co/runs/run-836c/request.txt"
x3=$(mktemp -d); $G clone -q "$T/origin.git" "$x3/c" 2>/dev/null
(cd "$x3/c" && $G checkout -q -b tmp dev && echo other > f731 && $G add f731 && $G -c user.email=a@b -c user.name=t commit -q -m "other 731" -m "Refs #731")
$G -C "$T/co" fetch -q "$x3/c" HEAD:refs/heads/squad/run/run-836-old 2>/dev/null
r=$(land2 run-836c 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y3: a leftover branch of another run does not hold the wave";; *) ko "Y3: not empty: '$r'";; esac || ko "Y3: exit $rc: '$r'"

# Y4 (#836): past the age cap an orphan child commit does not hold the wave: the capped wave lands empty and its issue goes back
# to the queue, as the capped landing requires (ADR-0145 rule 13).
W26=wave/run-836d; start2 run-836d >/dev/null
mkdir -p "$T/co/runs/run-836d"; printf '%s\n' 'CHILD — GitHub issue #741 (bug; one root cause, one branch):' > "$T/co/runs/run-836d/request.txt"
# A run that ended keeps its record: without it `orphan_child` finds no issue of the run and the capped case never runs (#1835).
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-836d/record.jsonl"
x4=$(mktemp -d); $G clone -q "$T/origin.git" "$x4/c" 2>/dev/null
(cd "$x4/c" && $G checkout -q -b tmp dev && echo cap > f741 && $G add f741 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 741" -m "Refs #741")
$G -C "$T/co" fetch -q "$x4/c" HEAD:refs/heads/squad/run/run-836d 2>/dev/null
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-836d"
: > "$T/board.log"
r=$(land2 run-836d 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y4: a capped wave with an orphan child commit lands empty (exit 0)";; *) ko "Y4: not an empty landing: '$r'";; esac || ko "Y4: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y4: the capped wave was refused: '$r'";; *) ok "Y4: the capped wave is not refused";; esac
case "$r" in *"WAVE REQUEUE #741"*"age cap"*) ok "Y4: the orphan's issue goes back to the queue";; *) ko "Y4: the issue was not requeued: '$r'";; esac
/usr/bin/grep -qx 'o 3 Todo 741' "$T/board.log" && ok "Y4: the card of the requeued issue reads Todo" || ko "Y4: no Todo card for #741"
$O rev-parse -q --verify "refs/heads/$W26" >/dev/null && ko "Y4: the capped wave branch is kept" || ok "Y4: the capped wave branch goes"
# Y5 (#1106): an issue the run itself requeued is no orphan. Its child commit stays on the run's branch and the loop builds the
# issue again, so the wave lands empty instead of refusing on every later cycle.
W27=wave/run-1106; start2 run-1106 >/dev/null
mkdir -p "$T/co/runs/run-1106"; printf '%s\n' 'CHILD — GitHub issue #761 (bug; one root cause, one branch):' > "$T/co/runs/run-1106/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-1106/record.jsonl"
x7=$(mktemp -d); $G clone -q "$T/origin.git" "$x7/c" 2>/dev/null
(cd "$x7/c" && $G checkout -q -b tmp dev && echo fix > f761 && $G add f761 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 761" -m "Refs #761")
X7SHA=$($G -C "$x7/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x7/c" HEAD:refs/heads/squad/run/run-1106 2>/dev/null
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" requeue "x/y#761" run-1106 "$X7SHA" >/dev/null 2>&1
r=$(land2 run-1106 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y5: a commit of an issue the run requeued does not hold the wave";; *) ko "Y5: not an empty landing: '$r'";; esac || ko "Y5: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y5: the wave was refused: '$r'";; *) ok "Y5: no WAVE REFUSE line";; esac
# Y6 (#1833): a wave already in the base keeps refusing its landing every cycle. The run's wave landed in an earlier cycle: its
# tip is dev's now, and one of its children entered it. One child of the run never entered, so its commit sits on the run's own
# branch only. The wave landed, so the pass writes its books and drops its markers as a kept landing branch does (#1826): the
# entered child is delivered, the child that never entered goes back to the queue, and no WAVE REFUSE line holds the run. The
# refusal for an unlanded wave with such a commit stays (Y2).
W29=wave/run-1833; start2 run-1833 >/dev/null
mkdir -p "$T/co/runs/run-1833"
printf '%s\n' 'CHILD — GitHub issue(s) 1752, 1753 (bug; one root cause, one branch):' > "$T/co/runs/run-1833/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-1833/record.jsonl"
child 1752 g1752 "origin/$W29"; en2 run-1833 1752 >/dev/null
# The wave lands: dev carries its tip, and the wave branch stays behind (an earlier landing left it, its deletion unmet).
$O update-ref refs/heads/dev "$($G -C "$T/c1752" rev-parse HEAD)"
# The child that never entered: a commit of this run naming #1753, on a local branch of the checkout only.
x9=$(mktemp -d); $G clone -q "$T/origin.git" "$x9/c" 2>/dev/null
(cd "$x9/c" && $G checkout -q -b tmp dev && echo fix > f1753 && $G add f1753 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 1753" -m "Refs #1753")
X9SHA=$($G -C "$x9/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x9/c" HEAD:refs/heads/squad/run/run-1833 2>/dev/null
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/auto/orphan-refused-run-1833"
r=$(land2 run-1833 2>&1); rc=$?
[ $rc = 0 ] && ok "Y6: a wave whose tip the base already carries lands (exit 0)" || ko "Y6: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y6: the landed wave is still refused: '$r'";; *) ok "Y6: no WAVE REFUSE line";; esac
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^WAVE .* already landed')" = 1 ] && ok "Y6: the landing names the wave as already landed, once" || ko "Y6: no single already-landed line: '$r'"
$O rev-parse -q --verify "refs/heads/$W29" >/dev/null && ko "Y6: the landed wave branch is kept" || ok "Y6: the landed wave branch goes"
[ ! -e "$T/st/auto/wave-run-1833" ] && ok "Y6: the run's wave marker goes" || ko "Y6: the run's wave marker is kept: '$(cat "$T/st/auto/wave-run-1833" 2>/dev/null)'"
[ ! -e "$T/st/auto/orphan-refused-run-1833" ] && ok "Y6: no orphan-refused marker is written" || ko "Y6: an orphan-refused marker was written"
/usr/bin/grep -q 'issues/1752 -f state=closed' "$T/gh.log" && ok "Y6: the child that entered the landed wave is delivered" || ko "Y6: #1752 is not closed: $(cat "$T/gh.log")"
/usr/bin/grep -q 'issues/1753 -f state=closed' "$T/gh.log" && ko "Y6: the child that never entered is closed" || ok "Y6: the child that never entered is not delivered"
case "$r" in *"WAVE REQUEUE #1753"*landed*) ok "Y6: the child that never entered goes back to the queue, its wave landed without it";; *) ko "Y6: #1753 is not requeued: '$r'";; esac
/usr/bin/grep -qx 'o 3 Todo 1753' "$T/board.log" && ok "Y6: the card of the requeued issue reads Todo" || ko "Y6: no Todo card for #1753: $(cat "$T/board.log" 2>/dev/null)"
[ -z "$(tries 1753 run-1833)" ] && ok "Y6: the requeue counts no landing try" || ko "Y6: tries of #1753: $(tries 1753 run-1833)"
rm -rf "$x9"

# (#845) The orphan-commit refusal applies only to a run whose verdict is completed. A failed, needs-human, stopped or verdict-less
# run's child commit never holds its wave: the wave takes its normal path (an empty landing here, with no requeue).
for k in failed:751 needs-human:752 stopped:753 none:754; do
  v=${k%%:*}; n=${k##*:}; run=run-845-$v; W=wave/$run; start2 $run >/dev/null
  mkdir -p "$T/co/runs/$run"; printf 'CHILD — GitHub issue #%s (bug; one root cause, one branch):\n' "$n" > "$T/co/runs/$run/request.txt"
  if [ "$v" = none ]; then printf '%s\n' '{"event": "run_start"}' > "$T/co/runs/$run/record.jsonl"
  else printf '%s\n%s\n' '{"event": "run_start"}' "{\"event\": \"verdict\", \"verdict\": \"$v\", \"reasons\": []}" > "$T/co/runs/$run/record.jsonl"; fi
  x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
  (cd "$x/c" && $G checkout -q -b tmp dev && echo o > "f$n" && $G add "f$n" && $G -c user.email=a@b -c user.name=t commit -q -m "fix $n" -m "Refs #$n")
  $G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/$run 2>/dev/null
  r=$(land2 $run 2>&1); rc=$?
  [ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Z1 $v: the orphan child commit does not hold the wave (exit 0, landed empty)";; *) ko "Z1 $v: not an empty landing: '$r'";; esac || ko "Z1 $v: exit $rc: '$r'"
  case "$r" in *"WAVE REFUSE"*) ko "Z1 $v: the wave was refused: '$r'";; *) ok "Z1 $v: no WAVE REFUSE line";; esac
  rm -rf "$x"
done

# (#845) A completed run's orphan child commit is refused once per wave and commit: over two passes the refusal line appears once,
# and the wave is kept both times.
W=wave/run-845-done; start2 run-845-done >/dev/null
mkdir -p "$T/co/runs/run-845-done"; printf '%s\n' 'CHILD — GitHub issue #755 (bug; one root cause, one branch):' > "$T/co/runs/run-845-done/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-845-done/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo o > f755 && $G add f755 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 755" -m "Refs #755")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/run-845-done 2>/dev/null
r1=$(land2 run-845-done 2>&1); rc1=$?
r2=$(land2 run-845-done 2>&1); rc2=$?
[ $rc1 = 1 ] && [ $rc2 = 1 ] && ok "Z2: a completed run's orphan commit still refuses the wave on both passes" || ko "Z2: exit $rc1 $rc2: '$r1' / '$r2'"
[ "$(printf '%s\n%s\n' "$r1" "$r2" | /usr/bin/grep -c '^WAVE REFUSE')" = 1 ] && ok "Z2: the refusal line appears once over two passes" || ko "Z2: refusal lines over two passes: '$r1' / '$r2'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "Z2: the wave branch is kept" || ko "Z2: the wave branch was deleted"
rm -rf "$x"

# (#866) A reviewed child commit that names an issue of the run never leaves the wave closed empty while it sits on a local branch
# only. R1: a completed run whose child's fix run committed the fix in its own worktree: the fix run's squad/<fix run>/ branch
# holds the only copy, so the wave is kept and refused. R2: a failed run whose reviewed head (auto/rvd-<run>-<child>) is a local-only
# commit that names its issue: the wave is kept and refused too. Z1 still lets an unreviewed commit of a failed run go.
W=wave/run-866a; start2 run-866a >/dev/null
mkdir -p "$T/co/runs/run-866a" "$T/co/runs/run-866fix"; printf '%s\n' 'CHILD — GitHub issue #787 (bug; one root cause, one branch):' > "$T/co/runs/run-866a/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-866a/record.jsonl"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-866fix/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo fix > f787 && $G add f787 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 787" -m "Refs #787")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run-866fix/worktrees_child-route.router.1 2>/dev/null
printf '%s\n' "$T/co/runs/run-866fix" > "$T/st/auto/ccfix-run-866a-route.router.1"
r=$(land2 run-866a 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REFUSE"*"787"*) ok "R1: a fix run's reviewed commit keeps its completed run's wave (refused, exit 1)";; *) ko "R1: not refused: '$r' (exit $rc)";; esac || ko "R1: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "R1: the wave branch is kept" || ko "R1: the wave branch was deleted"
case "$r" in *"landed empty"*) ko "R1: the wave landed empty: '$r'";; *) ok "R1: the wave does not land empty";; esac
rm -f "$T/st/auto/ccfix-run-866a-route.router.1"; rm -rf "$x"

W=wave/run-866b; start2 run-866b >/dev/null
mkdir -p "$T/co/runs/run-866b"; printf '%s\n' 'CHILD — GitHub issue #794 (bug; one root cause, one branch):' > "$T/co/runs/run-866b/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed"]}' > "$T/co/runs/run-866b/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo fix > f794 && $G add f794 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 794" -m "Refs #794")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/run-866b 2>/dev/null
printf '%s 4242\n' "$($G -C "$x/c" rev-parse HEAD)" > "$T/st/auto/rvd-run-866b-route.router.1"
r=$(land2 run-866b 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REFUSE"*"794"*) ok "R2: a failed run's reviewed commit keeps its wave (refused, exit 1)";; *) ko "R2: not refused: '$r' (exit $rc)";; esac || ko "R2: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "R2: the wave branch is kept" || ko "R2: the wave branch was deleted"
rm -f "$T/st/auto/rvd-run-866b-route.router.1"; rm -rf "$x"

rm -rf "$x2" "$x3" "$x4" "$x5"


# Z3 (#869): one commit per issue. A cluster child that writes one fix commit per issue (each naming exactly one `Refs #N`)
# closes every issue its commits fix: the wave closes the siblings, not only the first issue its tip names.
W17=wave/run-17; start2 run-17 >/dev/null
rm -rf "$T/c870"; $G clone -q "$T/origin.git" "$T/c870" 2>/dev/null
(cd "$T/c870" && $G checkout -q -b gui/870 "origin/$W17" && for n in 870 871 872; do echo "$n" > "g$n" && $G add "g$n" && $G -c user.email=a@b -c user.name=t commit -q -m "fix $n" -m "Refs #$n"; done)
en2 run-17 870 >/dev/null; mkdir -p "$T/co/runs/run-17"
printf '%s\n%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #870 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #871 work"}}' \
  '{"event": "spawn_accepted", "child": "c", "definition": {"prompt": "GitHub issue #872 work"}}' > "$T/co/runs/run-17/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-17 2>&1); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g871 2>/dev/null && echo yes)" = yes ] && ok "Z3: the cluster wave lands one commit per issue (exit 0)" || ko "Z3: the cluster wave did not land: '$r' (exit $rc)"
for n in 870 871 872; do [ "$(/usr/bin/grep -c "issues/$n -f state=closed" "$T/gh.log")" = 1 ] && ok "Z3: #$n, fixed by its own commit, closes once" || ko "Z3: closes for #$n: '$(cat "$T/gh.log")'"; done

# Z4 (#869): a commit that names several issues delivers none of them. The wave closes none of them, and each one of the run's
# issues is requeued with that reason (cap: it counts no failed dispatch, since its work reached the base).
W18=wave/run-18; start2 run-18 >/dev/null
rm -rf "$T/c880"; $G clone -q "$T/origin.git" "$T/c880" 2>/dev/null
(cd "$T/c880" && $G checkout -q -b gui/880 "origin/$W18" && echo cluster > g880 && $G add g880 && $G -c user.email=a@b -c user.name=t commit -q -m "cluster work" -m "Refs #880
Refs #881
Refs #882")
en2 run-18 880 >/dev/null; mkdir -p "$T/co/runs/run-18"
printf '%s\n%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #880 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #881 work"}}' \
  '{"event": "spawn_accepted", "child": "c", "definition": {"prompt": "GitHub issue #882 work"}}' > "$T/co/runs/run-18/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-18 2>&1); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g880 2>/dev/null && echo yes)" = yes ] && ok "Z4: the multi-issue commit lands (exit 0)" || ko "Z4: did not land: '$r' (exit $rc)"
[ -z "$(/usr/bin/grep 'issues/[0-9]* -f state=closed' "$T/gh.log")" ] && ok "Z4: a commit naming several issues closes none of them" || ko "Z4: closes: '$(cat "$T/gh.log")'"
for n in 880 881 882; do /usr/bin/grep -qx "o 3 Todo $n" "$T/board.log" && ok "Z4: #$n is requeued" || ko "Z4: #$n not requeued: '$(cat "$T/board.log")'"; done
case "$r" in *"WAVE REQUEUE #881"*"several issues"*) ok "Z4: the requeue names the multi-issue commit as its reason";; *) ko "Z4: no reason named for #881: '$r'";; esac

# Z5 (#869): the empty-wave route reads the same rule. A multi-issue commit that reached the base after the wave was cut
# delivers none of its issues to a later empty wave of a run dispatched for one of them; a single-issue commit still does.
W19=wave/run-19; start2 run-19 >/dev/null
rm -rf "$T/out19"; $G clone -q "$T/origin.git" "$T/out19" 2>/dev/null
(cd "$T/out19" && $G checkout -q dev && echo m > o890 && $G add o890 && $G -c user.email=a@b -c user.name=t commit -q -m "cluster work" -m "Refs #890
Refs #891
Refs #892" && echo s > o893 && $G add o893 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 893" -m "Refs #893" && $G push -q origin HEAD:dev)
mkdir -p "$T/co/runs/run-19"
printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #891 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #893 work"}}' > "$T/co/runs/run-19/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-19 2>&1); rc=$?
case "$r" in *"landed empty"*) ok "Z5: the wave lands empty (exit 0)";; *) ko "Z5: not an empty landing: '$r' (exit $rc)";; esac
/usr/bin/grep -q 'issues/891 -f state=closed' "$T/gh.log" && ko "Z5: a sibling of a multi-issue commit was closed" || ok "Z5: a multi-issue commit delivers none of its issues to an empty wave"
[ "$(/usr/bin/grep -c 'issues/893 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z5: a single-issue commit still delivers to an empty wave" || ko "Z5: closes for #893: '$(cat "$T/gh.log")'"

# Z6 (#869): a capped landing requeues no child that entered the wave. Child 940 entered; its commit reached the base before the
# landing, so the rebase drops it and $issues does not carry it. It is not missing from the wave, so it is not requeued.
W20=wave/run-20; start2 run-20 >/dev/null
child 940 g940 "origin/$W20"; en2 run-20 940 >/dev/null
child 941 g941 "origin/$W20"; en2 run-20 941 >/dev/null
rm -rf "$T/dup20"; $G clone -q "$T/origin.git" "$T/dup20" 2>/dev/null
(cd "$T/dup20" && $G checkout -q dev && $G fetch -q "$T/c940" gui/940 && GIT_COMMITTER_NAME=other $G -c user.email=a@b -c user.name=t cherry-pick FETCH_HEAD >/dev/null 2>&1 && $G push -q origin HEAD:dev)
mkdir -p "$T/co/runs/run-20"; printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #940 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #941 work"}}' > "$T/co/runs/run-20/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-20"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-20 2>&1); rc=$?
[ $rc = 0 ] && ok "Z6: the capped wave lands (exit 0)" || ko "Z6: exit $rc: '$r'"
/usr/bin/grep -qx 'o 3 Todo 940' "$T/board.log" && ko "Z6: a child that entered the wave was requeued as missing: '$r'" || ok "Z6: a child that entered the wave is not requeued"
[ "$(/usr/bin/grep -c 'issues/941 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z6: the sibling the wave carries closes" || ko "Z6: closes for #941: '$(cat "$T/gh.log")'"

# Z7 (#872): a squashed RED+fix commit delivers its Refs. Only a commit whose SUBJECT starts with "TDD RED" is skipped; a body
# line that names "TDD RED" (prepush needs it) does not hide the `Refs #N` of its commit.
W21=wave/run-21; start2 run-21 >/dev/null
rm -rf "$T/c895"; $G clone -q "$T/origin.git" "$T/c895" 2>/dev/null
(cd "$T/c895" && $G checkout -q -b gui/895 "origin/$W21" && echo r895 > g895 && $G add g895 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 895" -m "TDD RED: test_x
Refs #895" && mkdir -p tests && echo r896 > tests/test_896.py && $G add tests/test_896.py && $G -c user.email=a@b -c user.name=t commit -q -m "TDD RED: test_y" -m "Refs #896")
en2 run-21 895 >/dev/null; mkdir -p "$T/co/runs/run-21"
printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #895 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #896 work"}}' > "$T/co/runs/run-21/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-21 2>&1); rc=$?
[ "$(/usr/bin/grep -c 'issues/895 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z7: a squashed RED+fix commit closes its Refs #895 once" || ko "Z7: closes for #895: '$(cat "$T/gh.log")' (exit $rc: '$r')"
/usr/bin/grep -q 'issues/896 -f state=closed' "$T/gh.log" && ko "Z7: a commit whose subject is TDD RED closed #896" || ok "Z7: a commit whose subject is TDD RED does not close #896"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
