#!/bin/bash
# Regression test for child-gate.sh (#1762): a suite a child starts takes the machine's gates slot and waits when the
# ceiling is full, a second copy of the same suite is refused with a reason, and each run is named with its load.
# usage: bash test-child-gate.sh [scripts dir]
# mirrors: child-gate.sh
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_STATE=$T/state SQUAD_CAPS_POLL=0.2
mkdir -p "$T/proj/docs/squad" "$T/caps/gates" "$T/state"; printf 'caps:\n  gates: 1\n' > "$T/proj/docs/squad/defaults.yaml"   # one gate slot
for s in test-a test-b test-c; do
  printf '#!/bin/bash\necho "%s start $(date +%%s)" >> "%s/log"\nsleep 2\necho "%s end $(date +%%s)" >> "%s/log"\n' "$s" "$T" "$s" "$T" > "$T/proj/$s.sh"
done
cd "$T/proj" || exit 2

# two child suites, bound at one: the second waits for the first
bash "$d/child-gate.sh" ./test-a.sh > "$T/a.out" 2>&1 & pa=$!
sleep 0.5
bash "$d/child-gate.sh" ./test-b.sh > "$T/b.out" 2>&1 & pb=$!
wait "$pa"; ra=$?; wait "$pb"; rb=$?
[ "$ra" = 0 ] && [ "$rb" = 0 ] && ok "both child suites finish green" || ko "exit a=$ra b=$rb: $(cat "$T/a.out" "$T/b.out")"
ta_end=$(/usr/bin/grep '^test-a end' "$T/log" | cut -d' ' -f3); tb_start=$(/usr/bin/grep '^test-b start' "$T/log" | cut -d' ' -f3)
[ -n "$ta_end" ] && [ -n "$tb_start" ] && [ "$tb_start" -ge "$ta_end" ] && ok "the second suite starts after the first one ends" || ko "a ended ${ta_end:-?}, b started ${tb_start:-?}"
hist="$T/state/child-suite-history"
bw=$(/usr/bin/grep -F "$T/proj/test-b.sh" "$hist" 2>/dev/null | cut -f3)
[ "${bw:-0}" -ge 1 ] && ok "the second suite's wait is recorded as wait, not run time ($bw s)" || ko "b wait: ${bw:-none}; history: $(cat "$hist" 2>/dev/null)"
[ "$(/usr/bin/grep -c . "$hist" 2>/dev/null)" = 2 ] && ok "each child suite run has one history line" || ko "history: $(cat "$hist" 2>/dev/null)"
/usr/bin/grep -qE '	[0-9.]+	[0-9.]+	[0-9]+	' "$hist" 2>/dev/null && ok "each history line names its load at start and finish" || ko "no load fields: $(cat "$hist" 2>/dev/null)"

# a second copy of one suite is refused at once, with a reason the child reads
bash "$d/child-gate.sh" ./test-a.sh > "$T/c1.out" 2>&1 & pc=$!
sleep 0.5
bash "$d/child-gate.sh" ./test-a.sh > "$T/c2.out" 2>&1; rc2=$?
wait "$pc"
[ "$rc2" = 73 ] && /usr/bin/grep -q 'already running' "$T/c2.out" && ok "a second copy of one suite is refused with a reason" || ko "second copy rc=$rc2: $(cat "$T/c2.out")"

# a wait past its own bound names the ceiling and runs nothing: no history line, since no suite ran
# The holder's slot is waited for, never assumed from a clock (#2312): its python start is slowed here, so a bare
# sleep reads an empty ceiling as free under load. The wait polls the slot state child-gate.sh takes its slot from,
# through the same module, and its bound fails this case instead of letting an empty ceiling pass as free.
nb0=$(/usr/bin/grep -cF "$T/proj/test-b.sh" "$hist" 2>/dev/null)
mkdir -p "$T/slow"; printf 'import time\ntime.sleep(2)\n' > "$T/slow/sitecustomize.py"
gates_held() {  # the live holders of caps.gates and their lease pids, read as child-gate.sh reads them: the same
  # SQUAD_CAPS_DIR through squad.caps. The probe names the folder the holder inherits, never the default one a bare
  # python would read, so an empty answer means no holder, not a folder the holder never writes.
  SQUAD_CAPS_DIR="$T/caps" PYTHONPATH="$SG_SQUAD" python3 -c \
    'import sys; from squad import caps; print("; ".join(caps.ceiling(sys.argv[1]).holding("gates")))' "$T/proj" 2>/dev/null
}
PYTHONPATH="$T/slow" bash "$d/child-gate.sh" ./test-a.sh > "$T/h.out" 2>&1 & ph=$!
held=; bound=$((SECONDS + 30))
while [ "$SECONDS" -lt "$bound" ]; do
  held=$(gates_held)
  case "$held" in *"child-gate-$ph (lease pid $ph,"*) break;; esac   # the holder's own name and lease pid
  held=; sleep 0.1
done
[ -n "$held" ] && ok "the holder's gates slot is read from the slot state" || ko "the holder took no gates slot within 30 s: $(cat "$T/h.out")"
SG_CHILD_GATE_WAIT=1 bash "$d/child-gate.sh" ./test-b.sh > "$T/w.out" 2>&1; rcw=$?
wait "$ph"
[ "$rcw" = 75 ] && /usr/bin/grep -q 'no caps.gates slot' "$T/w.out" && ok "a wait past its bound names the full ceiling" || ko "wait bound rc=$rcw: $(cat "$T/w.out")"
[ "$(/usr/bin/grep -cF "$T/proj/test-b.sh" "$hist" 2>/dev/null)" = "$nb0" ] && ok "a suite that never ran writes no history line" || ko "history after the expired wait: $(cat "$hist" 2>/dev/null)"

# a suite a command starts under SG_GATE_SLOTS_HELD takes no slot: the full ceiling does not hold it, its row has wait 0 and the tree sha
git -C "$T/proj" init -q && git -C "$T/proj" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
bash "$d/child-gate.sh" ./test-a.sh > "$T/s.out" 2>&1 & psl=$!
sleep 0.5
SG_GATE_SLOTS_HELD=1 bash "$d/child-gate.sh" ./test-c.sh > "$T/s2.out" 2>&1; rcs=$?
wait "$psl"
ca=$(/usr/bin/grep -n '^test-c start' "$T/log" | tail -1 | cut -d: -f1); ae=$(/usr/bin/grep -n '^test-a end' "$T/log" | tail -1 | cut -d: -f1)
[ "$rcs" = 0 ] && [ -n "$ca" ] && [ -n "$ae" ] && [ "$ca" -lt "$ae" ] && ok "a suite under SG_GATE_SLOTS_HELD starts while the ceiling is full" || ko "exit $rcs, c started at line ${ca:-?}, a ended at line ${ae:-?}: $(cat "$T/s2.out")"
hrow=$(/usr/bin/grep -F "$T/proj/test-c.sh" "$hist" 2>/dev/null | tail -1)
[ "$(printf '%s' "$hrow" | cut -f3)" = 0 ] && [ "$(printf '%s' "$hrow" | cut -f8)" = "$(git -C "$T/proj" rev-parse HEAD)" ] && ok "its history row has wait 0 and the tree sha" || ko "row: ${hrow:-none}"

# one suite per call (#2025): a glob of suites gave the first suite the others as its arguments, and a watch suite copied a
# suite as its watch.sh, which then ran itself at every level until the user's process slots ran out
n0=$(/usr/bin/grep -c '^test-a start' "$T/log")
bash "$d/child-gate.sh" ./test-a.sh ./test-b.sh > "$T/g.out" 2>&1; rcg=$?
[ "$rcg" = 2 ] && /usr/bin/grep -q 'one suite per call' "$T/g.out" && [ "$(/usr/bin/grep -c '^test-a start' "$T/log")" = "$n0" ] && ok "a second suite as an argument is refused and nothing runs" || ko "two suites rc=$rcg: $(cat "$T/g.out")"
# the suite runs under a process bound below the user's own, so a runaway suite meets a fork error before the loop does
printf '#!/bin/bash\nulimit -u > "%s/ulimit"\n' "$T" > "$T/proj/test-u.sh"
bash "$d/child-gate.sh" ./test-u.sh > /dev/null 2>&1
uu=$(cat "$T/ulimit" 2>/dev/null); per=$(sysctl -n kern.maxprocperuid 2>/dev/null || ulimit -u)
[ -n "$uu" ] && [ "$uu" != unlimited ] && [ "$uu" -lt "$per" ] && ok "the suite runs under a process bound ($uu) below the user's ($per)" || ko "suite ulimit -u ${uu:-none}, user ${per:-?}"
# a watch suite refuses a source that is not a watch.sh, before it builds a fixture
printf '#!/bin/bash\nexit 0\n' > "$T/other.sh"
for w in test-watch-cycle.sh test-watch-land.sh test-watch-orphan.sh; do
  bash "$d/$w" "$T/other.sh" > "$T/w-$w.out" 2>&1 & pw=$!
  for i in $(seq 1 50); do kill -0 "$pw" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$pw" 2>/dev/null; then pkill -P "$pw" 2>/dev/null; kill "$pw" 2>/dev/null; wait "$pw" 2>/dev/null; ko "$w ran its cases on a source not named watch.sh"
  else wait "$pw"; rw=$?; [ "$rw" = 2 ] && /usr/bin/grep -q 'not a watch.sh' "$T/w-$w.out" && ok "$w refuses a source not named watch.sh" || ko "$w rc=$rw: $(head -3 "$T/w-$w.out")"; fi
done

[ "$fail" = 0 ] && echo "child-suite: all green" || echo "child-suite: FAILED"
exit "$fail"
