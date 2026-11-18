# usage: stack.sh <issue>   Dispatch readiness for one issue, from its "## Blocked by" section:
#   READY                    every blocker is closed, or every open blocker sits in Landing with an approved tip (#2260)
#   STACK TIP <sha> <branch> #<n>
#                            second line after READY, one per blocker satisfied that way: the approved tip the child's branch
#                            starts from, so its work builds on the blocker's line of commits and not on the base alone.
#   WAIT <reason>            anything else: an open blocker, a blocker whose state is not readable, a blocker the body names
#                            with no native blocked-by relation while it reads open (#1758), a blocker sitting in Doing or
#                            Review, or in Landing with no approved tip, or a ticket of a spec the owner has not started
#                            (below). The loop builds a child only on the line, so a blocker whose branch is not in $SG_BASE
#                            yet waits unless its card reads Landing and its review approves the tip (#2260). The landing
#                            pass rebases the child onto $SG_BASE before its own landing, and the janitor stops a run whose
#                            stacked blocker leaves Landing without landing (janitor.py, rule W).
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
# #2260 A blocker in Landing is a stop for the dependent no longer: its review approved the tip and the landing pass holds
# that tip, so the child starts on the blocker's own line of commits. The card's column is the caller's one board read
# ($SG_STACK_BOARD, "<issue>\t<state>" rows) and, for a blocker it lacks, the seam's own read of that card. A blocker in any
# other live column (Doing, Review, Todo) still waits: its work is not approved yet. The review is the one parser's
# (revformat.py --classify): an approval of THIS tip, never a first-line substring. A tip already in $SG_BASE is no stack:
# the child builds on the base and the blocker's work is already there.
land_cols=" Landing Code Complete Gating "
colof() { local c=""
  [ -n "${SG_STACK_BOARD:-}" ] && [ -s "$SG_STACK_BOARD" ] && c=$(awk -F '\t' -v n="$1" '$1 == n { print $2; exit }' "$SG_STACK_BOARD")
  [ -n "$c" ] || c=$(python3 "$here/issue_state.py" --state "$1" 2>/dev/null)
  printf '%s' "$c"; }
inbase() { [ -n "$1" ] && /usr/bin/git -C "${SG_CHECKOUT:-.}" merge-base --is-ancestor "$1" "refs/remotes/origin/$SG_BASE" 2>/dev/null; }
# landed_blocker <issue>: exit 0 and print "<tip> <branch>" when the blocker's card reads Landing and its review approves
# the branch tip. Exit 1 and print the reason otherwise.
landed_blocker() { local b=$1 c br tip rk cls
  c=$(colof "$b")
  [ -n "$c" ] || { printf ''; return 1; }   # an unread column is no Landing: the caller keeps its own wording for it
  case "$land_cols" in *" $c "*) ;; *) printf 'its card sits in %s, not in Landing' "$c"; return 1;; esac
  br=$(printf '%s' "${SG_FORK_BRANCH:-gui/%n}" | sed "s/%n/$b/")
  tip=$(gh_api "repos/$SG_REPO/commits/$br" -q .sha 2>/dev/null) || { printf 'its branch %s is not readable' "$br"; return 1; }
  case "$tip" in ''|*[!0-9a-fA-F]*) printf 'its branch %s names no tip' "$br"; return 1;; esac
  rk="$SG_STATE/review-$(printf '%s' "$br" | tr '/' '_')"
  cls=$(python3 "$here/revformat.py" --classify "$rk.md" "$rk.sha" "$tip" 2>/dev/null)
  [ "$cls" = approves ] || { printf 'it is in Landing with no approved tip (%s)' "${cls:-no review}"; return 1; }
  printf '%s %s' "$tip" "$br"; }
open=(); stacked=(); why=""; for b in $bl; do st=$(printf '%s' "$issue" | jq -r --argjson b "$b" '[.blockers[]?|select(.number==$b)|.state|select(.)][0] // empty' 2>/dev/null)
  [ -n "$st" ] || st=$(gh_api repos/$SG_REPO/issues/$b -q .state 2>/dev/null)
  [ "$st" = closed ] && continue
  [ "$st" = open ] || { echo "WAIT blocker #$b is not readable"; exit 0; }   # an unread state never counts as closed
  lb=""; reason=""
  if lb=$(landed_blocker "$b"); then
    set -- $lb; inbase "$1" || stacked+=("$1 $2 #$b")   # a tip in the base needs no cut: the child builds on the base
    why=""; continue
  fi
  reason=${lb:-its work is not in $SG_BASE yet}; open+=("$b"); why=${why:-"WAIT blocker #$b is open: $reason"}   # one reason, the first blocker's: it is what the card shows
done
if [ ${#open[@]} = 0 ]; then printf 'READY\n'; for s in ${stacked[@]+"${stacked[@]}"}; do echo "STACK TIP $s"; done; exit 0; fi
[ -n "$why" ] || why="WAIT blocker #${open[0]} is open: its work is not in $SG_BASE yet"
[ ${#open[@]} = 1 ] || why="$why; ${#open[@]} open blockers: ${open[*]/#/#}"
printf '%s\n' "$why"
exit 0
