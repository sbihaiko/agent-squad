#!/bin/bash
# usage: check-gh-interface.sh [tree]        tree defaults to the repository this script ships in
#
# ADR-0277, #8: every GitHub read the loop makes goes through one interface (#1893: ghread.py, and the shell door
# ghcall.sh every script reaches it through), and so does every write. This guard refuses a live script that keeps its
# own call site -- a `gh api …` of its own -- naming the script, the line and the route, because a caller that reads
# GitHub itself reads a refusal as data and can retry straight through a cooldown, and a caller that writes GitHub
# itself reads a refusal as success: `gh api -X PATCH … && touch moved` marks a card moved while GitHub refused it.
#
# It runs where the repository's other guards run -- from the profile's $SG_PREPUSH_CHECKS, beside check-tdd-red.sh, so
# the loop runs it in the tree before every push and a landing is gated by it -- and its own suite
# (test-check-gh-interface.sh) runs the live tree through it in the loop's shell suites. Exit 0 when clean, 1 on any
# live script that reaches GitHub directly.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$here/check-gh-interface.py" "$@"
