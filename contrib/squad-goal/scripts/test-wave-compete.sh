#!/bin/bash
# seconds: 8.03
# An offline run lands two children while a competing gate holds the lock (spec 1163, #1177): a throwaway repository with a
# real remote and a real worktree, the real wave.sh, gate.sh, serial.sh and caps.sh, and one gate that holds the lock.
# Both children enter before the competing gate starts, the wave's landing check queues behind it, and both land inside the
# deadline. The gate history names each outcome, and the queued landing holds no worker slot while it waits (ADR-0163,
# ADR-0164). The remote is a bare repository on this machine and the gh and board are stubs: nothing here touches the network.
# usage: bash test-wave-compete.sh [scripts dir]
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, so drop them all (#1410).
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
export SERIAL_WAIT_SECONDS=60   # a landing that queues for the competing gate answers inside one minute, never for the default
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" \
  "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2 SG_GATE_WORKERS=2
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=dev   # the competing gate takes the same lock and writes the same record
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git\n' "$T"
  printf "export SG_WAVE_TIP_GATES=%q\n" "bash '$T/s/gate.sh' true"   # the real gate: it queues for the lock and writes the record
  printf 'export SG_BOARD_VOCAB=shipped\n'
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *"/check-runs"*) echo "{}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"
[ "$($O rev-parse --is-bare-repository)" = true ] && ok "the suite runs against a real bare remote" || ko "the remote is not a bare repository"
R=run-77; W=wave/$R
start() { (cd "$T/co" && bash "$T/s/wave.sh" start "$R"); }
# A child is a clone of the real remote, on its own branch, cut from the wave branch: a real worktree.
child() { local n=$1 f=$2 o
  o=$($G clone -q "$T/origin.git" "$T/c$n" 2>&1) || { ko "child #$n: the clone failed: $o"; return 1; }
  o=$((cd "$T/c$n" && $G checkout -q -b "gui/$n" "origin/$W" && echo "$n" > "$f" && $G add "$f" && $G -c user.email=a@b -c user.name=t commit -q -m "work $n (Refs #$n)") 2>&1) \
    || { ko "child #$n: the worktree is not ready: $o"; return 1; }
  printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_$n.md"
  $G -C "$T/c$n" rev-parse HEAD > "$T/st/review-gui_$n.sha"; printf '%s\n' "$W" > "$T/st/review-gui_$n.base"; }
en() { (cd "$T/c$1" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter "$R" "$1"); }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$R"); }
capsctl() { PYTHONPATH="$SG_SQUAD" python3 -m squad.caps "$@"; }
occ() { capsctl | python3 -c 'import json,sys; print(json.load(sys.stdin)["occupancy"]["gates"])'; }
sha() { $O rev-parse "refs/heads/$1"; }
gates() { /usr/bin/grep -c . "$T/st/gate-history" 2>/dev/null; }

r=$(start); case "$r" in "WAVE $W "*) ok "the run cuts its wave branch from dev";; *) ko "no wave branch: '$r'";; esac
child 771 g771; r=$(en 771); [ "$(sha $W)" = "$($G -C "$T/c771" rev-parse HEAD)" ] && ok "the first child enters the wave" || ko "child #771 did not enter: '$r'"
child 772 g772; r=$(en 772); [ "$(sha $W)" = "$($G -C "$T/c772" rev-parse HEAD)" ] && ok "the second child enters the wave" || ko "child #772 did not enter: '$r'"
[ "$(awk -F'\t' '$8 == "pass"' "$T/st/gate-history" 2>/dev/null | /usr/bin/grep -c .)" -ge 2 ] && ok "the record names each entry's outcome" \
  || ko "entry outcomes: '$(cat "$T/st/gate-history" 2>/dev/null)'"

# The competing gate takes the lock and its two slots and holds them. The landing that starts meanwhile queues for the
# lock and must hold no slot while it waits (ADR-0163, ADR-0164).
bash "$T/s/gate.sh" sh -c 'sleep 3' >/dev/null 2>&1 & comp=$!
for _ in $(seq 1 100); do [ -d "$T/st/locks/gate" ] && [ "$(occ)" = 2 ] && break; sleep 0.05; done
held_before=$(occ)
t0=$(date +%s); ( land ) > "$T/land.out" 2>&1 & lp=$!
clog=$T/st/wave-check-wave_run-77.log   # the tip check's own output, where the gate's wait line goes
for _ in $(seq 1 200); do /usr/bin/grep -q 'serial: waiting for lock gate' "$clog" 2>/dev/null && break; sleep 0.05; done
queued=$(/usr/bin/grep -c 'serial: waiting for lock gate' "$clog" 2>/dev/null)
held_wait=$(occ)
[ "$held_before" = 2 ] && [ "$queued" = 1 ] && [ "$held_wait" = 2 ] \
  && ok "the landing queued behind the competing gate holds no worker slot" \
  || ko "queued landing: before $held_before, queued $queued, while waiting $held_wait"
wait "$lp"; lrc=$?; wait "$comp"; el=$(( $(date +%s) - t0 ))
[ "$lrc" = 0 ] && ok "the landing pass succeeds" || ko "landing exit $lrc: '$(cat "$T/land.out")'"
[ "$el" -lt 60 ] && ok "both children land inside the deadline (${el}s)" || ko "the landing took ${el}s"
$O merge-base --is-ancestor "$($G -C "$T/c771" rev-parse HEAD)" "$(sha dev)" && $O merge-base --is-ancestor "$($G -C "$T/c772" rev-parse HEAD)" "$(sha dev)" \
  && ok "both children's work is in dev" || ko "the children are not both in dev: '$(cat "$T/land.out")'"
[ "$(/usr/bin/grep -c 'issues/77[12] -f state=closed' "$T/gh.log")" = 2 ] && ok "each child's issue closes" || ko "closes: '$(cat "$T/gh.log")'"
[ "$(/usr/bin/grep -cE '^o 3 Closed 77[12]$' "$T/board.log")" = 2 ] && ok "each child's card reads Closed" || ko "cards: '$(cat "$T/board.log")'"

# The record names every outcome a gate attempt produced, and each one is one of the three words.
bad=$(awk -F'\t' 'NF >= 8 && $8 != "" && $8 != "pass" && $8 != "fail" && $8 != "unproven" { print $8 }' "$T/st/gate-history" 2>/dev/null)
[ -z "$bad" ] && ok "every gate attempt in the record names pass, fail or unproven" || ko "an unnamed outcome: '$bad'"
[ "$(awk -F'\t' '$8 == "pass"' "$T/st/gate-history" 2>/dev/null | /usr/bin/grep -c .)" -ge 3 ] \
  && ok "the record names the two entries and the landing's own outcome" || ko "outcomes: '$(cat "$T/st/gate-history" 2>&1)'"
[ "$(gates)" -ge 4 ] && ok "the record names every gate the scenario ran" || ko "gate history lines: '$(gates)'"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
