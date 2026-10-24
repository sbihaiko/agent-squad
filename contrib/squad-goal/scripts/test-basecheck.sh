#!/bin/bash
# Regression test for basecheck.sh (#786): a red completed check on the base branch head holds the landings of branchland.sh and
# wave.sh, is reported once per head (alerts.log and one queued comment on the issue landed at that head), and a pending or missing
# check does not block. Stub gh (every argv logged), a bare local remote, a temp state dir per case. One line per case.
# usage: bash test-basecheck.sh [scripts dir]
unset SG_SERIAL_HELD SG_BASE_CI_GATE   # an ambient gate or a live loop setting must not decide these cases
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
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
# run <state name> [VAR=value ...]: the driver in a fresh state dir; sets OUT and RC, the state dir is $T/st-<name>.
run() { local n=$1; shift; export STATE="$T/st-$n"; mkdir -p "$STATE/auto"; : > "$T/gh.log"; OUT=$(env "$@" bash "$T/s/driver.sh" 2>&1); RC=$?; }
landed() { mkdir -p "$T/st-$1"; printf '1700000000\tgui/7-x\t%s\t7\n' "$SHA" > "$T/st-$1/landed.tsv"; }

# A green head does not block, and its completed answer is cached under auto/basecheck-<sha>.
landed green; run green CHECKS="$GREEN"
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a green base head does not block" || ko "green: rc $RC out [$OUT]"
[ -e "$T/st-green/auto/basecheck-$SHA" ] && ok "a completed green answer is cached" || ko "no cache for the green head"
run green CHECKS="$GREEN"; n=$(/usr/bin/grep -c '/check-runs' "$T/gh.log")
[ "$n" = 0 ] && ok "a cached answer is not read again" || ko "the cached head was read $n times"

# A red completed check blocks, names the base and the check, and is reported once per head.
landed red; run red CHECKS="$RED"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && ok "a red base head blocks and names the check" || ko "red: rc $RC out [$OUT]"
[ "$(wc -l < "$T/st-red/alerts.log" 2>/dev/null | tr -d ' ')" = 1 ] && ok "the red head raises one alerts.log line" || ko "alerts.log: $(cat "$T/st-red/alerts.log" 2>/dev/null)"
[ "$(ls "$T/st-red/cq" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && /usr/bin/grep -q '#7-' <(ls "$T/st-red/cq") && ok "one queued comment on the issue landed at that head" || ko "cq: $(ls "$T/st-red/cq" 2>/dev/null)"
run red CHECKS="$RED"
[ "$RC" = 0 ] && ok "the head stays red for the next landing" || ko "second red call: rc $RC"
[ "$(wc -l < "$T/st-red/alerts.log" | tr -d ' ')" = 1 ] && [ "$(ls "$T/st-red/cq" | wc -l | tr -d ' ')" = 1 ] && ok "the red head is reported once, not per call" || ko "a repeat report: alerts [$(cat "$T/st-red/alerts.log")] cq [$(ls "$T/st-red/cq")]"

# No landed row at that head: the last landed issue carries the comment.
mkdir -p "$T/st-last"; printf '1700000000\tgui/5-x\tdeadbeef\t5\n1700000100\tgui/9-x\tcafef00d\t9\n' > "$T/st-last/landed.tsv"; run last CHECKS="$RED"
[ "$(ls "$T/st-last/cq" 2>/dev/null | /usr/bin/grep -c '#9-')" = 1 ] && ok "without a landed row at the head, the last landed issue is commented" || ko "last-landed cq: $(ls "$T/st-last/cq" 2>/dev/null)"

# A pending check and a missing check do not block; neither is cached.
run pending CHECKS="$RUNNING"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-pending/auto/basecheck-$SHA" ] && ok "a pending check does not block and is not cached" || ko "pending: rc $RC out [$OUT]"
run missing
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a head with no check runs does not block" || ko "missing: rc $RC out [$OUT]"
run unreadable GHFAIL=1
[ "$RC" = 1 ] && ok "an unreadable check list does not block" || ko "unreadable: rc $RC out [$OUT]"

# SG_BASE_CI_GATE=0 turns the whole check off, red or not.
run off CHECKS="$RED" SG_BASE_CI_GATE=0
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-off/alerts.log" ] && ok "SG_BASE_CI_GATE=0 turns it off" || ko "off: rc $RC out [$OUT]"

# The callers hold their landing while the base is red (branchland.sh and wave.sh).
/usr/bin/grep -q 'sg_base_red' "$d/branchland.sh" && /usr/bin/grep -q 'sg_base_red' "$d/wave.sh" && ok "branchland.sh and wave.sh ask the base check before a landing" || ko "a landing script does not call sg_base_red"
[ "$fail" = 0 ] && echo "PASS test-basecheck" || { echo "FAIL test-basecheck"; exit 1; }
