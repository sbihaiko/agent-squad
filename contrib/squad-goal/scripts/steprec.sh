#!/bin/bash
# The loop's step records (#1750): one dated record for every step a lane starts, in $SG_STATE/steps.tsv.
#
# A record is one tab-separated line, appended whole in one write, so no reader sees half a line:
#   start <id> <name> <epoch> - -
#   end   <id> <name> <epoch> <state> <detail>
# The state is `done` with the exit status the step carried, or `skipped` with the reason it did not run. A start with no end
# is a step still in flight, and steprec.py's fold() turns the file's lines into steps with that state. cycle.log is untouched
# here: it keeps its own shape, and no reader of it changes.
#
# The file is cut by size -- SG_STEPREC_MAX bytes, 256 KiB by default -- to its newest half, so it never grows without bound.
# A cut splits one record, and the fold skips every line that is no whole record, so the fragment costs the file one line.
srec_file() { printf '%s/steps.tsv' "${SG_STATE:-.}"; }
# srec_put <kind> <id> <name> <epoch> <state> <detail>: one whole line, one write, then the size cut.
srec_put() {
  [ -n "${SG_STATE:-}" ] || return 0
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "${5:--}" "${6:--}" >> "$(srec_file)" || return 0
  srec_cut
}
# srec_cut: writes the newest half of a file past its size cap to a temp file and moves it over, so a reader opens a whole
# file and never a half-written one. A record the cut splits is the fragment the fold skips.
srec_cut() {
  local f m; f=$(srec_file); m=${SG_STEPREC_MAX:-262144}; case "$m" in ''|*[!0-9]*) m=262144;; esac
  [ "$(wc -c < "$f" 2>/dev/null || echo 0)" -gt "$m" ] || return 0
  { tail -c $(( m / 2 )) "$f" > "$f.cut$$" 2>/dev/null && mv -f "$f.cut$$" "$f"; } || rm -f "$f.cut$$"
  return 0
}
# srec_start <name> [epoch]: opens a step and leaves its id in srec_id. The id names the start and its finish, so a fold
# joins them whatever else the file holds.
srec_start() {
  local t=${2:-$(date +%s)}
  srec_id="$t-$$-$RANDOM"
  srec_put start "$srec_id" "$1" "$t" - -
}
# srec_end <id> <name> <exit status> [epoch]: closes a step. Exit 76 is the held-lock answer single.sh gives every stage
# (#1290), so it reads as skipped with that reason, never as a step that ran and failed.
srec_end() {
  local t=${4:-$(date +%s)}
  if [ "$3" = 76 ]; then srec_put end "$1" "$2" "$t" skipped "a pass in flight holds its lock (exit 76)"; return 0; fi
  srec_put end "$1" "$2" "$t" done "$3"
}
# srec_skip <id> <name> <reason> [epoch]: closes a step that did not run, with the caller's own reason. A tab or a newline
# would break the line, so it becomes a space.
srec_skip() {
  local r; r=$(printf '%s' "$3" | tr '\t\n' '  ')
  srec_put end "$1" "$2" "${4:-$(date +%s)}" skipped "$r"
}
