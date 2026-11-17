#!/bin/bash
# Regression test for ADR-0277's "the loop can see what it spends": every call appends one line to $SG_STATE/api.log (timestamp,
# transport, route, cache hit or miss, outcome, spending pass), an exhausted cooldown and a sustained miss rate are health causes
# of their own, and the operator reads both in health's own output, alerts.log and health.json -- not only in the log file. It runs
# the real readers as processes (ghthrottle.py, ghread.py, ghcall.sh's doors) and the real health.sh against a stub `gh`, which is
# how the loop reaches them. Nothing here touches the network, a model or a board. usage: bash test-ghspend.sh
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAILED: $1"; fails=$((fails + 1)); }

for v in $(compgen -e | grep '^SG_'); do unset "$v"; done
unset HEALTH_DRY HEALTH_NOTIFY
mkdir -p "$T/bin" "$T/hbin" "$T/st/auto" "$T/co" "$T/s" "$T/home"
export SG_STATE="$T/st" HOME="$T/home" PATH="$T/bin:$PATH" STUB_DIR="$T"
cp "$here/ghread.py" "$here/ghthrottle.py" "$here/ghspend.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/health.sh" "$T/s/"
CLI="python3 $T/s/ghthrottle.py"; RD="python3 $T/s/ghread.py"
LOG="$T/st/api.log"

# The stub `gh`: it answers a body from $T/body, and it refuses with GitHub's own rate-limit shape while $T/ratelimited exists.
cat > "$T/bin/gh" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DIR/gh.log"
if [ -e "$STUB_DIR/ratelimited" ]; then printf 'HTTP/2.0 403 Forbidden\r\n\r\n{"message":"API rate limit exceeded for user ID 1.","status":"403"}'; exit 1; fi
printf 'HTTP/2.0 200 OK\r\n\r\n%s' "$(cat "$STUB_DIR/body" 2>/dev/null || echo '[]')"
STUB
chmod +x "$T/bin/gh"
printf '#!/bin/bash\nexit 0\n' > "$T/hbin/gh"; chmod +x "$T/hbin/gh"
printf '#!/bin/bash\nexit 0\n' > "$T/notify"; chmod +x "$T/notify"
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=7\n' "$T/st" "$T/co" > "$T/s/env.sh"
touch "$T/st/cycle.log" "$T/st/active-runs.txt"; git -C "$T/co" init -q 2>/dev/null
reset() { rm -rf "$T/st/gh-cache" "$T/st/gh-budget.json" "$T/st/gh-cooldown.json" "$LOG" "$LOG.1" "$T/ratelimited" "$T/st/auto/health-"* "$T/st/alerts.log" "$T/gh.log"; touch "$T/gh.log"; }
health() {   # one hermetic health pass; the live loop's processes are not this suite's business
  PATH="$T/hbin:$PATH" HEALTH_NOTIFY="$T/notify" SG_WATCH_CMD_RE="$T/s/watch\.sh\$" bash "$T/s/health.sh" 2>&1
}
SHAPE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z (REST|GraphQL) [^ ]+ (hit|miss) [a-z]+ [a-z0-9_-]+$'

echo "one line per call:"
reset
SG_GH_PASS=landing $CLI repos/o/r/labels >/dev/null; SG_GH_PASS=landing $CLI repos/o/r/labels >/dev/null
[ "$(wc -l < "$LOG" | tr -d ' ')" = 2 ] && ok "two reads, two lines" || bad "lines: $(cat "$LOG" 2>/dev/null)"
l1=$(sed -n 1p "$LOG"); l2=$(sed -n 2p "$LOG")
printf '%s\n%s\n' "$l1" "$l2" | grep -Eq "$SHAPE" && ok "the line is timestamp, transport, route, cache, outcome, pass" || bad "shape: $l1 / $l2"
case "$l1" in *" REST repos/o/r/labels miss answer landing") ok "the first read is a miss, and names the pass";; *) bad "first line: $l1";; esac
case "$l2" in *" REST repos/o/r/labels hit answer landing") ok "the repeat is a hit, with no request";; *) bad "second line: $l2";; esac
[ "$(wc -l < "$T/gh.log" | tr -d ' ')" = 1 ] && ok "and the hit spent no request" || bad "requests: $(cat "$T/gh.log")"
SG_GH_PASS=board $CLI "repos/o/r/pulls?state=open&per_page=100" >/dev/null
tail -1 "$LOG" | grep -Eq " GraphQL repos/o/r/pulls\?state=open&per_page=100 miss [a-z]+ board$" && ok "an aggregate route is logged as GraphQL" || bad "graphql: $(tail -1 "$LOG")"

echo "every door logs:"
reset
SG_GH_PASS=autobug $RD repos/o/r/issues/7 >/dev/null
tail -1 "$LOG" | grep -Eq " GraphQL repos/o/r/issues/7 miss answer autobug$" && ok "ghread.py, the unthrottled door, logs its read (one issue is one GraphQL query, #256)" || bad "ghread: $(tail -1 "$LOG" 2>/dev/null)"
SG_GH_PASS=autobug $RD --method PATCH repos/o/r/issues/7 -f state=closed >/dev/null
tail -1 "$LOG" | grep -Eq " REST repos/o/r/issues/7 write done autobug$" && ok "a write through ghread.py logs, and names the outcome done" || bad "write: $(tail -1 "$LOG")"
(. "$T/s/ghcall.sh"; SG_GH_PASS=frontier gh_get -H "Accept: application/vnd.github.diff" repos/o/r/pulls/7 >/dev/null)
tail -1 "$LOG" | grep -Eq " REST repos/o/r/pulls/7 miss answer frontier$" && ok "gh_get logs the route, not the header" || bad "gh_get: $(tail -1 "$LOG")"
(. "$T/s/ghcall.sh"; SG_GH_PASS=board gh_send --method POST repos/o/r/issues/7/comments -f body=x >/dev/null)
tail -1 "$LOG" | grep -Eq " REST repos/o/r/issues/7/comments write done board$" && ok "gh_send logs the write" || bad "gh_send: $(tail -1 "$LOG")"
$RD repos/o/r/issues/8 >/dev/null
tail -1 "$LOG" | grep -Eq " unattributed$" && ok "a caller that names no pass is unattributed" || bad "no pass: $(tail -1 "$LOG")"
touch "$T/ratelimited"; SG_GH_PASS=board $RD repos/o/r/issues/9 >/dev/null
tail -1 "$LOG" | grep -Eq " GraphQL repos/o/r/issues/9 miss refused board$" && ok "a refusal is a refused outcome" || bad "refusal: $(tail -1 "$LOG")"

echo "a GraphQL read logs through the helper:"
reset
printf '%s' '{"data":{"repository":{"x":1}}}' > "$T/body"
SG_GH_PASS=board python3 -c "import sys; sys.path.insert(0, '$T/s'); import ghread; print(ghread.graphql_read('query{x}').kind)" >/dev/null
tail -1 "$LOG" 2>/dev/null | grep -Eq " GraphQL graphql miss answer board$" && ok "graphql_read logs its answer as a GraphQL read, with the pass that spent it" || bad "graphql_read: $(tail -1 "$LOG" 2>/dev/null)"
touch "$T/ratelimited"; SG_GH_PASS=board python3 -c "import sys; sys.path.insert(0, '$T/s'); import ghread; print(ghread.graphql_read('query{x}').kind)" >/dev/null; rm -f "$T/ratelimited"
tail -1 "$LOG" 2>/dev/null | grep -Eq " GraphQL graphql miss refused board$" && ok "a refused GraphQL read is logged as refused" || bad "graphql refusal: $(tail -1 "$LOG" 2>/dev/null)"
(. "$T/s/ghcall.sh"; SG_GH_PASS=hub gh_get graphql -f query=x >/dev/null)
tail -1 "$LOG" 2>/dev/null | grep -Eq " GraphQL graphql miss answer hub$" && ok "a shell door's GraphQL read is logged as GraphQL" || bad "shell graphql: $(tail -1 "$LOG" 2>/dev/null)"

echo "each pass names itself:"
for pair in startability.sh:landing autofrontier.sh:frontier autobug.sh:autobug board.sh:board; do
  s=${pair%%:*}; p=${pair##*:}
  grep -Eq "^export SG_GH_PASS=$p( |$)" "$here/$s" && ok "$s spends as the $p pass" || bad "$s does not export SG_GH_PASS=$p"
done

echo "an exhausted cooldown is a health cause:"
reset
touch "$T/ratelimited"; SG_GH_PASS=landing $CLI repos/o/r/issues/9 >/dev/null; rc=$?
[ "$rc" = 2 ] && ok "the refusal is exit 2" || bad "rc $rc"
jq -e '.pass == "landing"' "$T/st/gh-cooldown.json" >/dev/null && ok "the cooldown file records the pass that wrote it" || bad "cooldown: $(cat "$T/st/gh-cooldown.json")"
until=$(jq -r '.until|floor' "$T/st/gh-cooldown.json"); iso=$(date -u -r "$until" '+%Y-%m-%dT%H:%M:%SZ'); hm=$(date -u -r "$until" '+%H:%M:%SZ')
SG_GH_PASS=autobug $CLI repos/o/r/issues/10 >/dev/null
o=$(health)
printf '%s' "$o" | grep -F 'HEALTH gh-cooldown:' | grep -qF "until $iso" && ok "health names when the cooldown expires" || bad "expiry not named ($iso): $o"
printf '%s' "$o" | grep -F 'HEALTH gh-cooldown:' | grep -qF "owned by the landing pass" && ok "and the pass that owns it, not the one held back" || bad "owner not named: $o"
grep -F 'HEALTH gh-cooldown:' "$T/st/alerts.log" | grep -qF "until $iso" && ok "alerts.log carries it" || bad "alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
jq -e '.ok == false and (.causes | index("gh-cooldown")) and (.briefs["gh-cooldown"] == "until '"$hm"', owned by landing")' "$T/st/health.json" >/dev/null && ok "health.json carries the cause and a brief for the operator's footer" || bad "health.json: $(cat "$T/st/health.json")"
o=$(health); printf '%s' "$o" | grep -q 'HEALTH gh-cooldown:' && bad "the same cooldown alerted twice" || ok "the same cooldown alerts once"
jq '.until = 1 | .at = 1' "$T/st/gh-cooldown.json" > "$T/cd" && mv "$T/cd" "$T/st/gh-cooldown.json"
o=$(health); printf '%s' "$o" | grep -q 'HEALTH gh-cooldown cleared' && ok "an expired cooldown clears the cause" || bad "not cleared: $o"

echo "a sustained miss rate is a health cause:"
reset; rm -f "$T/ratelimited"
for _ in $(seq 1 25); do SG_GH_CACHE_WINDOW=0 SG_GH_PASS=frontier $CLI repos/o/r/labels >/dev/null; done
o=$(health)
printf '%s' "$o" | grep -F 'HEALTH gh-miss-rate:' | grep -qF '25 of 25' && ok "health names the misses and the reads" || bad "miss rate: $o"
printf '%s' "$o" | grep -F 'HEALTH gh-miss-rate:' | grep -qF 'frontier' && ok "and the pass that missed most" || bad "pass not named: $o"
grep -q 'HEALTH gh-miss-rate:' "$T/st/alerts.log" && ok "alerts.log carries it" || bad "alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
jq -e '(.causes | index("gh-miss-rate")) and .briefs["gh-miss-rate"] == "100% of 25 reads missed"' "$T/st/health.json" >/dev/null && ok "health.json carries it for the operator's footer" || bad "health.json: $(cat "$T/st/health.json")"
reset
for _ in $(seq 1 25); do SG_GH_PASS=frontier $CLI repos/o/r/labels >/dev/null; done
o=$(health); printf '%s' "$o" | grep -q 'HEALTH gh-miss-rate' && bad "a snapshot that answers the repeats alerted: $o" || ok "a healthy snapshot is not a cause"
reset
for _ in $(seq 1 25); do SG_GH_PASS=landing $CLI repos/o/r/issues/7 >/dev/null; done
o=$(health); printf '%s' "$o" | grep -q 'HEALTH gh-miss-rate' && bad "reads that gate a write are live by design and alerted: $o" || ok "live reads are never a miss rate"

echo
[ "$fails" -eq 0 ] && { echo "ghspend: all cases passed"; exit 0; }
echo "ghspend: $fails case(s) FAILED"; exit 1
