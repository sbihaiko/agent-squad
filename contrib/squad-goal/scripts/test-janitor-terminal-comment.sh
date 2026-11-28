#!/bin/bash
# seconds: 2.71
# Every terminal entry carries its condition in a comment (#245, spec #132): with SG_BOARD_VOCAB=shipped a move into Closed
# queues one comment naming the branch that is in dev and the test on its tip, and a move into Abandoned one naming
# who decided and why. A terminal write whose condition cannot be read is not made (the guard). A card that settled in a
# terminal column with no such comment gets one, once. Stubbed gh and board.sh; a temp state dir, never the live board.
# usage: bash test-janitor-terminal-comment.sh [janitor.py]
# (#3114) The janitor loads squad/procs.py from the tree it runs in: it resolves its own path and walks up from there, then
# SG_SQUAD, SG_CHECKOUT and cwd. The daily pass clears every SG_* and runs from a cwd outside the repository, so the suite
# drops the inherited names and symlinks the janitor into its sandbox. The symlink resolves to the repository's own
# janitor, so the walk-up that answers in the daily pass is the walk-up this suite exercises; a janitor that cannot find
# its tree goes red here instead of borrowing a package the fixture placed beside it.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)'); do unset "$v"; done   # a caller's SG_SQUAD or SG_CHECKOUT never answers for this suite
src=$(cd "$(dirname "$src")" && pwd -P)/$(basename "$src")   # a relative invocation resolves before the repo is derived from it
[ -f "$src" ] || { echo "FAIL suite: no janitor at $src"; exit 1; }
repo=$(cd "$(dirname "$src")/../../.." && pwd -P)
[ -d "$repo/squad" ] || { echo "FAIL suite: $repo/squad is absent"; exit 1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(dirname "$src")"
ln -s "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cp "$here/specstall.py" "$here/issue_ledger.py" "$here/gate_attempts.py" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields|--clear-run) :;;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls"; if [ -n "$SG_MOVE_REASON" ]; then echo "REASON $SG_MOVE_REASON $4" >> "TMPDIR/board.calls"; fi;;
esac
J
sed -i "" "s|TMPDIR|$T|g" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *--include*repos/x/y/branches/gui%2F2201-a*) echo '{"commit":{"sha":"abcdef1234567890"}}';;
  *--include*repos/x/y/branches/gui%2F2205-b*) echo '{"commit":{"sha":"5555555aaaaaaaa"}}';;
  *repos/x/y/branches*) printf 'gui/2201-a abcdef1234567890\ngui/2205-b 5555555aaaaaaaa\ngui/2211-c 1111111bbbbbbbb\ngui/2212-1222 1212121cccccccc\n';;
  *compare/dev...*) echo behind;;
  *--include*commits/abcdef1234567890/check-runs*) echo '{"check_runs":[{"name":"test (3.11)","status":"completed","conclusion":"success"}]}';;
  *--include*commits/5555555aaaaaaaa/check-runs*) echo '{"check_runs":[{"name":"test (3.13)","status":"completed","conclusion":"success"}]}';;
  *--include*issues/2202/events*) echo '[{"event":"labeled","label":{"name":"wontfix"},"actor":{"login":"rafael"}}]';;
  *--include*issues/2205/comments*) echo '[]';;
  *--include*issues/2206/comments*) echo '[{"body":"<!-- squad-goal:terminal -->\nClosed: earlier note"}]';;
  *--include*issues/2203/events*) echo '[{"event":"labeled","label":{"name":"bug"},"actor":{"login":"x"}},{"event":"closed","actor":{"login":"owner1"},"state_reason":"not_planned"}]';;
  *--include*issues/2208/events*) echo '[{"event":"closed","actor":{"login":"owner1"},"state_reason":"not_planned"}]';;
  *--include*issues/2208/comments*) echo '[]';;
  *--include*issues/2209/comments*) echo '[]';;
  *--include*issues/2210/comments*) echo '[]';;
  *--include*issues/2210/events*) echo '[{"event":"closed","actor":{"login":"owner2"},"state_reason":"duplicate"}]';;
  *--include*issues/2209/events*) echo '[{"event":"closed","actor":{"login":"sbihaiko"}},{"event":"reopened","actor":{"login":"sbihaiko"}}]';;
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    printf '2201\tLanding\thttps://api.github.com/repos/x/y\n2202\tTodo\thttps://api.github.com/repos/x/y\n'
    printf '2203\tTodo\thttps://api.github.com/repos/x/y\n2204\tLanding\thttps://api.github.com/repos/x/y\n'
    printf '2205\tClosed\thttps://api.github.com/repos/x/y\n2206\tClosed\thttps://api.github.com/repos/x/y\n'
    printf '2208\tClosed\thttps://api.github.com/repos/x/y\n'
    /usr/bin/grep -q '^STATUS o 4 Todo 2209$' "TMPDIR/board.calls" 2>/dev/null && c=Todo || c=Closed   # the board keeps the move: a second pass reads Todo
    printf '2209\t%s\thttps://api.github.com/repos/x/y\n' "$c"
    printf '2211\tClosed\thttps://api.github.com/repos/x/y\n'
    /usr/bin/grep -q '^STATUS o 4 Todo 2212$' "TMPDIR/board.calls" 2>/dev/null && c2=Todo || c2=Closed   # the board keeps the move: a second pass reads Todo
    printf '2212\t%s\thttps://api.github.com/repos/x/y\n' "$c2"   # a reopened spec: its card sits Closed while a ticket is in flight (#1431)
    printf '2213\tTodo\thttps://api.github.com/repos/x/y\n'       # the spec's ticket, in flight
    printf '2210\tTodo\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    printf '{"number":2202,"title":"Issue 2202","body":"","state":"OPEN","labels":{"nodes":[{"name":"wontfix"}]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf ',{"number":2209,"title":"Issue 2209","body":"","state":"OPEN","labels":{"nodes":[{"name":"ready-for-agent"},{"name":"rework"}]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf ',{"number":2211,"title":"Issue 2211","body":"","state":"OPEN","labels":{"nodes":[{"name":"ready-for-agent"}]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf ',{"number":2212,"title":"Issue 2212","body":"","state":"OPEN","labels":{"nodes":[{"name":"spec"}]},"subIssuesSummary":{"total":1,"completed":0},"subIssues":{"nodes":[{"number":2213}]},"comments":{"nodes":[]}}'
    printf ',{"number":2213,"title":"Issue 2213","body":"","state":"OPEN","labels":{"nodes":[{"name":"ready-for-agent"}]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
    printf ']}}}}';;
  *issues/2201*|*issues/2204*|*issues/2205*) echo $'closed\tcompleted\tIssue';;
  *issues/2203*) echo $'closed\tnot_planned\tIssue';;
  *issues/2208*) echo $'closed\tnot_planned\tIssue';;
  *issues/2210*) echo $'closed\tduplicate\tIssue';;
  *issues/22*) echo $'open\t\tIssue\twontfix';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mkdir -p "$T/co/runs/run-a"; echo run-a > "$T/st/active-runs.txt"
printf 'CHILD — GitHub issue(s) 2201, 2203, 2204, 2210:\n' > "$T/co/runs/run-a/request.txt"
printf '%s\n' '{"event":"run_start","run_id":"run-a","ts":"2026-10-14T08:00:00Z"}' '{"event":"spawn_accepted","child":"a","definition":"GitHub issue #2201"}' '{"event":"spawn_accepted","child":"b","definition":"GitHub issue #2203"}' '{"event":"spawn_accepted","child":"c","definition":"GitHub issue #2210"}' '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}' > "$T/co/runs/run-a/record.jsonl"
# #1431: a spec's children land their own commits under the parent's number (#665). The spec #2212 is open again with a
# ticket in flight, and its child's branch is in dev; the spec's card still returns to Todo, because a spec's own work is
# never a branch. #2212 holds both facts the reopen rule's skip reads (a work branch in dev and a commit in dev naming it),
# and #2211 holds the in-dev branch fact without the spec label: it is the same-run control, the plain issue that keeps
# its Closed card while the spec returns to Todo.
git -C "$T/co" init -q
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A child of the spec landed (#2212)"
git -C "$T/co" update-ref refs/remotes/origin/dev HEAD
git -C "$T/co" log --format=%s origin/dev | /usr/bin/grep -q '(#2212)$' || { echo "FAIL fixture: no commit in dev names #2212"; exit 1; }
# The fixture states the facts the reopen rule's skip reads: #2212 both, a commit in dev naming it (named_in_land) and a
# work branch in dev (branch_in_dev_count); #2211 the in-dev branch. Read through the same stubbed route the janitor uses.
gh api repos/x/y/branches | /usr/bin/grep -q '^gui/2211-' && gh api repos/x/y/branches | /usr/bin/grep -q '^gui/2212-' \
  && [ "$(gh api repos/x/y/compare/dev...1111111bbbbbbbb -q .status)" = behind ] && [ "$(gh api repos/x/y/compare/dev...1212121cccccccc -q .status)" = behind ] \
  || { echo "FAIL fixture: #2211/#2212 have no work branch in dev"; exit 1; }
# The daily pass clears every SG_* and may run from a cwd outside the repository (#2307), so each pass runs from the
# fixture's own folder: a green that needs the caller's cwd to resolve the janitor's root is no green on dev.
run() { (cd "$T" && SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1); }
o=$(run); r1=$(/usr/bin/grep -c 'issues/2208' "$T/gh.calls" 2>/dev/null); : > "$T/gh.calls"; o2=$(run); r2=$(/usr/bin/grep -c 'issues/2208' "$T/gh.calls" 2>/dev/null)
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
note() { cat "$T"/st/cq/x__y#"$1"-terminal-*.md 2>/dev/null; }
count() { ls "$T"/st/cq/ 2>/dev/null | /usr/bin/grep -c "^x__y#$1-terminal-"; }
has '^STATUS o 4 Closed 2201$' && ok "a closed issue moves into Closed" || ko "#2201 not Closed: $(cat "$T/board.calls")"
note 2201 | /usr/bin/grep -q 'gui/2201-a' && note 2201 | /usr/bin/grep -q 'abcdef1' && note 2201 | /usr/bin/grep -q 'test (3.11): success' \
  && ok "the Closed comment names the branch in dev and the test on its tip" || ko "#2201 comment: $(note 2201) $o"
has '^STATUS o 4 Abandoned 2202$' && ok "a wontfix issue moves into Abandoned" || ko "#2202 not Abandoned: $(cat "$T/board.calls")"
note 2202 | /usr/bin/grep -q 'wontfix' && note 2202 | /usr/bin/grep -q '@rafael' && ok "the Abandoned comment names who labelled wontfix" || ko "#2202 comment: $(note 2202)"
has '^STATUS o 4 Abandoned 2203$' && ok "an issue closed as not planned moves into Abandoned" || ko "#2203 not Abandoned: $(cat "$T/board.calls")"
note 2203 | /usr/bin/grep -q 'not planned' && note 2203 | /usr/bin/grep -q '@owner1' && ok "the Abandoned comment names who closed it and why" || ko "#2203 comment: $(note 2203)"
has '^STATUS o 4 Abandoned 2210$' && ok "an issue closed as a duplicate moves into Abandoned" || ko "#2210 not Abandoned: $(cat "$T/board.calls")"
note 2210 | /usr/bin/grep -q 'duplicate' && note 2210 | /usr/bin/grep -q '@owner2' && ok "the Abandoned comment names the duplicate and who closed it" || ko "#2210 comment: $(note 2210)"
! has ' 2204$' && ok "a terminal write whose condition cannot be read is not made" || ko "#2204 written with no comment: $(cat "$T/board.calls")"
[ "$(count 2204)" = 0 ] && /usr/bin/grep -q "TERMINAL-NO-CONDITION #2204" "$T/st/cycle.log" && ok "the refused terminal write is named" || ko "#2204 not named: $o"
note 2205 | /usr/bin/grep -q 'gui/2205-b' && ok "a settled Closed card with no comment is backfilled" || ko "#2205 not backfilled: $(note 2205) $o"
[ "$(count 2206)" = 0 ] && ok "a settled card that already carries its comment gets none" || ko "#2206 commented twice"
! has ' 2208$' && ok "a settled card whose condition cannot be stated is not moved" || ko "#2208 written: $(cat "$T/board.calls")"
[ -f "$T/st/auto/janitor-terminal-unreadable-2208" ] && [ "$(cat "$T/st/auto/janitor-terminal-unreadable-2208")" = Closed ] \
  && ok "an unreadable terminal condition is remembered by a marker under SG_STATE" || ko "#2208 marker missing or wrong"
[ "$(/usr/bin/grep -c 'TERMINAL-NO-CONDITION #2208' "$T/st/cycle.log")" = 1 ] && ok "an unreadable condition is reported once over two passes" || ko "#2208 reported $(/usr/bin/grep -c 'TERMINAL-NO-CONDITION #2208' "$T/st/cycle.log") times: $o$o2"
/usr/bin/grep -q 'TERMINAL-NO-CONDITION #2208: settled in Closed, but the issue is closed as not_planned; its condition is Abandoned' "$T/st/cycle.log" \
  && ok "the one report names the condition the issue actually carries" || ko "#2208 report: $(/usr/bin/grep 'TERMINAL-NO-CONDITION #2208' "$T/st/cycle.log")"
[ "$r1" -gt 0 ] && [ "$r2" = 0 ] && ok "a second pass over the same unreadable issue makes no read" || ko "#2208 reads: first $r1, second $r2: $o$o2"
[ "$(count 2208)" = 0 ] && ok "a remembered unreadable card gets no terminal comment" || ko "#2208 commented"
for n in 2201 2202 2203 2205 2210; do [ "$(count $n)" = 1 ] || ko "#$n has $(count $n) terminal comments after two passes, not one"; done
[ "$fail" = 0 ] && ok "every terminal entry has exactly one comment after two passes"
note 2201 | /usr/bin/grep -q 'squad-goal:terminal' && ok "the comment carries the tag the backfill reads" || ko "no tag: $(note 2201)"
# The run's summary rides each child's terminal comment, so a run parent is never needed (#559): the run id, the other
# children, the verdict and the dashboard command. A card no run holds gets the plain comment.
for n in 2201 2203; do other=2203; [ $n = 2203 ] && other=2201
  note $n | /usr/bin/grep -q 'run-a' && note $n | /usr/bin/grep -q "#$other" && note $n | /usr/bin/grep -q 'pass' && note $n | /usr/bin/grep -q 'dashboard run-a' \
    && ok "#$n's terminal comment carries the run id, the other child #$other, the verdict and the dashboard command" || ko "#$n comment has no run summary: $(note $n)"
done
note 2205 | /usr/bin/grep -q 'dashboard' && ko "#2205 belongs to no run but its comment names a dashboard: $(note 2205)" || ok "a card no run holds gets no run summary"
# A Closed comment states a close that still holds (#775): the issue state is read again before the comment is queued,
# and an issue that is open again (#2209 was reopened by hand) gets no comment, only a log line.
[ "$(count 2209)" = 0 ] && ok "a Closed card whose issue is open again gets no terminal comment" || ko "#2209 commented while open: $(note 2209)"
/usr/bin/grep -q 'TERMINAL-REOPENED #2209' "$T/st/cycle.log" && ok "the skipped comment is named in cycle.log" || ko "#2209 not named: $(/usr/bin/grep 2209 "$T/st/cycle.log")"
# A reopened issue's Closed card returns to Todo under SG_MOVE_REASON=reopened, once (#1420, ADR-0143 rule 2).
[ "$(/usr/bin/grep -c '^STATUS o 4 Todo 2209$' "$T/board.calls")" = 1 ] && ok "a reopened issue's card moves Closed to Todo once over two passes" || ko "#2209 not moved once: $(cat "$T/board.calls")"
/usr/bin/grep -q '^REASON reopened 2209$' "$T/board.calls" && ok "the move names SG_MOVE_REASON=reopened" || ko "#2209 move has no reopened reason"
/usr/bin/grep -q 'card -> Todo.*2209\|#2209: card -> Todo' "$T/st/cycle.log" && ok "cycle.log names the move" || ko "#2209 move not named"
# An open issue whose work branch is in dev keeps its Closed card: Todo would cycle it through the later rules (#1420).
! /usr/bin/grep -q ' 2211$' "$T/board.calls" && ok "an open issue whose branch is in dev keeps its Closed card over two passes" || ko "#2211 moved: $(cat "$T/board.calls")"
# A reopened spec is never a branch delivery (#1431): its card sits Closed while a ticket of it is in flight and its child's
# branch is in dev, and it returns to Todo with the same reason, once. It never keeps Closed and never gains the reverted tag.
# #2211 is the plain control in the same run: the same in-dev branch keeps its Closed card, so the two assertions together
# fail if the reopen rule drops its spec split.
[ "$(/usr/bin/grep -c '^STATUS o 4 Todo 2212$' "$T/board.calls")" = 1 ] && ok "a reopened spec's card moves Closed to Todo once over two passes" || ko "#2212 not moved once: $(cat "$T/board.calls")"
/usr/bin/grep -q '^REASON reopened 2212$' "$T/board.calls" && ok "the spec move names SG_MOVE_REASON=reopened" || ko "#2212 move has no reopened reason"
[ "$(/usr/bin/grep -E '^(STATUS|ADD|REMOVE).* 2212$' "$T/board.calls" | /usr/bin/grep -vc '^STATUS o 4 Todo 2212$')" = 0 ] && ok "the spec card never stays in a terminal column" || ko "#2212 written elsewhere: $(/usr/bin/grep ' 2212$' "$T/board.calls")"
! /usr/bin/grep -q 'issues/2212/labels' "$T/gh.calls" && ok "the reopened spec gains no reverted tag" || ko "#2212 tagged: $(/usr/bin/grep 'issues/2212' "$T/gh.calls")"
[ "$(count 2212)" = 0 ] && ok "a reopened spec gets no Closed comment" || ko "#2212 commented while open: $(note 2212)"
/usr/bin/grep -q 'TERMINAL-REOPENED #2212' "$T/st/cycle.log" && ok "the spec reopen is named in cycle.log" || ko "#2212 not named: $(/usr/bin/grep 2212 "$T/st/cycle.log")"
! /usr/bin/grep -q ' 2211$' "$T/board.calls" && ok "the plain issue with the spec's identical facts keeps its Closed card" || ko "#2211 moved: $(cat "$T/board.calls")"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
