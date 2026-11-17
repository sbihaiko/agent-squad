#!/bin/bash
# One defect, one issue: the fingerprint is the cause, never the occurrence (#139).
#
# `autorefuse.sh` files ONE bug for one refused-write CAUSE, never one per occurrence. The fingerprint is the
# operation, the address and the error's own first line, so the run id, the timestamp and the cycle number can never
# enter it. The index is `$SG_STATE/auto/refusal-<fingerprint>`, one line `<issue> <state>`: a cause whose issue is open
# files nothing, a cause whose issue is closed files again, because a defect that came back is a defect, and the file
# survives a restart, so a restart does not re-file every open defect. Offline: a stub gh, a fixture profile and a temp
# state dir; nothing here touches the network.
# usage: bash test-autorefuse.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
trap 'rm -rf "$T"' EXIT
unset SG_STATE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_PROFILES_DIR SG_FILED_BY SG_FILED_LABELS SG_GH_PASS

N=$T/loop; S=$N/scripts; mkdir -p "$S" "$N/st/auto" "$N/bin" "$N/co"
for f in autorefuse.sh autohunt.sh ghcall.sh drainpolicy.py ghread.py refusal-names.sh; do [ -f "$d/$f" ] && cp "$d/$f" "$S/$f"; done
# the profile of the fixture: one repository, this state dir, the board stubbed (the card is not what this suite proves)
cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$N/st; SG_CHECKOUT=$N/co; SG_BASE=main
SG_CODING_RULES=rules; BOARD=true
E
# the stub gh: a filing write answers a number (or GitHub's refusal while $GHREFUSE exists), a single-issue read answers
# the state in $GHSTATE, and every call lands in the log so a case can count the filings that were made.
cat > "$N/bin/gh" <<'GH'
#!/bin/bash
printf '%s\n' "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *"--method POST"*)
    for a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "${GHBODIES:-/dev/null}";; esac; done
    [ -e "${GHREFUSE:-/nonexistent}" ] && { cat "$GHTEXT"; exit 1; }
    printf '{"number":%s}\n' "${GHNEW:-42}";;
  *"issues/"*)
    [ -e "${GHREFUSE:-/nonexistent}" ] && { cat "$GHTEXT"; exit 1; }
    printf '{"state":"%s"}\n' "$(cat "${GHSTATE:-/nonexistent}" 2>/dev/null || echo open)";;
  *graphql*"issue(number:"*)   # a single-issue read goes through one GraphQL request since #256; GitHub spells the state in capitals
    [ -e "${GHREFUSE:-/nonexistent}" ] && { cat "$GHTEXT"; exit 1; }
    n=$(printf '%s' "$*" | grep -o 'issue(number: [0-9]*' | head -1 | tr -dc 0-9)
    st=$(cat "${GHSTATE:-/nonexistent}" 2>/dev/null || echo open)
    printf '{"data":{"repository":{"i%s":{"number":%s,"state":"%s"}}}}\n' "$n" "$n" "$(printf '%s' "$st" | tr a-z A-Z)";;
  *) printf '{}\n';;
esac
GH
chmod +x "$N/bin/gh" 2>/dev/null
export PATH="$N/bin:$PATH" GHLOG=$N/gh.log GHBODIES=$N/bodies.log GHNEW=42 GHSTATE=$N/state
export GHREFUSE=$N/refuse GHTEXT=$N/refusal-text
: > "$GHLOG"; : > "$GHBODIES"; echo open > "$GHSTATE"; rm -f "$GHREFUSE"; : > "$GHTEXT"

# refuse <operation> <address> <error>: one occurrence of the refusal, filed the way the pass that hit it asks for it --
# its own process, its own shell, the fixture's state dir.
refuse() { ( cd "$N/co" && bash "$S/autorefuse.sh" --refused "$@" 2>&1 ); }
# fingerprint <operation> <address> <error>: the cause's key alone, nothing read and nothing filed
fingerprint() { ( cd "$N/co" && bash "$S/autorefuse.sh" --fingerprint "$@" 2>/dev/null ); }
posts() { $G -c -- '--method POST' "$GHLOG" 2>/dev/null; }   # one filing write is one POST call
said() { case "$1" in *"$2"*) return 0;; *) return 1;; esac; }

ADDR=repos/o/r/issues/77
# the SAME cause, two occurrences: the error's first line is the cause, the run id and the cycle behind it are not
E1='gh: Content already exists in this project (HTTP 422)
run 20261013-192808 cycle 3'
E2='gh: Content already exists in this project (HTTP 422)
run 20261013-193500 cycle 9'
E3='gh: Server Error (HTTP 500)'                         # another cause: the error's own first line changed

# 1. one refusal files one issue for its cause, once
o=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E1")
if said "$o" "REFUSE filed #42"; then ok "a refused write files one bug for its cause"; else ko "the refusal filed nothing: '$o'"; fi
if [ "$(posts)" = 1 ]; then ok "the filing is one write"; else ko "the filing made $(posts) write(s)"; fi

# 2. the same cause, another occurrence: one issue, and the second occurrence files nothing
o=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E2")
if [ "$(posts)" = 1 ]; then ok "two refusals with one fingerprint file one issue"; else ko "the second occurrence filed: $(posts) write(s)"; fi
if said "$o" "REFUSE open #42"; then ok "the second occurrence files nothing"; else ko "the second occurrence files nothing: '$o'"; fi

# 3. a changed error message is a new fingerprint and files its own issue
export GHNEW=43; o=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E3"); export GHNEW=42
if [ "$(posts)" = 2 ] && said "$o" "REFUSE filed #43"; then ok "a changed error message is a new fingerprint and files its own issue"
else ko "the changed error did not file its own issue: $(posts) write(s), '$o'"; fi

# 4. the index is a file in the loop's state directory, holding the issue number and the state it was filed in
f1=$(fingerprint BOARD-WRITE-FAILED "$ADDR" "$E1")
idx="$N/st/auto/refusal-$f1"
if [ -f "$idx" ]; then read -r n st < "$idx"; else n=; st=; fi
if [ "$n" = 42 ] && [ "$st" = open ]; then ok "the index holds the issue number and the state it was filed in"
else ko "the index '$idx' is '${n:-missing} ${st:-} ', not '42 open'"; fi

# 5. a restart re-files nothing while the defects' issues are open: every call below is a fresh process reading that file
before=$(posts)
o1=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E2"); o2=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E3")
if [ "$(posts)" = "$before" ]; then ok "the index survives a restart, so a restart does not re-file every open defect"
else ko "a restart re-filed $(( $(posts) - before )) open defect(s)"; fi

# 6. a fingerprint whose issue is closed files again, because a defect that came back is a defect
echo closed > "$GHSTATE"
export GHNEW=44; o=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E1"); export GHNEW=42
if [ "$(posts)" = "$(( before + 1 ))" ] && said "$o" "REFUSE refile #42"; then ok "a fingerprint whose issue is closed files again"
else ko "the closed issue was not filed again: $(posts) write(s), '$o'"; fi
echo open > "$GHSTATE"

# 7. a state the loop cannot read files nothing: a refusal is not an answer
printf '%s' '{"message":"API rate limit exceeded for user ID 122891372. Include the request ID 06F8:34CAED:22CD6B.","documentation_url":"https://docs.github.com/rest","status":"403"}' > "$GHTEXT"
: > "$GHREFUSE"
before=$(posts); o=$(refuse BOARD-WRITE-FAILED "$ADDR" "$E3")
if [ "$(posts)" = "$before" ] && said "$o" "REFUSE unreadable"; then ok "a state the loop cannot read files nothing"
else ko "an unreadable state filed $(( $(posts) - before )) issue(s): '$o'"; fi
rm -f "$GHREFUSE"

# 8. the fingerprint is the cause: the operation, the address and the error's own first line, and nothing else
a=$(fingerprint BOARD-WRITE-FAILED "$ADDR" "$E1"); b=$(fingerprint BOARD-WRITE-FAILED "$ADDR" "$E2")
c=$(fingerprint BOARD-WRITE-FAILED "$ADDR" "$E3"); e=$(fingerprint DISPATCH-FAILED "$ADDR" "$E1")
f=$(fingerprint BOARD-WRITE-FAILED repos/o/r/issues/78 "$E1")
if [ -n "$a" ] && [ "$a" = "$b" ]; then ok "the run id, the timestamp and the cycle number are outside the fingerprint"
else ko "the occurrence entered the fingerprint: '$a' vs '$b'"; fi
if [ "$a" != "$c" ]; then ok "a changed error's first line is a changed fingerprint"; else ko "two different errors share a fingerprint: '$a'"; fi
if [ "$a" != "$e" ]; then ok "a changed operation is a changed fingerprint"; else ko "two operations share a fingerprint: '$a'"; fi
if [ "$a" != "$f" ]; then ok "a changed address is a changed fingerprint"; else ko "two addresses share a fingerprint: '$a'"; fi

# 9. the issue the loop files names the cause: the operation, the address and the error's first line, under the prefix
if $G -qF 'title=[SQUAD] refused write BOARD-WRITE-FAILED repos/o/r/issues/77: gh: Content already exists in this project (HTTP 422)' "$GHLOG"; then
  ok "the issue is titled with the operation, the address and the error's first line"
else ko "the title does not name the cause: $(head -2 "$GHLOG")"; fi
if $G -qF "refusal-$f1" "$GHBODIES" && $G -qF 'operation:   BOARD-WRITE-FAILED' "$GHBODIES"; then
  ok "the body the loop files carries the cause's fingerprint and its operation"
else ko "the body does not carry the cause: $(head -3 "$GHBODIES")"; fi

# 10. (#142) the refusal names are a closed set in code: the set is readable in one place, every filer call names a
# member, and a name outside the set is refused by the filer -- never filed under a guessed name. CARD-WRITE-FAILED is
# the drift spelling #117 warns about; it files nothing.
set=$(bash "$S/refusal-names.sh" 2>/dev/null)
for n in BOARD-WRITE-FAILED DISPATCH-FAILED REFUSAL-COMMENT-FAILED; do
  if $G -qxF "$n" <<< "$set"; then ok "the closed set is readable in one place and holds $n"; else ko "the set does not hold $n: '$set'"; fi
done
before=$(posts); o=$(refuse CARD-WRITE-FAILED "$ADDR" "$E1" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && [ "$(posts)" = "$before" ]; then ok "a name outside the closed set is refused, and nothing is filed"
else ko "a name outside the set filed $(( $(posts) - before )) issue(s) (rc $rc): '$o'"; fi
if said "$o" CARD-WRITE-FAILED; then ok "the filer names the name it would not take"; else ko "the filer does not name it: '$o'"; fi
um=""
while read -r n; do [ -n "$n" ] && { fingerprint "$n" "$ADDR" "$E1" >/dev/null 2>&1 || um="$um $n"; }; done <<< "$set"
[ -z "$um" ] && ok "every name in the set is a name the filer takes" || ko "the filer refused a member:$um"

# 9. a chain is one cause (#308, #311, #315): each link names the previous bug's number in the address and in the error,
# and the error's text starts with the seam's "items read N" diagnostic. Neither the number nor the count is the cause,
# so two links of one chain share one fingerprint, and a different column is still a different cause.
C1=$'issue_state.py: items read 170; project total: none on the REST items route\n#307: the board carries no \'In Progress\' on it after 4 reads (gh: Invalid field values (HTTP 400)); a read failure, not a written card'
C2=$'issue_state.py: items read 172; project total: none on the REST items route\n#308: the board carries no \'In Progress\' on it after 4 reads (gh: Invalid field values (HTTP 400)); a read failure, not a written card'
f1=$(fingerprint BOARD-WRITE-FAILED "sbihaiko/4 #307 -> In Progress" "$C1"); f2=$(fingerprint BOARD-WRITE-FAILED "sbihaiko/4 #308 -> In Progress" "$C2")
[ -n "$f1" ] && [ "$f1" = "$f2" ] && ok "two links of one chain share one fingerprint" || ko "the links differ: '$f1' '$f2'"
f3=$(fingerprint BOARD-WRITE-FAILED "sbihaiko/4 #308 -> Doing" "${C2//In Progress/Doing}")
[ "$f3" != "$f2" ] && ok "another column is another cause" || ko "the column did not enter the fingerprint"

echo "--- shell suite: test-autorefuse.sh $([ "$fail" = 0 ] && echo green || echo RED)"
exit $fail
