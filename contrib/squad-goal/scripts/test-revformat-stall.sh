#!/bin/bash
# seconds: 0.74
# Regression tests for the fix-round rule ADR-0134 keeps in revformat.py: the fingerprint of a review's findings, and the
# count of failed rounds in a row for one marker set (--fingerprint, --stall, --record-stall). branchfixpass.sh asks both,
# and a child fix round asks the same rule later (#565), so the two cannot read one review two ways.
# No network, no model. usage: bash test-revformat-stall.sh [path/to/revformat.py]
src=${1:-$(dirname "$0")/revformat.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
fp() { python3 "$src" --fingerprint "$1"; }
stall() { python3 "$src" --stall "$@"; }        # <dir> <prefix> <key> <fingerprint>
record() { python3 "$src" --record-stall "$@"; }  # <dir> <prefix> <key> <fingerprint> <rounds>
cat > "$T/r1.md" <<'M'
VERDICT: CHANGES
## Blocking
- gui/7-x  the cap is wrong at src/a.sh:312 — commit 3f2a9b1c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80
- whole diff	—	the commit message misstates the change
## Debt
- src/b.py:9 — naming — unclear — rename it
M
# The same findings at another place: a moved line number, a new sha and other whitespace are not a change.
cat > "$T/r2.md" <<'M'
VERDICT: CHANGES
## Blocking
- gui/7-x  the cap is wrong at src/a.sh:98 — commit 0123456789abcdef0123456789abcdef01234567
- whole diff	—	the commit message misstates the change
## Debt
- src/b.py:44 — naming — unclear — rename it
M
# A finding that changed: the round fixed nothing the review can see, and the count restarts.
cat > "$T/r3.md" <<'M'
VERDICT: CHANGES
## Blocking
- gui/7-x  the floor is wrong at src/a.sh:312 — commit 3f2a9b1c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80
M

# 1) the fingerprint is the value branchfixpass.sh's own findings() gave on this review before the move (#565), pinned
#    here as the literal that function printed: a live auto/branchfix-fp-<key> marker still matches after the move
[ "$(fp "$T/r1.md")" = 4f1e953cfa9d90b4 ] && ok "the fingerprint of the fixture review is today's findings() value" || ko "the fixture review fingerprints as '$(fp "$T/r1.md")', not 4f1e953cfa9d90b4"
# 2) equal findings with moved lines give the same fingerprint
[ "$(fp "$T/r2.md")" = "$(fp "$T/r1.md")" ] && ok "a moved line and a new sha give the same fingerprint" || ko "a moved line changed the fingerprint: '$(fp "$T/r1.md")' -> '$(fp "$T/r2.md")'"
[ "$(fp "$T/r3.md")" != "$(fp "$T/r1.md")" ] && ok "a changed finding gives another fingerprint" || ko "a changed finding kept the fingerprint"
# 3) the count of failed rounds in a row: the same findings raise it, a changed finding resets it
record "$T/a" branchfix gui_7-x "$(fp "$T/r1.md")" 2
[ "$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r2.md")")" = 3 ] && ok "the same findings raise the count of failed rounds" || ko "the count was '$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r2.md")")', not 3"
[ "$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r3.md")")" = 0 ] && ok "changed findings reset the count" || ko "changed findings counted '$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r3.md")")', not 0"
# 4) a round the pass kept is the round the next pass reads: a changed count recorded at 0 counts 1 when the same
#    findings come back, as branchfixpass.sh counted them before the move
record "$T/a" branchfix gui_7-x "$(fp "$T/r3.md")" 0
[ "$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r3.md")")" = 1 ] && ok "the round after a reset counts one failed round" || ko "after a reset the count was '$(stall "$T/a" branchfix gui_7-x "$(fp "$T/r3.md")")', not 1"
# 5) the prefix and the key name the markers, so each pass keeps its own: another prefix in the same directory is another count
[ "$(stall "$T/a" other gui_7-x "$(fp "$T/r1.md")")" = 0 ] && ok "another marker prefix keeps its own count" || ko "the marker prefix was ignored: '$(stall "$T/a" other gui_7-x "$(fp "$T/r1.md")")'"
[ -f "$T/a/branchfix-fp-gui_7-x" ] && [ -f "$T/a/branchfix-same-gui_7-x" ] && ok "the markers are <dir>/<prefix>-fp-<key> and <dir>/<prefix>-same-<key>" || ko "the markers are named $(ls "$T/a")"
# 6) no marker yet is no failed round
[ "$(stall "$T/a" branchfix gui_9-z "$(fp "$T/r1.md")")" = 0 ] && ok "a key with no marker has no failed round" || ko "a fresh key counted '$(stall "$T/a" branchfix gui_9-z "$(fp "$T/r1.md")")'"
exit $fail
