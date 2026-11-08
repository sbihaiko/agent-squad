# usage: stack.sh <issue>   Dispatch readiness for one issue, from its "## Blocked by" section:
#   READY                    every blocker is closed
#   WAIT <reason>            anything else: an open blocker, a blocker whose state is not readable, a blocker sitting in Code Review or Code Complete
#                            whose work has not landed, or a ticket of a spec the owner has not started (below). The loop builds a
#                            child only on the line, so a blocker whose branch is not in $SG_BASE yet waits; optimistic branch
#                            stacking returns with the wave rules of ADR-0145 (#372).
# A caller that already holds the facts hands them over and saves the reads (#383): SG_STACK_ISSUE names a file with the
# issue's entry of the open-issue list (ghread.py's shape, each blocker's state beside it). Without it, or for a blocker whose
# state the entry lacks, the issue and the state are read one each.
# A ticket of a spec is work only once the owner starts the spec (ADR-0142 rule 6, #1620). Linking a sub-issue puts its card
# in Todo by itself, so the ticket's own column proves nothing about the spec's: the spec's card in Backlog, or no card at
# all, holds the ticket whatever its card reads. The parent is the native sub-issue link the entry carries; its column comes
# from the caller's one board read ($SG_STACK_BOARD, "<issue>\t<state>" rows), else from the seam itself.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; here="$(cd "$(dirname "$0")" && pwd)"; iss=$1
if [ -n "${SG_STACK_ISSUE:-}" ] && [ -s "$SG_STACK_ISSUE" ]; then issue=$(cat "$SG_STACK_ISSUE")
else issue=$(gh_api "repos/$SG_REPO/issues/$iss") || { echo "WAIT issue #$iss is not readable"; exit 0; }; fi
# An answer that does not carry the relation (no issue, a REST shape) is not "no blockers": dispatch waits until it is read.
printf '%s' "$issue" | jq -e '.blocked_by_read == true' >/dev/null 2>&1 || { echo "WAIT the blocked-by relation of #$iss is not readable"; exit 0; }
body=$(printf '%s' "$issue" | jq -r '.body // ""')
# The parent spec's column: from the caller's one board read, else from the seam. A parent the caller's rows lack is asked
# for itself, so a board read that omits a spec's card is never read as "started".
parent=$(printf '%s' "$issue" | jq -r '.parent // empty' 2>/dev/null)
if [ -n "$parent" ]; then
  col=""
  [ -n "${SG_STACK_BOARD:-}" ] && [ -s "$SG_STACK_BOARD" ] && col=$(awk -F '\t' -v n="$parent" '$1 == n { print $2; exit }' "$SG_STACK_BOARD")
  [ -n "$col" ] || col=$(python3 "$here/issue_state.py" --state "$parent" 2>/dev/null)
  case "$col" in
    Backlog) echo "WAIT ticket of spec #$parent: the spec's card sits in Backlog, and the owner starts a spec before any of its tickets (ADR-0142)"; exit 0;;
    "") echo "WAIT ticket of spec #$parent: the spec carries no readable card, so the owner has not started it (ADR-0142)"; exit 0;;
  esac
fi
# GitHub's blocked-by relation is the dispatch fact. The body remains a human-facing mirror, so a stale body edge is named
# once but never gates this decision.
bl=$(printf '%s' "$issue" | jq -r '.blocked_by[]?' | sort -nu)
body_bl=$(printf '%s\n' "$body" | awk '/^## Blocked by/{f=1;next} f&&/^## /{exit} f&&/^[[:space:]]*$/{next} f&&!/^[[:space:]]*([-*] |[0-9]+\. )/{exit} f' | grep -oE '(^|[^/[:alnum:]])#[0-9]+' | grep -oE '[0-9]+' | sort -nu)
for b in $body_bl; do case " $(echo $bl) " in *" $b "*) ;; *) echo "STACK NOTE #$iss: body names blocker #$b without a native blocked-by relation" >&2;; esac; done
hb="${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json"   # hub's board cache
open=(); for b in $bl; do st=$(printf '%s' "$issue" | jq -r --argjson b "$b" '[.blockers[]?|select(.number==$b)|.state|select(.)][0] // empty' 2>/dev/null)
  [ -n "$st" ] || st=$(gh_api repos/$SG_REPO/issues/$b -q .state 2>/dev/null)
  [ "$st" = closed ] && continue
  [ "$st" = open ] || { echo "WAIT blocker #$b is not readable"; exit 0; }   # an unread state never counts as closed
  open+=("$b"); done
[ ${#open[@]} = 0 ] && { echo "READY"; exit 0; }
[ ${#open[@]} = 1 ] || { echo "WAIT ${#open[@]} open blockers: ${open[*]/#/#}"; exit 0; }
echo "WAIT blocker #${open[0]} is open: its work is not in $SG_BASE yet"
