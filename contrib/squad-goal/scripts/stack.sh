# usage: stack.sh <issue>   Dispatch readiness for one issue, from its "## Blocked by" section:
#   READY                    every blocker is closed
#   WAIT <reason>            anything else: an open blocker, a blocker whose state is not readable, a blocker the body names
#                            with no native blocked-by relation while it reads open (#1758), a blocker sitting in Review or
#                            Landing whose work has not landed, or a ticket of a spec the owner has not started (below). The loop builds a
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
# The dependency component of this card, from the pass's own issue list (#1984): an invalid or unknown component holds it, and
# an owner hold names the owner and writes one alert. A pass that passes no list, or a copy of this script without deadlock.py
# beside it, keeps the gates below.
if [ -n "${SG_STACK_GRAPH:-}" ] && [ -s "$SG_STACK_GRAPH" ] && [ -f "$here/deadlock.py" ]; then
  v=$(python3 "$here/deadlock.py" verdict "$SG_STACK_GRAPH" "$SG_REPO" "$iss" 2>/dev/null) || v='{"status":"unknown","reasons":["the validator did not answer"],"actor":null}'
  vs=$(printf '%s' "$v" | jq -r '.status'); vr=$(printf '%s' "$v" | jq -r '.reasons[0] // ""'); va=$(printf '%s' "$v" | jq -r '.actor // ""')
  case "$vs" in
    invalid|unknown) echo "WAIT dependency component of #$iss is $vs: $vr"; exit 0;;
    held) echo "WAIT dependency component of #$iss is held by $va: $vr"
          if [ -n "${SG_STATE:-}" ] && ! grep -qF "STACK alert #$iss waits on $va: $vr" "$SG_STATE/alerts.log" 2>/dev/null; then
            printf '%s STACK alert #%s waits on %s: %s\n' "$(date '+%F %T')" "$iss" "$va" "$vr" >> "$SG_STATE/alerts.log"
          fi
          exit 0;;
  esac
fi
# GitHub's blocked-by relation is the dispatch fact. A blocker the body names without that relation still holds the ticket
# while it reads open (#1758): the body's "## Blocked by" section is how a ticket without the native edge states its wait,
# and reading it as no edge dispatched tickets whose blocker was in flight. Its state is read alone, one call, and the note
# names the missing relation whichever way it reads; only a blocker that reads closed holds nothing.
bl=$(printf '%s' "$issue" | jq -r '.blocked_by[]?' | sort -nu)
body_bl=$(printf '%s\n' "$body" | awk '/^## Blocked by/{f=1;next} f&&/^## /{exit} f&&/^[[:space:]]*$/{next} f&&!/^[[:space:]]*([-*] |[0-9]+\. )/{exit} f' | grep -oE '(^|[^/[:alnum:]])#[0-9]+' | grep -oE '[0-9]+' | sort -nu)
for b in $body_bl; do
  case " $(echo $bl) " in *" $b "*) continue;; esac   # the native relation decides that blocker, and the caller's own loop below
  echo "STACK NOTE #$iss: body names blocker #$b without a native blocked-by relation" >&2
  st=$(gh_api repos/$SG_REPO/issues/$b -q .state 2>/dev/null)
  [ "$st" = closed ] && continue   # a closed blocker named as text holds nothing
  [ "$st" = open ] || { echo "WAIT blocker #$b, named in the body, is not readable"; exit 0; }   # an unread state never counts as closed
  echo "WAIT blocker #$b is open: the body names it with no native blocked-by relation, and its work is not in $SG_BASE yet"; exit 0
done
hb="${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json"   # hub's board cache
open=(); for b in $bl; do st=$(printf '%s' "$issue" | jq -r --argjson b "$b" '[.blockers[]?|select(.number==$b)|.state|select(.)][0] // empty' 2>/dev/null)
  [ -n "$st" ] || st=$(gh_api repos/$SG_REPO/issues/$b -q .state 2>/dev/null)
  [ "$st" = closed ] && continue
  [ "$st" = open ] || { echo "WAIT blocker #$b is not readable"; exit 0; }   # an unread state never counts as closed
  open+=("$b"); done
[ ${#open[@]} = 0 ] && { echo "READY"; exit 0; }
[ ${#open[@]} = 1 ] || { echo "WAIT ${#open[@]} open blockers: ${open[*]/#/#}"; exit 0; }
echo "WAIT blocker #${open[0]} is open: its work is not in $SG_BASE yet"
