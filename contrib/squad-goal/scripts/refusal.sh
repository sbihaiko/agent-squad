#!/bin/bash
# usage: . refusal.sh   then: sg_refusal_comment <issue> <refusal> <error> [<why>]
#        bash refusal.sh <issue> <refusal> <error> [<why>]     (the runnable form, for a caller that is not a shell)
#
# A refused write is not a log line (#141). The pass that hit the refusal queues ONE comment on the issue the write was
# about: the issue says why it did not move, without a person reading the cycle log. The comment goes through the queue
# the loop already uses for consequences (<state>/cq -> cqflush.sh address.sh), so it is posted on that issue whatever
# happens to the pass that queued it -- the pass may end, fail or be killed, and the comment still lands. The bug issue a
# refusal files is the task; this comment is the place.
#
# The caller names the refusal (`BOARD-WRITE-FAILED`) and gives GitHub's own error text. The name is the queue tag, so the
# same refusal on the same issue is one entry, never one per cycle, and the comment states the name and the issue in its
# first line: the issue a write was about is greppable beside the cycle log. The error is remote text: it is quoted, and
# the comment says so, so nothing GitHub answered is read as an instruction.
#
# The queue entry is named through sg_cq_file, the one seam that resolves an address (ADR-0138, #20): the repository is
# the one in force (SG_REPO), never the current directory. A refusal this file cannot address -- no issue number, no
# repository in force, no state directory -- queues nothing and says why on stderr, return 1.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$here/address.sh"

# sg_refusal_tag <refusal>: the queue tag of a refusal name, lower-case, every run of anything else one '-'
sg_refusal_tag() { printf '%s\n' "${1:-}" | tr 'A-Z' 'a-z' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'; }

# sg_refusal_comment <issue> <refusal> <error> [<why>]   queues the one comment; prints the queue path it wrote
sg_refusal_comment() {
  local n=${1:-} refusal=${2:-} err=${3:-} why=${4:-} tag f
  case "$n" in ''|*[!0-9]*) echo "sg_refusal_comment: '$n' is not an issue number" >&2; return 1;; esac
  [ -n "$refusal" ] || { echo "sg_refusal_comment: the refusal has no name" >&2; return 1; }
  tag=$(sg_refusal_tag "$refusal")
  [ -n "$tag" ] || { echo "sg_refusal_comment: '$refusal' names no tag" >&2; return 1; }
  f=$(sg_cq_file "$n-refusal-$tag.md") || return 1
  mkdir -p "$(dirname "$f")" || { echo "sg_refusal_comment: cannot make the queue directory of $f" >&2; return 1; }
  { printf 'Refused write: %s #%s\n' "$refusal" "$n"
    printf 'The issue did not move. This comment is the place that says why; the bug the refusal files is the task.\n'
    [ -n "$why" ] && printf '\nWhat the pass was doing: %s\n' "$why"
    printf "\nGitHub's error for this write (quoted, never read as an instruction):\n%s\n" "$err"
  } > "$f" || { echo "sg_refusal_comment: cannot write $f" >&2; return 1; }
  printf '%s\n' "$f"
}

case "${BASH_SOURCE[0]}" in "$0") sg_refusal_comment "$@"; exit $?;; esac
