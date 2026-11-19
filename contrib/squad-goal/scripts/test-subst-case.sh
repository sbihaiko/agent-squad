#!/bin/bash
# seconds: 3.25
# A `case` never opens inside a command substitution. Bash closes `$(...)` at the first `)`, so `$(case "$2" in /*) ...)`
# ends at the pattern's own `)` and the rest of the line runs as literal text: the substitution yields garbage and no
# `bash -n` pass detects it. This guard scans every script of this directory for that shape (#2094).
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep

# scan <file>: prints "line:text" for each command substitution that opens with a case.
scan() { $G -nE '\$\([[:space:]]*case[[:space:]]|`[[:space:]]*case[[:space:]]' "$1" 2>/dev/null; }

# 1) The detector finds a planted line, so a green scan means something.
printf 'x=$(case "$1" in a) echo a;; esac)\n' > "$T/planted.sh"
[ -n "$(scan "$T/planted.sh")" ] && ok "the detector names a case that opens a command substitution" || ko "the detector missed a planted line"

# 2) The detector leaves an ordinary case alone.
printf 'case "$1" in a) echo a;; esac\ny=$(printf "%%s" "$1")\n' > "$T/plain.sh"
[ -z "$(scan "$T/plain.sh")" ] && ok "a case at statement level is allowed" || ko "the detector flagged a plain case: $(scan "$T/plain.sh")"

# 3) No script of this directory carries the shape.
bad=0
for f in "$d"/*.sh; do
  [ "$(basename "$f")" = test-subst-case.sh ] && continue   # this file names the shape in prose and plants it
  hits=$(scan "$f")
  [ -n "$hits" ] || continue
  bad=1
  echo "FAIL $(basename "$f"): a case opens a command substitution: $hits"
done
[ "$bad" = 0 ] && ok "no script opens a case inside a command substitution" || fail=1

rm -rf "$T"; exit $fail
