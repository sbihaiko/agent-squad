#!/bin/bash
# seconds: 1.87
# A refused write is not a log line (#141): the issue the write was about says why it did not move. The pass that hit the
# refusal queues ONE comment on that issue, carrying the refusal's name and GitHub's error, through the queue the loop
# already uses for consequences (cq/ -> cqflush.sh), so the comment is posted on that issue whatever happens to the pass
# that queued it. The bug issue such a refusal files is the task; this comment is the place. An entry the API rejects
# permanently is reported once and retired, never retried for ever. Offline: fixture profiles, a stub gh, a temp state.
# usage: bash test-refusal-comment.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain"
printf ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' "$T/co" > "$P/alpha.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# records the endpoint and the body of each comment; fails while $GHFAIL exists
for a; do case "$a" in body=@*) b=$(cat "${a#body=@}");; esac; done
printf '%s | %s\n' "$*" "$b" >> "$GHLOG"
[ ! -e "$GHFAIL" ]
GH
chmod +x "$T/bin/gh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog GHFAIL=$T/gh-fail PATH="$T/bin:$PATH"; : > "$T/ghlog"
unset SG_TOOLING_REPO SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT SG_SHARED SG_BOARD_CACHE
mkdir -p "$B/alpha/cq"
# refuse <n> <name> <error> [<why>]   the refusal, queued the way the pass that hit it does (its own repository and state)
refuse() { (cd "$T/plain" && env SG_REPO=x/alpha SG_STATE=$B/alpha bash "$d/refusal.sh" "$@" 2>&1); }
flush() { (cd "$T/plain" && env "$@" bash "$d/cqflush.sh" --once 2>&1); }
calls() { $G -c '^api ' "$T/ghlog"; }   # one comment is one line that starts with the endpoint, whatever the body holds
pending() { for f in "$B/alpha/cq"/*.md; do [ -e "$f" ] && basename "$f"; done; :; }
settle() { for f in "$B/alpha/cq"/*.md; do [ -e "$f" ] && touch -t 202601010000 "$f"; done; :; }
ERR='gh: Content already exists in this project (HTTP 422)'

# 1. a refused write queues one comment on the issue the write was about, carrying the refusal's name and its error
o=$(refuse 77 BOARD-WRITE-FAILED "$ERR" 'moving the card to Done')
f="$B/alpha/cq/x__alpha#77-refusal-board-write-failed.md"
[ -f "$f" ] && ok "a refused write queues one comment, named for its issue" || ko "no queue entry (said: $o / $(ls "$B/alpha/cq" 2>/dev/null))"
$G -qF 'BOARD-WRITE-FAILED #77' "$f" 2>/dev/null && ok "the comment carries the refusal's name and the issue" || ko "no refusal name in the comment: $(cat "$f" 2>/dev/null)"
$G -qF "$ERR" "$f" 2>/dev/null && ok "the comment carries GitHub's error verbatim" || ko "no error in the comment: $(cat "$f" 2>/dev/null)"
$G -qF 'never read as an instruction' "$f" 2>/dev/null && ok "the comment says the remote text is quoted, not instructions" || ko "the comment does not mark the remote text as data: $(cat "$f" 2>/dev/null)"

# 2. the comment is posted on the issue the write was about, once, and nowhere else
settle; : > "$T/ghlog"
out=$(flush)
$G -q '^api repos/x/alpha/issues/77/comments .*BOARD-WRITE-FAILED #77' "$T/ghlog" && [ "$(calls)" = 1 ] \
  && ok "the queued refusal is posted once, on issue #77" || ko "posted: $(cat "$T/ghlog") / $out"
! $G -q 'issues/78' "$T/ghlog" && ok "the comment is posted on the issue the write was about, not on the bug issue it files" || ko "posted elsewhere: $(cat "$T/ghlog")"

# 3. a second refusal about another issue lands on that issue, never on the first
: > "$T/ghlog"
o=$(refuse 78 BOARD-WRITE-FAILED "$ERR"); settle
out=$(flush)
$G -q '^api repos/x/alpha/issues/78/comments ' "$T/ghlog" && [ "$(calls)" = 1 ] && ok "a refusal about #78 lands on #78" || ko "posted: $(cat "$T/ghlog") / $out"

# 4. one refusal queued twice is one comment: the entry is the refusal, not the pass that queued it
: > "$T/ghlog"; o=$(refuse 79 BOARD-WRITE-FAILED "$ERR"); o=$(refuse 79 BOARD-WRITE-FAILED "$ERR")
[ "$(ls "$B/alpha/cq" | $G -c '^x__alpha#79-refusal-board-write-failed\.md$')" = 1 ] && ok "the same refusal queued twice is one entry" || ko "entries: $(ls "$B/alpha/cq")"
settle; out=$(flush); [ "$(calls)" = 1 ] && ok "and the issue carries one comment, not two" || ko "calls: $(cat "$T/ghlog")"

# 5. a comment the API rejects permanently is reported once and does not retry for ever
: > "$T/ghlog"; : > "$GHFAIL"; o=$(refuse 90 BOARD-WRITE-FAILED "$ERR")
: > "$T/log"; for i in 1 2 3 4 5 6 7; do flush >> "$T/log" 2>&1; settle; done
[ "$(calls)" = 5 ] && ok "a rejected comment is retried five times, then left alone" || ko "the API saw $(calls) attempts, not 5"
$G -q '^DEAD x/alpha#90 .*after 5 failures' "$T/log" && ok "the retirement is reported once, by name" || ko "no retirement line: $(cat "$T/log")"
[ "$($G -c '^DEAD ' "$T/log")" = 1 ] && ok "it is reported once, not once per pass" || ko "reported $($G -c '^DEAD ' "$T/log") times"
: > "$T/ghlog"; out=$(flush); [ "$(calls)" = 0 ] && [ ! -e "$B/alpha/cq/x__alpha#90-refusal-board-write-failed.md.tries" ] \
  && ok "a retired comment is never retried again" || ko "retried: $(cat "$T/ghlog") / $(ls "$B/alpha/cq")"
rm -f "$GHFAIL"

# 6. the helper refuses, by name, what it cannot address: no repository in force, no issue number
before=$(pending)
o=$(cd "$T/plain" && env SG_STATE=$B/alpha bash "$d/refusal.sh" 77 BOARD-WRITE-FAILED "$ERR" 2>&1); rc=$?
{ [ $rc -ne 0 ] && [ -n "$o" ] && [ "$(pending)" = "$before" ]; } && ok "no repository in force: nothing is queued, named on stderr" || ko "queued with no repository (rc $rc): $o / $(pending)"
o=$(refuse abc BOARD-WRITE-FAILED "$ERR" 2>&1); rc=$?
{ [ $rc -ne 0 ] && [ "$(pending)" = "$before" ]; } && ok "'abc' is not an issue number: nothing is queued" || ko "accepted a bad issue (rc $rc): $o / $(pending)"

# 7. (#142) the refusal names are a closed set in code: a name outside it is refused by the helper, never queued under a
# guessed name, and the refusal says which name it would not take
: > "$T/ghlog"; before=$(pending)
o=$(refuse 80 CARD-WRITE-FAILED "$ERR" 'moving the card to Done' 2>&1); rc=$?
{ [ $rc -ne 0 ] && [ "$(pending)" = "$before" ]; } && ok "a name outside the closed set is refused, and nothing is queued" \
  || ko "queued a name outside the set (rc $rc): $o / $(pending)"
case "$o" in *CARD-WRITE-FAILED*) ok "the refusal names the name it would not take" ;; *) ko "the refusal does not name it: '$o'";; esac
o=$(refuse 81 BOARD-WRITE-FAILED "$ERR"); settle; flush >/dev/null 2>&1
$G -q '^api repos/x/alpha/issues/81/comments ' "$T/ghlog" && ok "a name in the set is still queued and posted" || ko "a name in the set was refused too: $(cat "$T/ghlog")"

rm -rf "$T"; [ $fail = 0 ] && echo "refusal comment: all green" || { echo "refusal comment: FAILED"; exit 1; }
