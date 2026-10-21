#!/bin/bash
# usage: nightly.sh   The full suite in both modes, heavy included, once a day (#704), in its own tree and its own lock.
# It runs `-n auto` and then `-p no:xdist` with `-p no:deepeval` over the whole suite -- no `-m 'not heavy'`, so the calls the
# fast battery deselects run here -- in a plain clone at $SG_STATE/nightly-tree, never in the owner's checkout and never in a
# child's worktree, so no suite it runs can touch the line the loop is working on.
# Its lock is the serial name `nightly` (serial.sh), in try mode: a second start never queues behind a suite of hours, and it
# never takes the `gate` lock, which the landing gate, the wave entry and the promotion all wait on. A held landing lock is no
# obstacle here, and a nightly in flight never holds a landing up.
# It runs at most once a day: a completed run leaves $SG_STATE/auto/nightly-<date>, and a later start the same day exits at
# once. The marker is written after the run, so a suite that dies leaves no marker and the next cycle tries again.
# One result record, written atomically (ADR-0030), is what the hub reads:
#   $SG_STATE/nightly-result: `<epoch> TAB <pass|fail> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>`
# The result is a pass only when both modes pass. `<ids>` is ONE JSON array of the complete failing test ids, `[]` when
# none; a mode that failed names at least the tests it failed, and the hub draws them. An id is never split at a space,
# so a parametrized case id such as `test_x[case with space]` reaches the hub whole. A red record holds dispatch
# (ghcall.sh, gh_dispatch_hold) until one green run replaces it.
# A red result raises one alert on the path health.sh already uses: one line in $SG_STATE/alerts.log and one notification
# (HEALTH_NOTIFY overrides the notifier). The two mode logs stay in $SG_STATE/nightly-parallel.log and nightly-serial.log.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
mkdir -p "$st/auto" "$st/locks"
day=$st/auto/nightly-$(date +%F)
[ -e "$day" ] && exit 0   # it already ran today: no second suite, whatever started this one
if [ "${1:-}" != --run ]; then   # the lock is taken once, around the suite, and never around the day check
  SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-10800} \
    exec bash "$here/serial.sh" nightly bash "$0" --run
fi
tree=${SG_NIGHTLY_TREE:-$st/nightly-tree}; remote=${SG_NIGHTLY_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || { echo "nightly: fetch of $base failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse "origin/$base") || exit 1
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { echo "nightly: the tree could not check out $tip" >&2; exit 1; }
py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
# The two modes are one command each, so a caller may point them elsewhere: the whole suite, heavy included, in both.
par=${SG_NIGHTLY_PARALLEL:-"$py -m pytest -p no:deepeval -n auto"}
ser=${SG_NIGHTLY_SERIAL:-"$py -m pytest -p no:deepeval -p no:xdist"}
# A failing id is whatever pytest's short summary names after `FAILED ` or `ERROR `: a node id, or the file a
# collection error names (`ERROR tests/test_module.py`, no `::`). Both are what the hub shows, so neither is dropped.
# pytest writes ` - ` and the failure's message after the id, so the id is the line up to the FIRST ` - `; the rest is
# the message and is never part of the id. A node id may itself carry a space (a parametrized case id such as
# `test_x[case with space]`), so the ids are never split at whitespace: the record carries them as ONE JSON array on its
# one line, and both readers (hub.py, ghcall.sh) parse that array and never a word of it.
ids_of() { sed -n -e 's/^FAILED //p' -e 's/^ERROR //p' "$1" | sed -e 's/ - .*//'; }
pout=$st/nightly-parallel.log; sout=$st/nightly-serial.log
pm=pass; ( cd "$tree" && bash -c "$par" ) > "$pout" 2>&1 || pm=fail
sm=pass; ( cd "$tree" && bash -c "$ser" ) > "$sout" 2>&1 || sm=fail   # both modes always run: the record says which one broke
failed=$st/nightly-failing.$$   # one complete id per line, so no id is ever split
{ ids_of "$pout"; ids_of "$sout"; } | sort -u > "$failed"
nfail=$(awk 'END{print NR}' "$failed")
ids=$("$py" -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().split("\n") if l], separators=(",", ":")))' < "$failed") \
  || { rm -f "$failed"; echo "nightly: the failing ids could not be encoded" >&2; exit 1; }
rm -f "$failed"
res=pass; { [ "$pm" = pass ] && [ "$sm" = pass ]; } || res=fail
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$res" "$tip" "$pm" "$sm" "$ids" > "$st/nightly-result.$$" \
  && mv -f "$st/nightly-result.$$" "$st/nightly-result" || { echo "nightly: the result record could not be written" >&2; exit 1; }
if [ "$res" = fail ]; then
  if [ "$nfail" -gt 0 ]; then msg="NIGHTLY full suite red at ${tip:0:7}: $nfail failing test(s)"
  else msg="NIGHTLY full suite red at ${tip:0:7}: no failing test named"; fi
  printf '%s %s\n' "$(date '+%F %T')" "$msg" >> "$st/alerts.log"
  notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
  "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1
fi
: > "$day"   # the run is spent for today, green or red
[ "$res" = pass ] && exit 0
exit 1
