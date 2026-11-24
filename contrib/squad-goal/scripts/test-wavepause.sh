#!/bin/bash
# seconds: 0.65
# Regression test for the pause of a wave's age cap (#637): `wave.sh expired <run>` does not count the time a child of the
# wave spends in an in-run review round or fix entry (the run's record), or in a childcc fix run (auto/wave-pause-<run>).
# usage: bash test-wavepause.sh [scripts dir]
# Hermetic: a stub env.sh, a temp state folder and checkout, records written with known timestamps. No git, no network.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/co/runs"
cp "$d/wave.sh" "$d/columnlimit.sh" "$d/wavepause.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/child_issues.py" "$d/basecheck.sh" "$T/s/" 2>/dev/null
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_WAVE_MAX_AGE_SECONDS=1000\n' "$T/st" "$T/co" > "$T/s/env.sh"
now=$(date +%s)
# wave <run> <age>: the run cut its wave <age> seconds ago; its record holds the events given on stdin, `@-N` read as now-N.
wave() { echo "wave/$1" > "$T/st/auto/wave-$1"; echo $((now - $2)) > "$T/st/auto/wave-start-$1"; mkdir -p "$T/co/runs/$1"
  rec > "$T/co/runs/$1/record.jsonl"; }
rec() { perl -pe 's/\@-(\d+)/'"$now"'-$1/ge'; }
expired() { bash "$T/s/wave.sh" expired "$1" >/dev/null 2>&1; }

# A: a wave past its cap with no review in its record is expired: the control case.
wave run-a 1500 <<'EOF'
{"event": "run_start", "ts": @-1500}
EOF
expired run-a && ok "A: a wave past its cap with no pause is expired" || ko "A: a wave past its cap is not expired"

# B: a finished review round of 700 s pauses the cap: 1500 - 700 = 800 s of age, under the 1000 s cap.
wave run-b 1500 <<'EOF'
{"event": "review_round", "node": "c1", "round": 1, "verdict": "approve", "review": {"elapsed": 700}, "ts": @-600}
EOF
expired run-b && ko "B: a finished review round spent the age cap" || ok "B: a finished review round does not spend the age cap"

# C: a review round still in flight (a progress tick, no round yet) pauses the cap until now.
wave run-c 1500 <<'EOF'
{"event": "review_progress", "node": "c1", "gate": "review", "elapsed": 600, "ts": @-10}
EOF
expired run-c && ko "C: a review in flight spent the age cap" || ok "C: a review in flight does not spend the age cap"

# D: a fix entry (entry 2 after a `fix` verdict on round 1) pauses the cap from its call start to its outcome.
wave run-d 1500 <<'EOF'
{"event": "review_round", "node": "c1", "round": 1, "verdict": "fix", "review": {"elapsed": 0}, "ts": @-1400}
{"event": "call_start", "node": "c1", "entry": 2, "ts": @-1300}
{"event": "outcome", "node": "c1", "entry": 2, "outcome": "pass", "ts": @-700}
EOF
expired run-d && ko "D: a fix entry spent the age cap" || ok "D: a fix entry does not spend the age cap"

# E: the pause between two tries of one round is part of the round: [now-1300, now-800] is 500 s, the tries alone 200 s.
wave run-e 1400 <<'EOF'
{"event": "review_try", "node": "c1", "round": 1, "try": 1, "review": {"elapsed": 100}, "ts": @-1200}
{"event": "review_round", "node": "c1", "round": 1, "verdict": "approve", "review": {"elapsed": 100}, "ts": @-800}
EOF
expired run-e && ko "E: the pause between two tries spent the age cap" || ok "E: a round with tries pauses the cap from its first try to its verdict"

# F: two children in review at the same time pause the wave once: 1600 - 500 = 1100 s, past the cap.
wave run-f 1600 <<'EOF'
{"event": "review_round", "node": "c1", "round": 1, "verdict": "approve", "review": {"elapsed": 500}, "ts": @-500}
{"event": "review_round", "node": "c2", "round": 1, "verdict": "approve", "review": {"elapsed": 500}, "ts": @-500}
EOF
expired run-f && ok "F: overlapping reviews of two children pause the wave once" || ko "F: overlapping reviews were counted twice"

# G: a childcc fix run the pause marker names pauses the cap from its start to its verdict.
wave run-g 1500 <<'EOF'
{"event": "run_start", "ts": @-1500}
EOF
mkdir -p "$T/co/runs/fix-g"; rec > "$T/co/runs/fix-g/record.jsonl" <<'EOF'
{"event": "run_start", "ts": @-900}
{"event": "verdict", "verdict": "pass", "ts": @-300}
EOF
echo "$T/co/runs/fix-g" > "$T/st/auto/wave-pause-run-g"
expired run-g && ko "G: a finished childcc fix run spent the age cap" || ok "G: a finished childcc fix run does not spend the age cap"

# H: a childcc fix run with no verdict yet, whose record still moves, pauses the cap until now.
wave run-h 1500 <<'EOF'
{"event": "run_start", "ts": @-1500}
EOF
mkdir -p "$T/co/runs/fix-h"; rec > "$T/co/runs/fix-h/record.jsonl" <<'EOF'
{"event": "run_start", "ts": @-700}
{"event": "progress", "node": "x", "entry": 1, "ts": @-20}
EOF
echo "$T/co/runs/fix-h" > "$T/st/auto/wave-pause-run-h"
expired run-h && ko "H: a live childcc fix run spent the age cap" || ok "H: a live childcc fix run does not spend the age cap"

# I: an open review in a record that stopped moving (the run died) pauses only to its last event plus the heartbeat's quiet
# window (300 s): [now-2100, now-1700] is 400 s, so 3000 - 400 is past the cap and the wave is never held for good.
wave run-i 3000 <<'EOF'
{"event": "review_progress", "node": "c1", "gate": "review", "elapsed": 100, "ts": @-2000}
EOF
expired run-i && ok "I: an open review in a dead record pauses only to its last event plus the quiet window" || ko "I: a dead run's open review held the wave"

# J: review time before the wave was cut is no pause of it: [now-2400, now-1400] keeps 100 s inside the wave's age.
wave run-j 1500 <<'EOF'
{"event": "review_round", "node": "c1", "round": 1, "verdict": "approve", "review": {"elapsed": 1000}, "ts": @-1400}
EOF
expired run-j && ok "J: a review before the wave's start is no pause of it" || ko "J: time before the wave's start paused it"

# K: a wave under its cap is never expired, and a run with no wave marker is never expired.
wave run-k 10 <<'EOF'
{"event": "run_start", "ts": @-10}
EOF
expired run-k && ko "K: a young wave is expired" || ok "K: a young wave is not expired"
expired run-none && ko "K: a run with no wave is expired" || ok "K: a run with no wave is not expired"

# L: a wait for a gate slot is no wave time (#2231, ADR-0166): one gate row [now-700, now-400] and one suite row
# [now-350, now-50] pause 600 s, so 1500 - 600 is under the 1000 s cap.
wave run-l 1500 <<'EOF'
{"event": "run_start", "ts": @-1500}
EOF
awk -v s=$((now - 400)) 'BEGIN{f[5]=s; f[18]="300"; for(i=1;i<=23;i++) printf "%s%s", (i>1?"\t":""), (f[i]==""?"-":f[i]); print ""}' > "$T/st/gate-history"
printf '%s\t%s\t300\t1\t1\t0\ttest-x.sh\t-\n' "$((now - 50))" "$((now - 50))" > "$T/st/child-suite-history"
expired run-l && ko "L: a gate wait spent the age cap" || ok "L: a gate and a suite wait do not spend the age cap"

# M (#2840): the ceiling file the engine reads carries the same pause this check subtracts, so the engine's bound and the
# wave's expiry rest on one epoch. `expired` writes it, and run-b's finished 700 s round pauses run-b's ceiling too.
[ "$(cat "$T/st/auto/wave-ceiling-run-b" 2>/dev/null)" = "$(( now - 1500 + 1000 + 700 ))" ] && ok "M: the ceiling file carries the cap and the pause" || ko "M: the ceiling of a paused wave is '$(cat "$T/st/auto/wave-ceiling-run-b" 2>&1)', not the cap and the pause"

rm -rf "$T"; [ $fail = 0 ] && echo "test-wavepause: all checks green" || { echo "test-wavepause: failed"; exit 1; }
