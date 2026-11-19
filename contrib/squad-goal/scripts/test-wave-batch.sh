#!/bin/bash
# seconds: 25.79
# Regression test for the batch landing (#2058): the landing pass takes every run that waits for the landing lock, stacks
# their waves on the base, runs one tip gate on that exact tip and pushes it by one fast-forward. A red or unproven batch
# gate, or a stack conflict, falls back to one landing per run. SG_LAND_BATCH=1 keeps the one-run pass. A bare local remote,
# a stub gh, a stub board: no GitHub call. usage: bash test-wave-batch.sh [scripts dir]
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS GATE_REUSE_FILE SG_LAND_BATCH SG_LAND_QUEUE SG_GATE_BATCH
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/columnlimit.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$d/fast-gate.sh" "$d/quality-reading.py" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_RETIRE_CMD=/usr/bin/true
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_BOARD_VOCAB=shipped\n: "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"
# The remote logs every ref a push writes: one batch is one push to dev.
mkdir -p "$T/origin.git/hooks"
printf '#!/bin/bash\nwhile read -r old new ref; do printf "%%s\\t%%s\\t%%s\\n" "$old" "$new" "$ref" >> "%s/pushes.log"; done\n' "$T" > "$T/origin.git/hooks/pre-receive"
chmod +x "$T/origin.git/hooks/pre-receive"
devpushes() { awk -F'\t' '$3 == "refs/heads/dev"' "$T/pushes.log" 2>/dev/null | /usr/bin/grep -c .; }
closes() { sed -n 's#^api -X PATCH repos/x/y/issues/\([0-9]*\) -f state=closed -f state_reason=completed$#\1#p' "$T/gh.log" 2>/dev/null | sort -n | tr '\n' ' '; }
# mkrun <run> <issue> [file]: the run's folder, its wave branch, and one child commit `work N (Refs #N)` that writes <file>.
mkrun() { local run=$1 n=$2 f=${3:-g-$1} rd="$T/co/runs/$1"
  mkdir -p "$rd"; : > "$rd/record.jsonl"; printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"; echo "$run" > "$T/st/auto/disp-$n"
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run" && echo "$run" > "$f" && $G add "$f" \
    && $G commit -q -m "work $n (Refs #$n)" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null
  (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" wave "x/y#$n" "$run" entered "child entered") >/dev/null; }
# queue <run>: the run waits for the landing lock, as watch.sh marks it (wland-<run>.queued names the run's folder).
queue() { printf '%s\n' "$T/co/runs/$1" > "$T/st/auto/wland-$1.queued"; }
# The tip gate stub logs one line per gate: the batch it names and the tree it measured. BATCHRED=1 fails a batch gate only.
TIP='printf "%s %s\n" "${SG_GATE_BATCH:--}" "$(/usr/bin/git rev-parse HEAD)" >> "$TIPLOG"; [ -z "${BATCHRED:-}" ] || [ -z "${SG_GATE_BATCH:-}" ]'
export TIPLOG=$T/tip.log
landq() { (cd "$T/co" && SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES=$TIP bash "$T/s/wave.sh" land "$1" "$T/co/runs/$1" 2>&1); }
reset() { : > "$T/gh.log"; : > "$TIPLOG"; : > "$T/pushes.log"; }

# a: three runs ready: one pass, one tip gate, one push, and all three waves land.
mkrun run-a 11; mkrun run-b 12; mkrun run-c 13; queue run-b; queue run-c; reset
r=$(landq run-a); rc=$?
[ $rc = 0 ] && ok "a: the batch pass exits 0" || ko "a: exit $rc: '$r'"
[ "$(/usr/bin/grep -c . "$TIPLOG")" = 1 ] && ok "a: one tip gate for three waves" || ko "a: tip gates: '$(cat "$TIPLOG")'"
[ "$(devpushes)" = 1 ] && ok "a: one push to the base" || ko "a: pushes to dev: '$(cat "$T/pushes.log")'"
$O cat-file -e dev:g-run-a 2>/dev/null && $O cat-file -e dev:g-run-b 2>/dev/null && $O cat-file -e dev:g-run-c 2>/dev/null \
  && ok "a: all three waves are in the base" || ko "a: dev lacks a wave: '$r'"
[ "$(cut -d' ' -f2 "$TIPLOG")" = "$($O rev-parse dev)" ] && ok "a: the pushed tip is the tip the gate measured" || ko "a: gate $(cat "$TIPLOG"), dev $($O rev-parse dev)"
case "$(cut -d' ' -f1 "$TIPLOG")" in "3:run-a,run-b,run-c"|"3:run-a,run-c,run-b") ok "a: the gate names the batch size and its runs";; *) ko "a: batch '$(cat "$TIPLOG")'";; esac
[ "$(closes)" = "11 12 13 " ] && ok "a: each issue of the batch closes" || ko "a: closes '$(closes)': '$r'"
[ -e "$T/st/auto/wland-run-b" ] && [ -e "$T/st/auto/wland-run-c" ] && ok "a: each claimed run gets its landed marker" || ko "a: markers: $(ls "$T/st/auto")"
ls "$T/st/auto" | /usr/bin/grep -qE '\.queued$|\.busy$' && ko "a: a queue or busy marker stays: $(ls "$T/st/auto")" || ok "a: no queue or busy marker stays"
$O rev-parse -q --verify wave/run-b >/dev/null && ko "a: the wave branch of run-b stays" || ok "a: the wave branches go"
rm -f "$T/st/auto"/wland-*

# b: a red batch gate falls back to one landing per run; each run's own gate is green, so all land.
mkrun run-d 21; mkrun run-e 22; mkrun run-f 23; queue run-e; queue run-f; reset
r=$(BATCHRED=1 landq run-d); rc=$?
[ $rc = 0 ] && ok "b: the pass exits 0 after the fallback" || ko "b: exit $rc: '$r'"
[ "$(/usr/bin/grep -c '^3:' "$TIPLOG")" = 1 ] && [ "$(/usr/bin/grep -c '^- ' "$TIPLOG")" = 3 ] && ok "b: one batch gate, then one gate per run" || ko "b: tip gates: '$(cat "$TIPLOG")'"
[ "$(devpushes)" = 3 ] && ok "b: each run lands by its own push" || ko "b: pushes to dev: '$(cat "$T/pushes.log")'"
[ "$(closes)" = "21 22 23 " ] && ok "b: each issue closes on its own landing" || ko "b: closes '$(closes)': '$r'"
[ -e "$T/st/auto/wland-run-e" ] && [ -e "$T/st/auto/wland-run-f" ] && ok "b: the claimed runs get their landed markers" || ko "b: markers: $(ls "$T/st/auto")"
rm -f "$T/st/auto"/wland-*

# c: two waves that change the same file conflict on the stack: the fallback lands the first and the second takes its own conflict path.
mkrun run-g 31 same; mkrun run-h 32 same; queue run-h; reset
r=$(landq run-g); rc=$?
[ "$(/usr/bin/grep -c '^2:' "$TIPLOG")" = 0 ] && ok "c: a stack conflict runs no batch gate" || ko "c: tip gates: '$(cat "$TIPLOG")'"
$O cat-file -e dev:same 2>/dev/null && ok "c: one of the two waves lands alone" || ko "c: neither landed: '$r'"
case "$r" in *"WAVE CONFLICT"*) ok "c: the other wave takes the single-wave conflict path";; *) ko "c: no conflict line: '$r'";; esac
rm -f "$T/st/auto"/wland-*

# d: the base moves while the batch gate runs: the push is refused, and the new tip is gated again before the push.
mkrun run-i 41; mkrun run-j 42; queue run-j; reset
MOVE='[ -e "$TIPLOG.moved" ] || { : > "$TIPLOG.moved"; d=$(mktemp -d); /usr/bin/git clone -q "$SG_LAND_REMOTE" "$d" && (cd "$d" && echo o > other && /usr/bin/git add other && /usr/bin/git commit -q -m other && /usr/bin/git push -q origin HEAD:dev); rm -rf "$d"; }; '
r=$( (cd "$T/co" && SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES="$MOVE$TIP" bash "$T/s/wave.sh" land run-i "$T/co/runs/run-i" 2>&1) ); rc=$?
[ $rc = 0 ] && ok "d: the batch lands after the base moved" || ko "d: exit $rc: '$r'"
[ "$(/usr/bin/grep -c '^2:' "$TIPLOG")" = 2 ] && ok "d: the moved tip is gated again" || ko "d: tip gates: '$(cat "$TIPLOG")'"
[ "$(tail -1 "$TIPLOG" | cut -d' ' -f2)" = "$($O rev-parse dev)" ] && ok "d: the pushed tip is the tip of the second gate" || ko "d: gate $(cat "$TIPLOG"), dev $($O rev-parse dev)"
$O cat-file -e dev:other 2>/dev/null && $O cat-file -e dev:g-run-i 2>/dev/null && $O cat-file -e dev:g-run-j 2>/dev/null && ok "d: both waves land on the moved base" || ko "d: dev lacks a commit: '$r'"
rm -f "$T/st/auto"/wland-*

# e: SG_LAND_BATCH=1 is the one-run pass: one gate that names no batch, and the queued run waits.
mkrun run-k 51; mkrun run-l 52; queue run-l; reset
r=$( (cd "$T/co" && SG_LAND_BATCH=1 SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES=$TIP bash "$T/s/wave.sh" land run-k "$T/co/runs/run-k" 2>&1) ); rc=$?
[ $rc = 0 ] && [ "$(cat "$TIPLOG" | cut -d' ' -f1)" = - ] && ok "e: one gate with no batch" || ko "e: exit $rc, gates '$(cat "$TIPLOG")': '$r'"
$O cat-file -e dev:g-run-l 2>/dev/null && ko "e: the queued run landed with SG_LAND_BATCH=1" || ok "e: the queued run waits"
[ -e "$T/st/auto/wland-run-l.queued" ] && ok "e: its queue marker stays" || ko "e: the queue marker went"

# f: with one run ready, the pass is the one-run pass: no batch is named.
rm -f "$T/st/auto"/wland-*; mkrun run-m 61; reset
r=$(landq run-m); rc=$?
[ $rc = 0 ] && [ "$(cut -d' ' -f1 "$TIPLOG")" = - ] && ok "f: one run ready lands with the one-run pass" || ko "f: exit $rc, gates '$(cat "$TIPLOG")': '$r'"

# g: the gate history row of a batch gate names its size and its runs in the run id field. The field is read by its place,
# the 24th, because the slot clocks follow it (#2317).
(cd "$T/co" && SG_STATE=$T/st SG_GATE_BATCH=3:run-a,run-b,run-c SG_GATE_KIND=fast SG_GATE_CALLER=tip bash "$T/s/gate.sh" true) >/dev/null 2>&1
[ "$(tail -1 "$T/st/gate-history" 2>/dev/null | awk -F'\t' '{ print $24 }')" = "batch:3:run-a,run-b,run-c" ] && ok "g: the history row names the batch" || ko "g: row '$(tail -1 "$T/st/gate-history" 2>/dev/null)'"
(cd "$T/co" && SG_STATE=$T/st SG_GATE_KIND=fast bash "$T/s/gate.sh" true) >/dev/null 2>&1
[ "$(tail -1 "$T/st/gate-history" 2>/dev/null | awk -F'\t' '{ print $24 }')" = - ] && ok "g: a gate with no batch keeps the empty run id" || ko "g: row '$(tail -1 "$T/st/gate-history" 2>/dev/null)'"

# h (#2068): a queue marker that is empty, or a run whose wave is held red (#1961), is never claimed into a batch.
rm -f "$T/st/auto"/wland-*; mkrun run-n 71; mkrun run-o 72; mkrun run-p 73; : > "$T/st/auto/wland-run-o.queued"; queue run-p
printf 'sha\n' > "$T/st/auto/tipred-wave_run-p.red"; reset
r=$(landq run-n); rc=$?
[ $rc = 0 ] && [ "$(cut -d' ' -f1 "$TIPLOG")" = - ] && ok "h: no batch forms from an empty marker or a held-red wave" || ko "h: exit $rc, gates '$(cat "$TIPLOG")': '$r'"
[ "$(closes)" = "71 " ] && ok "h: only the leading run's issue closes" || ko "h: closes '$(closes)': '$r'"
[ -e "$T/st/auto/wland-run-o.queued" ] && ko "h: the empty marker stays" || ok "h: the empty marker goes"
[ -e "$T/st/auto/wland-run-p.queued" ] && ok "h: the held-red run keeps its marker" || ko "h: the held-red run's marker went"
rm -f "$T/st/auto/tipred-wave_run-p.red"

# mkempty <run>: a run whose wave carries nothing ahead of the base.
mkempty() { local run=$1 rd="$T/co/runs/$1"
  mkdir -p "$rd"; : > "$rd/record.jsonl"
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null; }

# i (#2075): an empty wave leaves the batch and lands empty; the two waves with commits stay in one batch behind one tip gate.
rm -f "$T/st/auto"/wland-*; mkempty run-q; mkrun run-r 81; mkrun run-s 82; queue run-r; queue run-s; reset
r=$(landq run-q); rc=$?
[ $rc = 0 ] && ok "i: the pass exits 0" || ko "i: exit $rc: '$r'"
[ "$(/usr/bin/grep -c . "$TIPLOG")" = 1 ] && case "$(cut -d' ' -f1 "$TIPLOG")" in "2:run-r,run-s"|"2:run-s,run-r") ok "i: one tip gate for the two waves with commits";; *) false;; esac || ko "i: tip gates: '$(cat "$TIPLOG")': '$r'"
[ "$(devpushes)" = 1 ] && ok "i: one push to the base" || ko "i: pushes to dev: '$(cat "$T/pushes.log")'"
$O cat-file -e dev:g-run-r 2>/dev/null && $O cat-file -e dev:g-run-s 2>/dev/null && ok "i: both waves with commits are in the base" || ko "i: dev lacks a wave: '$r'"
[ "$(closes)" = "81 82 " ] && ok "i: each issue of the two waves closes" || ko "i: closes '$(closes)': '$r'"
$O rev-parse -q --verify wave/run-q >/dev/null && ko "i: the empty wave of run-q stays" || ok "i: the empty wave lands empty and its branch goes"
[ -e "$T/st/auto/wland-run-r" ] && [ -e "$T/st/auto/wland-run-s" ] && ok "i: each claimed run gets its landed marker" || ko "i: markers: $(ls "$T/st/auto")"
rm -f "$T/st/auto"/wland-*

# j (#2075): an empty wave and one wave with commits: the wave with commits lands alone, behind its own gate.
mkempty run-t; mkrun run-u 91; queue run-u; reset
r=$(landq run-t); rc=$?
[ $rc = 0 ] && ok "j: the pass exits 0" || ko "j: exit $rc: '$r'"
[ "$(/usr/bin/grep -c . "$TIPLOG")" = 1 ] && [ "$(cut -d' ' -f1 "$TIPLOG")" = - ] && ok "j: the wave with commits gets its own gate, no batch" || ko "j: gates '$(cat "$TIPLOG")': '$r'"
$O cat-file -e dev:g-run-u 2>/dev/null && ok "j: the wave with commits is in the base" || ko "j: dev lacks run-u: '$r'"
[ "$(closes)" = "91 " ] && ok "j: its issue closes" || ko "j: closes '$(closes)': '$r'"
rm -f "$T/st/auto"/wland-*

# k (#2288): a lead past its age cap leaves the batch and takes its own path; the two fresh waves land behind one tip gate.
mkrun run-v 101; mkrun run-w 102; mkrun run-x 103; queue run-w; queue run-x; reset
echo $(( $(date +%s) - 100000 )) > "$T/st/auto/wave-start-run-v"
r=$(landq run-v); rc=$?
case "$r" in *"run-v is past its age cap, so it leaves the batch"*) ok "k: the expired lead leaves the batch";; *) ko "k: no leave line: '$r'";; esac
/usr/bin/grep -qE '^2:run-(w,run-x|x,run-w) ' "$TIPLOG" && ok "k: the two fresh waves share one tip gate" || ko "k: tip gates: '$(cat "$TIPLOG")': '$r'"
$O cat-file -e dev:g-run-w 2>/dev/null && $O cat-file -e dev:g-run-x 2>/dev/null && ok "k: both fresh waves are in the base" || ko "k: dev lacks a fresh wave: '$r'"
[ -e "$T/st/auto/wland-run-w" ] && [ -e "$T/st/auto/wland-run-x" ] && ok "k: each fresh run gets its landed marker" || ko "k: markers: $(ls "$T/st/auto")"
rm -f "$T/st/auto"/wland-*

# l (#2288): a red base makes the batch wait before it stacks a wave: no gate runs and no wave lands.
mkrun run-y 111; mkrun run-z 112; queue run-z; reset
printf 'red2 %s fast-battery ended failure\n' "$(date +%s)" > "$T/st/auto/basecheck-$($O rev-parse dev)"
r=$(landq run-y); rc=$?
[ $rc != 0 ] && ok "l: the pass waits on a red base" || ko "l: exit $rc: '$r'"
[ ! -s "$TIPLOG" ] && ok "l: no gate runs on a red base" || ko "l: tip gates: '$(cat "$TIPLOG")': '$r'"
[ "$(devpushes)" = 0 ] && ok "l: no wave lands on a red base" || ko "l: pushes: '$(cat "$T/pushes.log")'"
case "$r" in *"WAVE WAIT batch run-y,run-z"*) ok "l: one wait line names the batch";; *) ko "l: no wait line: '$r'";; esac
rm -f "$T/st/auto"/basecheck-* "$T/st/auto"/wland-*

# m (#2295, ADR-0170): the batch gate moves the card of every wave in the batch to Testing before it starts, and the landing
# moves each card from Testing to Closed. The tip stub writes GATE into the board log, so the log shows the order.
printf '#!/bin/bash\necho GATE >> "%s/board.log"\nexit "$(cat "%s/m-rc")"\n' "$T" "$T" > "$T/m-tip.sh"; echo 0 > "$T/m-rc"
mgate() { (cd "$T/co" && SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES="bash '$T/m-tip.sh'" SG_WAVE_CHILD_GATES=true bash "$T/s/wave.sh" land "$1" "$T/co/runs/$1" 2>&1); }
mkrun run-ma 121; mkrun run-mb 122; queue run-mb; reset; : > "$T/board.log"
r=$(mgate run-ma); rc=$?
[ $rc = 0 ] && [ "$(devpushes)" = 1 ] && ok "m: the batch lands by one push" || ko "m: exit $rc: '$r'"
[ "$(sed -n 1,2p "$T/board.log" | sort)" = "$(printf 'o 3 Testing 121\no 3 Testing 122')" ] && [ "$(sed -n 3p "$T/board.log")" = GATE ] \
  && ok "m: each card of the batch moves to Testing before the gate starts" || ko "m: board log '$(cat "$T/board.log")': '$r'"
[ "$(sed -n '4,$p' "$T/board.log" | sort)" = "$(printf 'o 3 Closed 121\no 3 Closed 122')" ] && ok "m: the landing moves each card from Testing to Closed" || ko "m: board log '$(cat "$T/board.log")': '$r'"
rm -f "$T/st/auto"/wland-*

# n (#2295): a red batch gate, and a red gate for each run alone, write no move after Testing.
mkrun run-mc 131; mkrun run-md 132; queue run-md; reset; : > "$T/board.log"; echo 1 > "$T/m-rc"
r=$(mgate run-mc); rc=$?
[ "$(devpushes)" = 0 ] && ok "n: no wave lands on a red gate" || ko "n: pushes: '$(cat "$T/pushes.log")': '$r'"
[ "$(/usr/bin/grep -v '^GATE$' "$T/board.log" | sort -u)" = "$(printf 'o 3 Testing 131\no 3 Testing 132')" ] && ok "n: a red attempt writes no move after Testing" || ko "n: board log '$(cat "$T/board.log")': '$r'"
rm -f "$T/st/auto"/wland-*

# o (#2330): a tip check that took no slot measured nothing, so the batch waits for a later pass. It never reads that skip as a
# failed check, and it never lands each run alone, which runs one battery per run. The skip line is the budget's own: squad.caps
# writes it here, with one of three slots held by a child gate, so a take of the whole count skips (73) and names its holder.
mkdir -p "$T/ogen/docs/squad"; printf 'caps:\n  gates: 3\n' > "$T/ogen/docs/squad/defaults.yaml"
sleep 30 & ohp=$!
ocap() { PYTHONPATH="$SG_SQUAD" SQUAD_CAPS_DIR="$T/ogenleases" python3 -m squad.caps take gates "$@" --pid "$ohp" --target "$T/ogen"; }
ocap child-gate-7 --count 1 >/dev/null 2>&1
OSKIP=$(ocap gate-9 --count 3 --try --priority tip 2>&1); os=$?
[ "$os" = 73 ] && case "$OSKIP" in *"has 2 free slot(s) of 3"*"held by child-gate-7 (lease pid"*) ok "o: the budget writes the skip line";; *) ko "o: skip line: '$OSKIP'";; esac || ko "o: the budget's skip exits $os, not 73: '$OSKIP'"
kill $ohp 2>/dev/null; wait $ohp 2>/dev/null
# ostub <file> <code> <line>: a tip check that logs each call, writes the line to stderr and exits with the code.
ostub() { { echo '#!/bin/bash'; echo "echo call >> '$T/o-calls'"; echo "cat >&2 <<'EOX'"; printf '%s\n' "$3"; echo EOX; echo "exit $2"; } > "$1"; }
ogate() { (cd "$T/co" && SG_LAND_QUEUE=1 SG_WAVE_TIP_GATES="bash '$1'" bash "$T/s/wave.sh" land "$2" "$T/co/runs/$2" 2>&1); }
ostub "$T/o-tip.sh" 73 "$OSKIP"
rm -f "$T/st/auto"/wland-* "$T/o-calls"; mkrun run-ta 141; mkrun run-tb 142; queue run-tb; reset
r=$(ogate "$T/o-tip.sh" run-ta); rc=$?
[ $rc = 1 ] && ok "o: the pass waits (exit 1)" || ko "o: exit $rc: '$r'"
case "$r" in *"WAVE WAIT batch run-ta,run-tb"*"the gate slots are held by child-gate-7"*) ok "o: one wait line names the batch and the holder";; *) ko "o: no wait line: '$r'";; esac
case "$r" in *"each run lands alone"*) ko "o: the batch read the skip as a failed check: '$r'";; *) ok "o: the skip never lands each run alone";; esac
[ "$(devpushes)" = 0 ] && ok "o: no wave lands on the skipped check" || ko "o: pushes to dev: '$(cat "$T/pushes.log")'"
[ "$(/usr/bin/grep -c . "$T/o-calls" 2>/dev/null)" = 1 ] && ok "o: no gate runs for each run alone" || ko "o: tip calls: $(/usr/bin/grep -c . "$T/o-calls" 2>/dev/null)"
[ -e "$T/st/auto/wland-run-tb.queued" ] && ok "o: the queued run keeps its place in the queue" || ko "o: the queue lost run-tb: $(ls "$T/st/auto")"
$O rev-parse -q --verify wave/run-tb >/dev/null && ok "o: the queued wave keeps its branch" || ko "o: the queued run's branch went"
# o2 (#2330): a tip check whose slot wait ran out answers 75 with the budget's line. The batch waits, as it does on a skip.
ostub "$T/o2-tip.sh" 75 "caps: gate-9 waited 2s for caps.gates, held by child-gate-7 (lease pid 1, live)"
rm -f "$T/st/auto"/wland-* "$T/o-calls"; mkrun run-te 145; mkrun run-tf 146; queue run-tf; reset
r=$(ogate "$T/o2-tip.sh" run-te); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE WAIT batch run-te,run-tf"*) ok "o2: a waited-out tip check makes the batch wait";; *) ko "o2: no wait line: '$r'";; esac || ko "o2: exit $rc: '$r'"
[ "$(devpushes)" = 0 ] && [ "$(/usr/bin/grep -c . "$T/o-calls" 2>/dev/null)" = 1 ] && ok "o2: no push and no gate for each run alone" || ko "o2: pushes '$(cat "$T/pushes.log" 2>/dev/null)', calls $(/usr/bin/grep -c . "$T/o-calls" 2>/dev/null)"
# o3 (#2330): a 73 that names no gate hold is not a skip the batch can wait out: a gate that answers 73 for a real failure would
# wait for ever. The batch takes the check's own failure path and lands each run alone, as a red does.
ostub "$T/o3-tip.sh" 73 "gate: something went wrong that names no hold"
rm -f "$T/st/auto"/wland-*; mkrun run-tc 143; mkrun run-td 144; queue run-td; reset
r=$(ogate "$T/o3-tip.sh" run-tc); rc=$?
case "$r" in *"WAVE WAIT batch"*) ko "o3: the batch waited on a 73 that names no hold: '$r'";; *) ok "o3: the batch never waits on a 73 that names no hold";; esac
case "$r" in *"WAVE BATCH: the tip's check of batch run-tc,run-td is red, so each run lands alone"*) ok "o3: the unnamed 73 takes the red path";; *) ko "o3: exit $rc: '$r'";; esac
rm -f "$T/st/auto"/wland-*

# p (#2330): a red batch gate sends each run to land alone. A claimed run that landed alone never goes back to the queue, even
# when the lead run's own landing then waits on a held gate lock: the queue is restored only when the batch itself waited.
printf '#!/bin/bash\n[ -z "${SG_GATE_BATCH:-}" ] || exit 1\n[ ! -e g-run-ua ] || { echo "serial: lock gate held by pid 1: skipped, not queued (SERIAL_TRY)"; exit 73; }\nexit 0\n' > "$T/p-tip.sh"
rm -f "$T/st/auto"/wland-*; mkrun run-ua 151; mkrun run-ub 152; queue run-ub; reset
r=$(ogate "$T/p-tip.sh" run-ua); rc=$?
case "$r" in *"WAVE BATCH: the tip's check of batch run-ua,run-ub is red, so each run lands alone"*) ok "p: the red batch lands each run alone";; *) ko "p: no fallback line: exit $rc: '$r'";; esac
$O cat-file -e dev:g-run-ub 2>/dev/null && ok "p: the claimed run lands alone" || ko "p: run-ub did not land: exit $rc: '$r'"
[ -e "$T/st/auto/wland-run-ub.queued" ] && ko "p: a run that landed alone went back to the queue (lead exit $rc)" || ok "p: a run that landed alone stays out of the queue"
[ $rc = 1 ] && ok "p: the lead run's own landing waits (exit 1)" || ko "p: the lead run's landing exit $rc, want 1: '$r'"
rm -f "$T/st/auto"/wland-*

[ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
