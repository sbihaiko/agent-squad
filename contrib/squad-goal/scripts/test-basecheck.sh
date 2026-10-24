#!/bin/bash
# Regression test for basecheck.sh (#786): a red completed check on the base branch head holds the landings of branchland.sh and
# wave.sh, is reported once per head (one alerts.log line; one queued comment on each issue whose landed row names that head), a
# red answer is read again after SG_BASE_CI_RECHECK, a green one is kept only once its checks are older than SG_BASE_CI_GRACE, a
# cache that is not a whole answer is read again, and a pending, missing or unreadable check does not block. The callers' holds are
# proved by the fixture runs of test-branchland.sh and test-wave.sh. Stub gh (every argv logged), a bare local remote, a temp state
# dir per case. One line per case.
# usage: bash test-basecheck.sh [scripts dir]
unset SG_SERIAL_HELD SG_BASE_CI_GATE SG_BASE_CI_RECHECK SG_BASE_CI_GRACE   # an ambient gate or loop setting must not decide these cases
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/bin" "$T/co"
cp "$d/basecheck.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/address.sh" "$T/s/"
$G init -q -b dev "$T/co" && (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git" 2>/dev/null
SHA=$($G -C "$T/origin.git" rev-parse refs/heads/dev); S9=${SHA:0:9}
# The stub gh: check-runs answers CHECKS (GHFAIL makes the read fail); every other route answers an empty list.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"/check-runs"*) [ -n "\${GHFAIL:-}" ] && exit 1; echo "\${CHECKS:-{\"check_runs\":[]\}}";;
  *) echo "[]";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The driver: one call of sg_base_red for dev, in the state directory the case names.
cat > "$T/s/driver.sh" <<J
#!/bin/bash
export SG_STATE=\$STATE SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 SG_LAND_REMOTE=$T/origin.git SG_GHREAD="python3 $T/s/ghthrottle.py" SG_GH_PASS=basecheck
. "$T/s/ghcall.sh"; . "$T/s/address.sh"; . "$T/s/basecheck.sh"
sg_base_red dev
J
# Completed checks carry completed_at: the green answer is kept only when that is old enough (SG_BASE_CI_GRACE, default 900 s).
NOW=$(date -u +%FT%TZ)
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
FRESH='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"'"$NOW"'"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure","completed_at":"2001-01-01T00:00:00Z"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
# run <state name> [VAR=value ...]: the driver in a fresh state dir; sets OUT and RC, the state dir is $T/st-<name>.
run() { local n=$1; shift; export STATE="$T/st-$n"; mkdir -p "$STATE/auto"; : > "$T/gh.log"; OUT=$(env "$@" bash "$T/s/driver.sh" 2>&1); RC=$?; }
landed() { mkdir -p "$T/st-$1"; printf '1700000000\tgui/7-x\t%s\t7\n' "$SHA" > "$T/st-$1/landed.tsv"; }
reads() { /usr/bin/grep -c '/check-runs' "$T/gh.log"; }
alerts() { /usr/bin/grep -c 'BASE CI' "$T/st-$1/alerts.log" 2>/dev/null; }
cqs() { ls "$T/st-$1/cq" 2>/dev/null | /usr/bin/grep -c "$2"; }

# A green head does not block. Its checks are old enough, so the answer is cached as "ok <epoch>" and not read again.
landed green; run green CHECKS="$GREEN"
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a green base head does not block" || ko "green: rc $RC out [$OUT]"
/usr/bin/grep -qE "^ok [0-9]+$" "$T/st-green/auto/basecheck-$SHA" 2>/dev/null && ok "an old green answer is cached as ok" || ko "no ok cache: $(cat "$T/st-green/auto/basecheck-$SHA" 2>/dev/null)"
run green CHECKS="$GREEN"
[ "$(reads)" = 0 ] && ok "a cached green answer is not read again" || ko "the cached head was read $(reads) times"
# A green whose checks finished just now is not cached: a late job can still appear, so the head is read again.
landed fresh; run fresh CHECKS="$FRESH"
[ ! -e "$T/st-fresh/auto/basecheck-$SHA" ] && ok "a green answer whose checks are fresh is not cached" || ko "a fresh green was cached: $(cat "$T/st-fresh/auto/basecheck-$SHA")"

# A red completed check blocks, names the base and the check, is reported once, and is commented on the issue landed at that head.
landed red; run red CHECKS="$RED"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && ok "a red base head blocks and names the check" || ko "red: rc $RC out [$OUT]"
[ "$(alerts red)" = 1 ] && ok "the red head raises one alerts.log line" || ko "alerts.log: $(cat "$T/st-red/alerts.log" 2>/dev/null)"
[ "$(cqs red '#7-basecheck')" = 1 ] && ok "one queued comment on the issue landed at that head" || ko "cq: $(ls "$T/st-red/cq" 2>/dev/null)"
/usr/bin/grep -qx "red [0-9]* check (3.11) ended failure" "$T/st-red/auto/basecheck-$SHA" && ok "the red answer is cached as red, with its read time" || ko "red cache: $(cat "$T/st-red/auto/basecheck-$SHA" 2>/dev/null)"
/usr/bin/grep -q 'per_page=100&filter=latest' "$T/gh.log" && ok "the check runs are read with per_page=100 and filter=latest" || ko "the route: $(cat "$T/gh.log")"
# Within SG_BASE_CI_RECHECK the red answer is kept: no read, no second line, no second comment.
run red CHECKS="$RED"
[ "$RC" = 0 ] && [ "$(reads)" = 0 ] && ok "a red answer inside SG_BASE_CI_RECHECK is not read again" || ko "red again: rc $RC reads $(reads)"
[ "$(alerts red)" = 1 ] && [ "$(cqs red '#7-basecheck')" = 1 ] && ok "the red head is reported once, not per call" || ko "a repeat report: alerts [$(cat "$T/st-red/alerts.log")] cq [$(ls "$T/st-red/cq")]"

# Once SG_BASE_CI_RECHECK has passed, a red answer is read again. A job the owner re-ran turns green, and the head stops blocking.
run red CHECKS="$GREEN" SG_BASE_CI_RECHECK=0
[ "$RC" = 1 ] && [ "$(reads)" = 1 ] && ok "an expired red answer is read again, and a green re-run releases the head" || ko "re-run: rc $RC reads $(reads) out [$OUT]"
# A red that stays red after the window is read again, and still reported once.
run red2 CHECKS="$RED"
run red2 CHECKS="$RED" SG_BASE_CI_RECHECK=0
[ "$RC" = 0 ] && [ "$(reads)" = 1 ] && [ "$(alerts red2)" = 1 ] && ok "a red still red after the window is read again, reported once" || ko "red window: rc $RC reads $(reads) alerts $(alerts red2)"

# A cache that is not a whole answer is no answer: a partial line from a killed write is read again, and never holds the landings.
landed partial; mkdir -p "$T/st-partial/auto"; printf 'check (3.11) ended fail' > "$T/st-partial/auto/basecheck-$SHA"
run partial CHECKS="$GREEN"
[ "$(reads)" = 1 ] && [ "$RC" = 1 ] && ok "a partial cache line is no answer: the head is read again" || ko "partial: reads $(reads) rc $RC out [$OUT]"
landed torn; mkdir -p "$T/st-torn/auto"; printf 'red 99999999999' > "$T/st-torn/auto/basecheck-$SHA"
run torn CHECKS="$RED"
[ "$RC" = 0 ] && [ "$(reads)" = 1 ] && ok "a torn red line (no name) is no answer: the head is read again" || ko "torn: reads $(reads) rc $RC out [$OUT]"
# A write goes through a temporary file that is renamed: no tmp file is left in the state directory.
[ -z "$(ls "$T/st-red/auto" | /usr/bin/grep '\.tmp\.')" ] && ok "the cache is written by one rename: no temporary file is left" || ko "a temporary file is left: $(ls "$T/st-red/auto")"

# No landed row at that head: nothing is commented on an unrelated issue; the alerts.log line is still written.
mkdir -p "$T/st-last"; printf '1700000000\tgui/5-x\tdeadbeef\t5\n1700000100\tgui/9-x\tcafef00d\t9\n' > "$T/st-last/landed.tsv"; run last CHECKS="$RED"
[ "$(alerts last)" = 1 ] && [ "$(ls "$T/st-last/cq" 2>/dev/null | wc -l | tr -d ' ')" = 0 ] && ok "without a landed row at the head, only alerts.log is written" || ko "last: alerts $(alerts last) cq $(ls "$T/st-last/cq" 2>/dev/null)"

# A pending check and a missing check do not block; neither is cached.
run pending CHECKS="$RUNNING"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-pending/auto/basecheck-$SHA" ] && ok "a pending check does not block and is not cached" || ko "pending: rc $RC out [$OUT]"
run missing
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a head with no check runs does not block" || ko "missing: rc $RC out [$OUT]"

# An unreadable check list does not block, and is named once per head on alerts.log.
run unreadable GHFAIL=1
[ "$RC" = 1 ] && ok "an unreadable check list does not block" || ko "unreadable: rc $RC out [$OUT]"
[ "$(/usr/bin/grep -c 'is not read' "$T/st-unreadable/alerts.log" 2>/dev/null)" = 1 ] && ok "an unreadable read is named on alerts.log" || ko "no unreadable line: $(cat "$T/st-unreadable/alerts.log" 2>/dev/null)"
run unreadable GHFAIL=1
[ "$(/usr/bin/grep -c 'is not read' "$T/st-unreadable/alerts.log" 2>/dev/null)" = 1 ] && ok "an unreadable read is named once per head" || ko "unreadable repeat: $(cat "$T/st-unreadable/alerts.log" 2>/dev/null)"

# SG_BASE_CI_GATE=0 turns the whole check off, red or not.
run off CHECKS="$RED" SG_BASE_CI_GATE=0
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-off/alerts.log" ] && ok "SG_BASE_CI_GATE=0 turns it off" || ko "off: rc $RC out [$OUT]"

# The shared classifier: red, running, green, none (zero runs) and not-a-list, as promote-stg.sh reads them.
c() { printf '%s' "$1" | bash -c '. "$1"; sg_ci_class' _ "$T/s/basecheck.sh" 2>/dev/null; }
[ "$(c "$RED")" = red ] && [ "$(c "$RUNNING")" = running ] && [ "$(c "$GREEN")" = green ] && [ "$(c '{"check_runs":[]}')" = none ] && ok "the classifier reads red, running, green and none" || ko "classifier: [$(c "$RED")] [$(c "$RUNNING")] [$(c "$GREEN")] [$(c '{"check_runs":[]}')]"
[ -z "$(c '{}')" ] && ok "the classifier names no state for an answer that is not a check-run list" || ko "classifier on {}: [$(c '{}')]"
bash -c '. "$1"; r=$(printf "%s" "$2" | sg_ci_class) || r=none; [ "$r" = red ] && [ "$(printf "%s" "$2" | sg_ci_bad)" = "check (3.11) ended failure" ]' _ "$T/s/basecheck.sh" "$RED" && ok "the classifier names the first red check" || ko "sg_ci_bad: [$(printf '%s' "$RED" | bash -c '. "$1"; sg_ci_bad' _ "$T/s/basecheck.sh")]"

[ "$fail" = 0 ] && echo "PASS test-basecheck" || { echo "FAIL test-basecheck"; exit 1; }
