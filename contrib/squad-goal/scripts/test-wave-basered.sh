#!/bin/bash
# seconds: 30.00
# A red base head no longer blocks the wave that would fix it (#2597): a wave whose tip carries the red head lands on its own
# green tip check, and a wave whose tip does not carry that head waits and says so once. The batch and the lone landing each
# take that path, and a red base never makes the check blame a child. A bare local remote and a stub gh: no GitHub call.
# usage: bash test-wave-basered.sh [scripts dir]
# mirrors: wave.sh
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS GATE_REUSE_FILE SG_LAND_BATCH SG_LAND_QUEUE SG_GATE_BATCH RED_BASE
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/columnlimit.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$d/fast-gate.sh" "$d/quality-reading.py" "$d/autohunt.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_RETIRE_CMD=/usr/bin/true
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_BOARD_VOCAB=shipped\n: "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
# The gh stub answers a red completed check on every check-runs read when RED_BASE is set (the base's own check, #786); the
# bug a red base files is a stub too, so no issue is created. A close answers GitHub's own shape.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *"/actions/runs/"*"/jobs"*) echo "{\\"total_count\\":1,\\"jobs\\":[{\\"conclusion\\":\\"failure\\",\\"steps\\":[{\\"name\\":\\"run\\"}]}]}";; *"/check-runs"*) if [ -n "${RED_BASE:-}" ]; then echo "{\\"check_runs\\":[{\\"id\\":1,\\"name\\":\\"fast-battery\\",\\"status\\":\\"completed\\",\\"conclusion\\":\\"failure\\",\\"details_url\\":\\"https://github.com/x/y/actions/runs/7801/job/1\\"}]}"; else echo "{}"; fi;; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" SG_BASE_RED_FILE_CMD=/usr/bin/true
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"
mkdir -p "$T/origin.git/hooks"
printf '#!/bin/bash\nwhile read -r old new ref; do printf "%%s\\t%%s\\t%%s\\n" "$old" "$new" "$ref" >> "%s/pushes.log"; done\n' "$T" > "$T/origin.git/hooks/pre-receive"
chmod +x "$T/origin.git/hooks/pre-receive"
devpushes() { awk -F'\t' '$3 == "refs/heads/dev"' "$T/pushes.log" 2>/dev/null | /usr/bin/grep -c .; }
# mkrun <run> <issue>: the run's folder and its wave branch, cut from the base as it is now, with one child commit
# `work N (Refs #N)` that writes g-<run>.
mkrun() { local run=$1 n=$2 rd="$T/co/runs/$1"
  mkdir -p "$rd"; : > "$rd/record.jsonl"; printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"; echo "$run" > "$T/st/auto/disp-$n"
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run" && echo "$run" > "g-$run" && $G add "g-$run" \
    && $G commit -q -m "work $n (Refs #$n)" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null
  (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" wave "x/y#$n" "$run" entered "child entered") >/dev/null; }
# The base moves under the open waves: one commit lands on dev, and the wave tips no longer carry the new head.
devmoved() { rm -rf "$T/w2"; $G clone -q "$T/origin.git" "$T/w2" 2>/dev/null
  (cd "$T/w2" && $G checkout -q dev && echo other > other && $G add other && $G -c user.email=a@b -c user.name=t commit -q -m "other work" && $G push -q origin HEAD:dev); }
# rebase_run <run>: the run's wave is cut again on the moved base, as a child's entry after the move would leave it.
rebase_run() { (cd "$T/c-$1" && $G fetch -q origin dev && $G rebase -q origin/dev >/dev/null 2>&1 && $G push -q -f origin "HEAD:refs/heads/wave/$1") 2>/dev/null; }
queue() { printf '%s\n' "$T/co/runs/$1" > "$T/st/auto/wland-$1.queued"; }
# The tip gate stub: green, and it names the tree it measured. TIPRED makes it red.
TIP='echo "tip $($G rev-parse HEAD)" >> "$TIPLOG"; [ -z "${TIPRED:-}" ]'
export TIPLOG=$T/tip.log
landq() { (cd "$T/co" && RED_BASE=${RED_BASE:-} SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES=$TIP bash "$T/s/wave.sh" land "$1" "$T/co/runs/$1" 2>&1); }
lando() { (cd "$T/co" && RED_BASE=${RED_BASE:-} SG_LAND_BATCH=1 SG_WAVE_TIP_GATES=$TIP bash "$T/s/wave.sh" land "$1" "$T/co/runs/$1" 2>&1); }
reset() { : > "$T/gh.log"; : > "$TIPLOG"; : > "$T/pushes.log"; rm -f "$T/st/auto/basecheck-"* "$T/st/auto/basered-"*; }

# Setup: two waves cut from the base's first head, and then the base moves to its next head H1.
mkrun run-p 21; mkrun run-q 22
base0=$($O rev-parse dev); devmoved; H1=$($O rev-parse dev); h1=${H1:0:9}
$O merge-base --is-ancestor "$H1" "wave/run-p" && ko "setup: run-p carries the moved head" || ok "setup: the wave tips do not carry the moved base head"

# B (criterion 2, the batch): the base is red at H1 and no wave of the batch carries H1, so the batch waits, says it once,
# keeps its queue and pushes nothing. A later pass on the same head says nothing more.
reset; queue run-q
r=$(RED_BASE=1 landq run-p); rc=$?
[ $rc = 1 ] && ok "B: a batch whose tips do not carry the red head waits (exit 1)" || ko "B: exit $rc: '$r'"
case "$r" in *"WAVE WAIT"*"base dev is red at $h1"*) ok "B: the wait line names the red head";; *) ko "B: no wait line for $h1: '$r'";; esac
[ "$(devpushes)" = 0 ] && ok "B: the batch pushes nothing to the base" || ko "B: pushed: '$(cat "$T/pushes.log")'"
# The second pass on the same red head keeps the say-once marker (the pass writes it), and only the read cache goes.
rm -f "$T/st/auto/basecheck-"*; : > "$T/pushes.log"; queue run-q
r=$(RED_BASE=1 landq run-p); rc=$?
[ $rc = 1 ] && [ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE WAIT')" = 0 ] \
  && ok "B: a later pass on the same red head says it once and no more" || ko "B: the second pass said it again: '$r'"
rm -f "$T/st/auto"/wland-*

# A (criteria 1 and 3, the batch): the two waves carry H1 and the batch's one tip check is green, so the batch lands past the
# red base, and one line names the red head and the evidence that carried the tip.
rebase_run run-p; rebase_run run-q
$O merge-base --is-ancestor "$H1" "wave/run-p" && $O merge-base --is-ancestor "$H1" "wave/run-q" || ko "A: setup: a wave does not carry H1"
reset; queue run-q
r=$(RED_BASE=1 landq run-p); rc=$?
[ $rc = 0 ] && ok "A: a batch whose tips carry the red head lands (exit 0)" || ko "A: exit $rc: '$r'"
[ "$(devpushes)" = 1 ] && ok "A: the batch lands with one push" || ko "A: pushes: '$(cat "$T/pushes.log")'"
$O cat-file -e dev:g-run-p 2>/dev/null && $O cat-file -e dev:g-run-q 2>/dev/null && ok "A: both waves are in the base" || ko "A: dev lacks a wave"
case "$r" in *"WAVE PAST RED BASE"*"$h1"*) ok "A: one line names the red head the wave landed past";; *) ko "A: no PAST RED BASE line for $h1: '$r'";; esac
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE PAST RED BASE')" = 1 ] && ok "A: the past-red line names the red head once" || ko "A: past-red lines: '$r'"
case "$r" in *"WAVE PAST RED BASE"*green*) ok "A: the line names the evidence that carried the tip";; *) ko "A: the line names no evidence: '$r'";; esac
rm -f "$T/st/auto"/wland-*

# C (criteria 1 and 3, the lone landing): a wave cut from the head the red check names lands past the red with its own green
# check, and the line names the red head and the tip.
mkrun run-r 23
$O merge-base --is-ancestor "$($O rev-parse dev)" "wave/run-r" && ok "C: the wave carries the base head" || ko "C: the wave does not carry the base head"
HC=$($O rev-parse dev); hc=${HC:0:9}
reset
r=$(RED_BASE=1 lando run-r); rc=$?
[ $rc = 0 ] && ok "C: a lone wave that carries the red head lands (exit 0)" || ko "C: exit $rc: '$r'"
[ "$(devpushes)" = 1 ] && ok "C: the lone landing pushes once" || ko "C: pushes: '$(cat "$T/pushes.log")'"
case "$r" in *"WAVE PAST RED BASE wave/run-r"*"$hc"*) ok "C: the line names the red head the wave landed past";; *) ko "C: no PAST RED BASE line for $hc: '$r'";; esac
case "$r" in *"WAVE PAST RED BASE wave/run-r"*"green"*) ok "C: the line names the evidence";; *) ko "C: the line names no evidence: '$r'";; esac

# D (a wave that carries the red head but does not fix it): the red base plus a red tip check never blames the child. The wave
# waits, and the innocent child stays in the wave.
mkrun run-s 24
reset
r=$(RED_BASE=1 TIPRED=1 SG_WAVE_CHILD_GATES='[ "$SG_WAVE_CHILD" = 24 ] && exit 1; exit 0' lando run-s); rc=$?
[ $rc = 1 ] && ok "D: a red tip on a red base waits (exit 1)" || ko "D: exit $rc: '$r'"
case "$r" in *"leaves the wave"*) ko "D: the wave blamed the child of a red base: '$r'";; *) ok "D: no child is blamed for the base's red";; esac
[ "$(devpushes)" = 0 ] && ok "D: nothing lands on the red base without green evidence" || ko "D: pushed: '$(cat "$T/pushes.log")'"

[ $fail = 0 ] && echo "PASS test-wave-basered.sh" || echo "FAILED test-wave-basered.sh"
exit $fail
