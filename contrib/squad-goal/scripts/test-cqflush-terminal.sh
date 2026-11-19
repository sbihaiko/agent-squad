#!/bin/bash
# seconds: 1.70
# A queued Closed comment is checked again at flush time (#777): the janitor names it <owner>__<repo>#<n>-terminal-closed-<pid>.md.
# cqflush.sh reads the issue state before it posts one; an issue open again drops the comment with a log line, and a closed one
# is posted. Another terminal comment is posted with no read. Offline: fixture profile, stub gh that answers the state from a file.
# usage: bash test-cqflush-terminal.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain" "$B/alpha/cq"
printf ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' "$T/co" > "$P/alpha.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# the issue read reaches gh as REST or as the door's batched graphql: both answer the state in $ISTATE (fail: exit 1)
all="$*"
case "$all" in
  *issues/*/comments*) echo "POST $all" >> "$GHLOG"; echo '{"id":1}';;
  *graphql*issue\(number:*) echo "READ" >> "$GHLOG"; s=$(cat "$ISTATE"); [ "$s" = fail ] && exit 1
    S=$(printf '%s' "$s" | tr a-z A-Z)
    printf 'HTTP/2.0 200 OK\r\n\r\n{"data":{"repository":{"i9":{"number":9,"title":"t","body":"","state":"%s","labels":{"nodes":[]},"blockedBy":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}}}}' "$S";;
  *repos/x/alpha/issues/[0-9]*) echo "READ" >> "$GHLOG"; s=$(cat "$ISTATE"); [ "$s" = fail ] && exit 1; printf '{"state":"%s"}' "$s";;
esac
GH
chmod +x "$T/bin/gh"
export HOME=$T/home; mkdir -p "$T/home"; export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog ISTATE=$T/istate PATH="$T/bin:$PATH"
unset SG_TOOLING_REPO SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT
put() { printf 'Closed: done\n' > "$B/alpha/cq/$1"; touch -t 202601010000 "$B/alpha/cq/$1"; }
flush() { : > "$T/ghlog"; (cd "$T/plain" && bash "$d/cqflush.sh" --once 2>&1); }

# 1. the issue is open again: the Closed comment is dropped, logged, and not posted
echo open > "$T/istate"; f='x__alpha#9-terminal-closed-11.md'; put "$f"
out=$(flush); ! $G -q POST "$T/ghlog" && $G -q READ "$T/ghlog" && [ ! -e "$B/alpha/cq/$f" ] && printf '%s' "$out" | $G -qF "DROPPED x/alpha#9 $f: the issue is open again" \
  && ok "a Closed comment on a reopened issue is dropped with a log line" || ko "reopened: $(cat "$T/ghlog") / $out"

# 2. the issue is still closed: the Closed comment is posted
echo closed > "$T/istate"; f='x__alpha#9-terminal-closed-12.md'; put "$f"
out=$(flush); $G -q POST "$T/ghlog" && [ -e "$B/alpha/cq/$f.done" ] && ok "a Closed comment on a closed issue is posted" || ko "closed: $(cat "$T/ghlog") / $out"

# 3. the state cannot be read: nothing is posted, and the comment stays queued for a retry
echo fail > "$T/istate"; f='x__alpha#9-terminal-closed-13.md'; put "$f"
out=$(flush); ! $G -q POST "$T/ghlog" && [ -e "$B/alpha/cq/$f" ] && ok "an unread state keeps the comment queued" || ko "unread: $(cat "$T/ghlog") / $out"
rm -f "$B/alpha/cq/$f" "$B/alpha/cq/$f.tries"

# 4. another terminal comment (Done) is posted with no state read
echo open > "$T/istate"; f='x__alpha#9-terminal-14.md'; put "$f"
out=$(flush); $G -q POST "$T/ghlog" && ! $G -q READ "$T/ghlog" && ok "a non-Closed terminal comment is posted with no read" || ko "done: $(cat "$T/ghlog") / $out"

# 5. the janitor names a queued Closed comment with the tag the flush checks
$G -q 'terminal-closed' "$d/janitor.py" && ok "the janitor tags a Closed comment terminal-closed" || ko "janitor tag missing"
rm -rf "$T"; exit $fail
