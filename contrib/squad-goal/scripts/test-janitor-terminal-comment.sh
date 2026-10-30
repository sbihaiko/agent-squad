#!/bin/bash
# Every terminal entry carries its condition in a comment (#245, spec #132): with SG_BOARD_VOCAB=shipped a move into Closed
# queues one comment naming the branch that is in dev and the test on its tip, and a move into Abandoned one naming
# who decided and why. A terminal write whose condition cannot be read is not made (the guard). A card that settled in a
# terminal column with no such comment gets one, once. Stubbed gh and board.sh; a temp state dir, never the live board.
# usage: bash test-janitor-terminal-comment.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$T/s/"
cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields|--clear-run) :;;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *--include*repos/x/y/branches/gui%2F2201-a*) echo '{"commit":{"sha":"abcdef1234567890"}}';;
  *--include*repos/x/y/branches/gui%2F2205-b*) echo '{"commit":{"sha":"5555555aaaaaaaa"}}';;
  *repos/x/y/branches*) printf 'gui/2201-a abcdef1234567890\ngui/2205-b 5555555aaaaaaaa\n';;
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
    printf '2201\tCode Complete\thttps://api.github.com/repos/x/y\n2202\tTodo\thttps://api.github.com/repos/x/y\n'
    printf '2203\tTodo\thttps://api.github.com/repos/x/y\n2204\tCode Complete\thttps://api.github.com/repos/x/y\n'
    printf '2205\tClosed\thttps://api.github.com/repos/x/y\n2206\tClosed\thttps://api.github.com/repos/x/y\n'
    printf '2208\tClosed\thttps://api.github.com/repos/x/y\n2209\tClosed\thttps://api.github.com/repos/x/y\n'
    printf '2210\tTodo\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    printf '{"number":2202,"title":"Issue 2202","body":"","state":"OPEN","labels":{"nodes":[{"name":"wontfix"}]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}'
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
run() { SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1; }
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
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
