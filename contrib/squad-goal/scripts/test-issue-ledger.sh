#!/bin/bash
# One ledger file per issue (#673): issue_ledger.py is the one writer of $SG_STATE/state/<owner>__<repo>__<n>.json, under a
# lock per issue. Two writers at once each get their own count and no write is lost; a missing file reads as todo.
# usage: bash test-issue-ledger.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st SG_REPO=x/y; L() { python3 "$d/issue_ledger.py" "$@"; }
[ "$(L read 'x/y#5' state)" = todo ] && [ "$(L count 'x/y#5')" = 0 ] && ok "a missing file reads as todo with 0 attempts" || ko "missing: '$(L read 'x/y#5')'"
# Two writers at once, 15 runs each, on one issue: 30 counts, 30 runs, and each call saw a count of its own.
for w in a b; do ( for i in $(seq 1 15); do L fail 'x/y#7' "$w$i" "" "writer $w" | cut -d' ' -f1; done > "$T/$w.out" ) & done; wait
[ "$(L count 'x/y#7')" = 30 ] && ok "two writers at once lose no write" || ko "count after 30 writes: '$(L count 'x/y#7')'"
[ "$(cat "$T/a.out" "$T/b.out" | sort -n | uniq | wc -l | tr -d ' ')" = 30 ] && ok "each write got a count of its own" || ko "counts seen: $(cat "$T/a.out" "$T/b.out" | sort -n | tr '\n' ' ')"
[ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["own"]["runs"]))' "$T/st/state/x__y__7.json")" = 30 ] \
  && ok "the file lists every run once" || ko "runs: $(cat "$T/st/state/x__y__7.json")"
[ -z "$(/bin/ls "$T/st/state" | /usr/bin/grep -v '\.json$' | /usr/bin/grep -v '\.lock$')" ] && ok "no temp file stays" || ko "left: $(/bin/ls "$T/st/state")"
L fail 'x/y#7' r1 "" x >/dev/null; [ ! -e "$T/st/auto" ] && ok "the old requeue files are not written" || ko "auto: $(/bin/ls "$T/st/auto")"
rm -rf "$T"; exit $fail
