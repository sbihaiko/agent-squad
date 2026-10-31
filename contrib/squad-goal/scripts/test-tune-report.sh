#!/bin/bash
# Regression test for tune-report.sh: each column of the tuning table, read from a fake state directory (throw-away, no network).
# usage: bash test-tune-report.sh
# The expected values are worked out by hand from the fixtures below, never by the reader's own arithmetic.
TR="$(cd "$(dirname "$0")" && pwd)/tune-report.sh"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$(python3 -c 'import calendar; print(calendar.timegm((2026, 10, 10, 0, 0, 0)))')   # the window opens here (UTC)
hms() { python3 -c 'import datetime, sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%F %T"))' "$1"; }
A=$(python3 -c 'print("a" * 64)'); B=$(python3 -c 'print("b" * 64)'); C=$(python3 -c 'print("c" * 64)')
mkdir -p "$T/st" "$T/co/runs/run-20261010-010000-aaaa" "$T/co/runs/run-20261010-120000-bbbb" "$T/co/runs/run-20261009-010000-cccc" "$T/bin"
export SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_CORES=8
# a gh stub that logs every call: the reader must make none
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nexit 1\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"

# runs: run 1 lives in the window (2 h), run 2 lives in it too (30 min, no verdict), run 3 ended before the window
cat > "$T/co/runs/run-20261010-010000-aaaa/record.jsonl" <<EOF
{"event": "run_start", "ts": $S, "caps": {"runs": 6, "gates": 2}, "project_settings": {"path": "/p", "sha256": "$A"}}
{"event": "call_start", "ts": $((S + 10))}
{"event": "verdict", "ts": $((S + 7200)), "verdict": "completed"}
EOF
cat > "$T/co/runs/run-20261010-120000-bbbb/record.jsonl" <<EOF
{"event": "run_start", "ts": $((S + 36000)), "caps": {"runs": 4, "gates": 2}, "project_settings": {"path": "/p", "sha256": "$B"}}
{"event": "progress", "ts": $((S + 37800))}
EOF
cat > "$T/co/runs/run-20261009-010000-cccc/record.jsonl" <<EOF
{"event": "run_start", "ts": $((S - 86400)), "caps": {"runs": 9}, "project_settings": {"path": "/p", "sha256": "$C"}}
{"event": "verdict", "ts": $((S - 80000)), "verdict": "completed"}
EOF

# landings: landed.tsv rows, epoch TAB branch TAB sha TAB issue; one row before the window, one malformed
printf '%s\tfix/1-a\tsha1\t1\n%s\tfix/2-b\tsha2\t2\n%s\tfix/0-old\tsha0\t0\n%s\tfix/9-short\n' \
  "$((S + 100))" "$((S + 200))" "$((S - 500))" "$((S + 300))" > "$T/st/landed.tsv"

# dispatch passes: timed FRONTIER idle lines; one untimed (written before the timestamp) and one outside the window
{
  echo "$(hms $((S + 1000))) FRONTIER idle: 3 todo, 0 startable (0 no go label, 0 held, 0 sibling, 2 no slot, 1 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)"
  echo "$(hms $((S + 2000))) FRONTIER idle: 1 todo, 0 startable (0 no go label, 0 held, 0 sibling, 0 no slot, 1 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)"
  echo "$(hms $((S + 3000))) FRONTIER idle: 2 todo, 0 startable (0 no go label, 0 held, 0 sibling, 1 no slot, 1 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)"
  echo "FRONTIER idle: 1 todo, 0 startable (0 no go label, 0 held, 0 sibling, 1 no slot, 0 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)"
  echo "$(hms $((S - 300))) FRONTIER idle: 1 todo, 0 startable (0 no go label, 0 held, 0 sibling, 1 no slot, 0 blocked, 0 claimed, 0 busy, 0 not startable, 0 skip label, 0 ac paths, 0 not listed)"
  # gate holds: the promotion proof and the nightly red hold the gate; a childcc hold is not the gate; one lies before the window
  echo "$(hms $((S + 4000))) DISPATCH HOLD: the promotion proof holds the machine (since 00:00:00); no new run this pass"
  echo "$(hms $((S + 4100))) DISPATCH HOLD: the nightly full suite is red (test_x); no new run until one green nightly"
  echo "$(hms $((S + 4200))) DISPATCH HOLD: 5 children wait for review (cap 3); no new run this pass"
  echo "$(hms $((S - 100))) DISPATCH HOLD: the promotion proof holds the machine (since 00:00:00); no new run this pass"
} > "$T/st/cycle.log"

# gate history: finish TAB kind TAB result TAB sha TAB start; one row has no finish time, one lies before the window
printf '%s\ttest\tpass\tsha1\t%s\n%s\ttest\tpass\tsha2\t%s\n%s\ttest\tfail\tsha3\t%s\n\ttest\tpass\tsha4\t%s\n%s\ttest\tpass\tsha0\t%s\n' \
  "$((S + 100))" "$((S + 40))" "$((S + 500))" "$((S + 400))" "$((S + 900))" "$((S + 850))" "$((S + 10))" "$((S - 1000))" "$((S - 1100))" > "$T/st/gate-history"

# load samples: UTC time, then the 1, 5 and 15 minute averages; three valid in the window, two malformed, one before it
{
  echo "$(hms $((S + 10))) LOAD 1.0 2.5 3.0"
  echo "$(hms $((S + 70))) LOAD 9.0 9.5 9.1"
  echo "$(hms $((S + 130))) LOAD 0.5 1.0 1.0"
  echo "$(hms $((S + 160))) LOAD x y z"
  echo "not a load sample"
  echo "$(hms $((S - 500))) LOAD 9 9 9"
} > "$T/st/load.log"

# the GitHub cooldown: one 900 s hold that started inside the window
printf '{"until": %s, "at": %s, "base": 900, "jitter": 0, "message": "rate limit", "request_id": "r1", "pid": 1}\n' \
  "$((S + 30900))" "$((S + 30000))" > "$T/st/gh-cooldown.json"

out=$(bash "$TR" 2026-10-10 2026-10-11 2>&1); rc=$?
has() { printf '%s\n' "$out" | grep -qxF -- "$1"; }
[ "$rc" = 0 ] && ok "the window reads and exits 0" || ko "exit $rc: $out"
has "window: 2026-10-10 00:00:00 to 2026-10-11 00:00:00 UTC" && ok "window: names both ends in UTC" || ko "window line: $out"
has "landings: 2" && ok "landings: the two rows in the window; the old and the malformed row are skipped" || ko "landings: $out"
has "live-run hours: 2.50" && ok "live-run hours: 2 h of run 1 and 30 min of run 2" || ko "live-run hours: $out"
has "landings per live-run hour: 0.80" && ok "landings per live-run hour: 2 over 2.5 h" || ko "per hour: $out"
has "dispatch passes: 3" && ok "dispatch passes: the three timed idle lines; the untimed and the old one are skipped" || ko "dispatch passes: $out"
has "passes held for a run slot: 2" && ok "passes held for a run slot: the idle lines with a no-slot card" || ko "held for slot: $out"
has "share held for a run slot: 0.67" && ok "share held for a run slot: 2 of 3 passes" || ko "share held: $out"
has "passes held for the gate: 2" && ok "passes held for the gate: the promotion and nightly holds; a childcc hold is not the gate" || ko "held for gate: $out"
has "gate runs: 3" && ok "gate runs: three finished rows; the row with no finish time is skipped" || ko "gate runs: $out"
has "gate total seconds: 210" && ok "gate total seconds: 60 + 100 + 50" || ko "gate total: $out"
has "gate median seconds: 60" && ok "gate median seconds: the median of 50, 60, 100" || ko "gate median: $out"
has "cooldown seconds: 900" && ok "cooldown seconds: the one hold, read from its own start and end" || ko "cooldown: $out"
has "share of five-minute load samples at or above the core count: 0.33" && ok "load share: one of three valid samples at 8 cores" || ko "load share: $out"
has "caps in force: caps.gates=2 caps.runs=6 | caps.gates=2 caps.runs=4" && ok "caps in force: from the runs that lived in the window, one set per run" || ko "caps: $out"
has "settings hashes seen: aaaaaaaaaaaa bbbbbbbbbbbb" && ok "settings hashes seen: the two hashes of the window's runs" || ko "hashes: $out"
has "FLAG: window spans 2 settings hashes; its numbers are not one configuration" && ok "a window spanning two hashes is flagged in its own line" || ko "flag: $out"
case "$out" in *"caps.runs=9"*|*"cccccccc"*) ko "a run before the window leaks into the caps or hashes: $out";; *) ok "a run before the window leaks into neither the caps nor the hashes";; esac
[ ! -s "$T/gh.log" ] && ok "the reader makes no GitHub call" || ko "a gh call was made: $(cat "$T/gh.log")"

one=$(bash "$TR" 2026-10-10 2026-10-10T01:00:00 2>&1)
printf '%s\n' "$one" | grep -qxF -- "settings hashes seen: aaaaaaaaaaaa" && ! printf '%s\n' "$one" | grep -q "^FLAG" && ok "a window of one settings hash prints no flag" || ko "one-hash window: $one"
printf '%s\n' "$one" | grep -qxF -- "live-run hours: 1.00" && ok "a window cuts a run's hours at its edge" || ko "clipped hours: $one"

empty=$(bash "$TR" 2001-01-01 2001-01-02 2>&1); erc=$?
[ "$erc" = 0 ] && ok "an empty window exits 0" || ko "empty window exit $erc: $empty"
for line in "landings: 0" "live-run hours: 0.00" "landings per live-run hour: 0.00" "dispatch passes: 0" "passes held for a run slot: 0" \
  "share held for a run slot: 0.00" "passes held for the gate: 0" "gate runs: 0" "gate total seconds: 0" "gate median seconds: 0" \
  "cooldown seconds: 0" "share of five-minute load samples at or above the core count: 0.00" "caps in force: none" "settings hashes seen: none"; do
  printf '%s\n' "$empty" | grep -qxF -- "$line" && ok "empty window: $line" || ko "empty window: missing '$line': $empty"
done
case "$empty" in *FLAG*) ko "an empty window is flagged: $empty";; *) ok "an empty window is not flagged";; esac

bare=$(mktemp -d); mkdir -p "$bare/st" "$bare/co"
none=$(SG_STATE="$bare/st" SG_CHECKOUT="$bare/co" bash "$TR" 2026-10-10 2026-10-11 2>&1); nrc=$?
[ "$nrc" = 0 ] && printf '%s\n' "$none" | grep -qxF -- "landings: 0" && ok "a state directory with no files reads as zeros" || ko "no files: rc=$nrc $none"

bad=$(bash "$TR" not-a-date 2>&1); brc=$?
[ "$brc" = 2 ] && ok "a malformed window date exits 2" || ko "bad date: rc=$brc $bad"
rm -rf "$bare" "$T"; exit $fail
