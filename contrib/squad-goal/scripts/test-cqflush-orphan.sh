#!/bin/bash
# seconds: 3.18
# The comment flusher exits by itself once its supervisor is gone (#959): an orphaned flusher posts nothing and stays no longer
# than one interval, so the loop never keeps a second flusher beside the one a new supervisor starts.
# Offline: the real cqflush.sh, an empty queue and an empty profile directory, so no network is reached.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; lp=
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
mkdir -p "$T/profiles" "$T/state"
export SG_PROFILES_DIR=$T/profiles SG_STATE_BASE=$T/state SG_FLUSH_INTERVAL=1
trap '[ -n "$lp" ] && kill -9 "$lp" 2>/dev/null; rm -rf "$T"' EXIT
waitfor() { for _ in $(seq 1 60); do eval "$1" && return 0; sleep 0.2; done; return 1; }
ppid_of() { ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }

bash "$d/cqflush.sh" >/dev/null 2>&1 & sup=$!
sleep 2; kill -0 "$sup" 2>/dev/null && ok "a flusher under a live supervisor keeps running" || ko "a flusher under a live supervisor exited"
kill -TERM "$sup" 2>/dev/null; wait "$sup" 2>/dev/null

lp=$(bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$d/cqflush.sh")
waitfor '[ "$(ppid_of "$lp")" = 1 ]' && ok "the flusher is orphaned" || ko "the flusher was not orphaned"
for _ in $(seq 1 60); do kill -0 "$lp" 2>/dev/null || break; sleep 0.2; done
kill -0 "$lp" 2>/dev/null && ko "the orphaned flusher still runs" || ok "an orphaned flusher exits on its own"

[ $fail = 0 ] && echo "test-cqflush-orphan: all checks green" || { echo "test-cqflush-orphan: failed"; exit 1; }
