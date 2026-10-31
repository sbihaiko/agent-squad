#!/bin/bash
# usage: cap-edit.sh set <key> <value> | cap-edit.sh reset <key>
# The one procedure every cap edit goes through, a revert included. It stops dispatch, waits until no live run is listed in
# active-runs.txt, edits the project settings file, runs the settings check, and starts dispatch again on every path.
# Exit 0 applied; 1 failed, the old value kept; 2 usage; 3 held by a live run, which the held line names.
# A run is live when it is listed and its record carries no verdict (the rule watch.sh reads); an unreadable record is live.
# Each step writes one `CAP EDIT` line. The seams SG_CAP_EDIT_STOP, _START, _CHECK, _PY, _TRIES and _POLL default to the
# watchd stop and start, the project's `config check`, and the checkout's interpreter.
here="$(cd "$(dirname "$0")" && pwd)"; root="$(cd "$here/../../.." && pwd)"
usage() { echo "usage: cap-edit.sh set <key> <value> | cap-edit.sh reset <key>" >&2; exit 2; }
op=${1:-}; key=${2:-}; value=
case "$op" in
  set) [ $# -eq 3 ] || usage; value=$3;;
  reset) [ $# -eq 2 ] || usage;;
  *) usage;;
esac
: "${SG_STATE:?cap-edit: SG_STATE is not set}" "${SG_CHECKOUT:?cap-edit: SG_CHECKOUT is not set}"
file="$SG_CHECKOUT/docs/squad/defaults.yaml"
py=${SG_CAP_EDIT_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
snap=""; had=0; stopped=0

stop_dispatch() { if [ -n "${SG_CAP_EDIT_STOP:-}" ]; then "$SG_CAP_EDIT_STOP"; else bash "$here/watchd.sh" stop; fi; }
start_dispatch() { if [ -n "${SG_CAP_EDIT_START:-}" ]; then "$SG_CAP_EDIT_START"; else (cd "$SG_CHECKOUT" && bash "$here/watchd.sh" start); fi; }
check_settings() { if [ -n "${SG_CAP_EDIT_CHECK:-}" ]; then "$SG_CAP_EDIT_CHECK"; else PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.cli config --target "$SG_CHECKOUT" check; fi; }
edit_settings() { PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.cli config --target "$SG_CHECKOUT" "$@" >/dev/null; }

# live_runs: the listed runs that carry no verdict, one a line
live_runs() {
  local r f
  [ -f "$SG_STATE/active-runs.txt" ] || return 0
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    case "$r" in /*) f="$r/record.jsonl";; *) f="$SG_CHECKOUT/runs/$r/record.jsonl";; esac
    grep -q '"event": *"verdict"' "$f" 2>/dev/null || echo "$r"
  done < "$SG_STATE/active-runs.txt"
}

restore() {
  if [ "$had" = 1 ]; then cp "$snap" "$file"; else rm -f "$file"; fi
}

# Runs on every exit, so dispatch comes back on success, on a held or failed edit, and on a signal.
finish() {
  local rc=$?
  if [ "$stopped" = 1 ]; then
    if start_dispatch; then echo "CAP EDIT dispatch: started"
    else echo "CAP EDIT dispatch: start failed; start it by hand with watchd.sh start"; [ "$rc" = 0 ] && rc=1; fi
  fi
  [ -n "$snap" ] && rm -f "$snap"
  exit "$rc"
}
trap finish EXIT
trap 'exit 1' INT TERM HUP

stopped=1
if ! stop_dispatch; then echo "CAP EDIT stop failed: dispatch did not stop; nothing edited"; exit 1; fi
echo "CAP EDIT stop: dispatch stopped"

tries=0
while :; do
  live=$(live_runs | paste -sd, -)
  [ -z "$live" ] && break
  tries=$((tries + 1))
  if [ "$tries" -ge "${SG_CAP_EDIT_TRIES:-30}" ]; then echo "CAP EDIT held: live run $live holds the edit; no change made"; exit 3; fi
  sleep "${SG_CAP_EDIT_POLL:-10}"
done
echo "CAP EDIT wait: no live run in active-runs.txt"

snap=$(mktemp)
[ -f "$file" ] && { had=1; cp "$file" "$snap"; }
if [ "$op" = set ]; then edit=(set "$key" "$value"); else edit=(reset "$key"); fi
if ! edit_settings "${edit[@]}"; then
  restore
  echo "CAP EDIT edit failed: $key not written to $file; the old value stays"; exit 1
fi
if [ "$op" = set ]; then echo "CAP EDIT edit: $key set to $value in $file"; else echo "CAP EDIT edit: $key reset in $file"; fi

if ! check_settings; then
  restore
  echo "CAP EDIT check failed: the settings check refused $file; the old value is restored"; exit 1
fi
echo "CAP EDIT check: ok"
echo "CAP EDIT applied: $key in $file"
exit 0
