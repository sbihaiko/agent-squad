#!/bin/bash
# seconds: 1.82
# A queued comment carries the repository it is for (#20): its name is <owner>__<repo>#<n>-<tag>.md, and cqflush.sh posts it
# to that repository -- whichever process flushes it, whichever directory it sits in. A comment for a repository the machine
# does not know is refused by name, retired as .dead and never posted, never retried and never sent to another repository.
# Every writer writes that form: a guard fails on a writer that names a bare issue number. Offline: fixture profiles, stub gh.
# usage: bash test-cqflush-address.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain"
for p in alpha:x beta:y; do printf ': "${SG_REPO:=%s/%s}"; : "${SG_OWNER:=%s}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' \
  "${p#*:}" "${p%%:*}" "${p#*:}" "$T/co-${p%%:*}" > "$P/${p%%:*}.sh"; done
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# records the endpoint and the body of each comment; fails while $T/gh-fail exists
for a; do case "$a" in body=@*) b=$(cat "${a#body=@}");; esac; done
printf '%s | %s\n' "$*" "$b" >> "$GHLOG"
[ ! -e "$GHFAIL" ]
GH
chmod +x "$T/bin/gh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog GHFAIL=$T/gh-fail PATH="$T/bin:$PATH"; : > "$T/ghlog"
unset SG_TOOLING_REPO SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT
mkdir -p "$B/alpha/cq" "$B/beta/cq" "$B/ghost/cq"
put() { printf '%s\n' "$3" > "$B/$1/$2"; touch -t 202601010000 "$B/$1/$2"; }   # a comment idle long enough to be read
flush() { (cd "$T/plain" && env "$@" bash "$d/cqflush.sh" --once 2>&1); }
calls() { $G -c . "$T/ghlog"; }

# 1. a comment queued for beta, flushed by a process that belongs to alpha, is posted to beta
put alpha/cq 'y__beta#7-note.md' 'for beta'
out=$(flush SG_PROFILE=alpha SG_REPO=x/alpha)
$G -q '^api repos/y/beta/issues/7/comments .*| for beta$' "$T/ghlog" && ! $G -q 'x/alpha' "$T/ghlog" \
  && ok "a comment for y/beta, flushed by an alpha process, is posted to y/beta" || ko "posted: $(cat "$T/ghlog") / $out"
[ -f "$B/alpha/cq/y__beta#7-note.md.done" ] && ok "the posted comment is marked done" || ko "done mark: $(ls "$B/alpha/cq")"

# 2. the address is the write target: the same comment in a directory no profile owns, or in the other repository's queue
: > "$T/ghlog"
put ghost/cq 'x__alpha#9-moved.md' 'moved to a ghost directory'
put beta/cq  'x__alpha#10-moved.md' 'moved to beta'"'"'s queue'
out=$(flush)
$G -q '^api repos/x/alpha/issues/9/comments ' "$T/ghlog" && $G -q '^api repos/x/alpha/issues/10/comments ' "$T/ghlog" \
  && [ "$(calls)" = 2 ] && ok "a comment read from another directory is still posted to the repository it names" || ko "moved: $(cat "$T/ghlog") / $out"

# 3. the address is read in any case; the repository is posted as the profile spells it
: > "$T/ghlog"; put alpha/cq 'X__Alpha#11-case.md' 'case'
out=$(flush); $G -q '^api repos/x/alpha/issues/11/comments ' "$T/ghlog" && ok "owner and name match in any case" || ko "case: $(cat "$T/ghlog") / $out"

# 4. a repository the machine does not know is refused by name, retired, and never posted
: > "$T/ghlog"; put alpha/cq 'z__nowhere#3-lost.md' 'for nowhere'
out=$(flush SG_PROFILE=alpha SG_REPO=x/alpha)
[ -f "$B/alpha/cq/z__nowhere#3-lost.md.dead" ] && [ ! -e "$B/alpha/cq/z__nowhere#3-lost.md" ] && printf '%s' "$out" | $G -q 'z/nowhere' \
  && ok "an unknown repository is retired as .dead and named in the report" || ko "unknown: $(ls "$B/alpha/cq") / $out"
[ "$(calls)" = 0 ] && ok "an unknown repository is never posted, to its own address or to the flusher's repository" || ko "unknown posted: $(cat "$T/ghlog")"
out=$(flush SG_PROFILE=alpha SG_REPO=x/alpha); [ "$(calls)" = 0 ] && [ -z "$(ls "$B/alpha/cq" | $G 'nowhere.*tries')" ] \
  && ok "a retired comment is not retried" || ko "retried: $(cat "$T/ghlog") / $out"

# 5. a malformed address is refused the same way
: > "$T/ghlog"; put alpha/cq 'x__alpha#abc-bad.md' 'no number'
out=$(flush); [ -f "$B/alpha/cq/x__alpha#abc-bad.md.dead" ] && [ "$(calls)" = 0 ] && ok "an address with no issue number is retired, unposted" || ko "malformed: $(ls "$B/alpha/cq") / $(cat "$T/ghlog")"

# 6. a failing post keeps the address: the retry goes to the same repository, and five failures retire it
: > "$T/ghlog"; : > "$GHFAIL"; put beta/cq 'x__alpha#12-flaky.md' 'flaky'
for i in 1 2 3 4 5; do flush >/dev/null; touch -t 202601010000 "$B/beta/cq/x__alpha#12-flaky.md" 2>/dev/null; done
[ "$(calls)" = 5 ] && ! $G -v '^api repos/x/alpha/issues/12/comments ' "$T/ghlog" | $G -q . && [ -f "$B/beta/cq/x__alpha#12-flaky.md.dead" ] \
  && ok "a failing comment is retried against the repository it names, then retired" || ko "flaky: $(cat "$T/ghlog") / $(ls "$B/beta/cq")"
rm -f "$GHFAIL"

# 7. the writers' helper: sg_cq_file names the queue entry of the repository in force
. "$d/address.sh"
got=$(SG_REPO=x/alpha SG_STATE=$B/alpha sg_cq_file '12-tag.md')
[ "$got" = "$B/alpha/cq/x__alpha#12-tag.md" ] && ok "sg_cq_file: <state>/cq/<owner>__<repo>#<n>-<tag>.md" || ko "sg_cq_file gave '$got'"
got=$(SG_REPO= SG_STATE=$B/alpha sg_cq_file '12-tag.md' 2>/dev/null); rc=$?
{ [ $rc -ne 0 ] && [ -z "$got" ]; } && ok "sg_cq_file refuses to name an entry when no repository is in force" || ko "sg_cq_file with no repository gave '$got' (rc $rc)"
got=$(SG_REPO=x/alpha SG_STATE=$B/alpha sg_cq_file 'abc-tag.md' 2>/dev/null); rc=$?
{ [ $rc -ne 0 ] && [ -z "$got" ]; } && ok "sg_cq_file refuses a leaf that does not start with an issue number" || ko "sg_cq_file gave '$got' for a bare tag (rc $rc)"

# 8. the guard: a writer that names a bare issue number fails. bare_writers prints each offending line of the files it reads.
bare_writers() { $G -noE '(/cq/\$\{?[A-Za-z_]+\}?-|/cq/[0-9]|"cq" */ *f"\{[a-z_]+\}-)' "$@" | $G -v '^[^:]*:[0-9]*: *#'; }
mkdir -p "$T/fix"
printf 'printf "x" > "$SG_STATE/cq/$iss-open$$.md"\n' > "$T/fix/bad.sh"
printf '(st / "cq" / f"{n}-neverstart.md").write_text(body)\n' > "$T/fix/bad.py"
printf 'printf "x" > "$(sg_cq_file "$iss-open$$.md")"\n' > "$T/fix/good.sh"
printf '(st / "cq" / f"{CQ}#{n}-neverstart.md").write_text(body)\n' > "$T/fix/good.py"
[ -n "$(bare_writers "$T/fix/bad.sh")" ] && [ -n "$(bare_writers "$T/fix/bad.py")" ] && [ -z "$(bare_writers "$T/fix/good.sh" "$T/fix/good.py")" ] \
  && ok "guard: it flags a bare shell writer and a bare Python writer, and passes the addressed form" || ko "guard fixtures: bad.sh '$(bare_writers "$T/fix/bad.sh")' bad.py '$(bare_writers "$T/fix/bad.py")'"
bad=$(cd "$d" && bare_writers $(ls *.sh *.py | $G -v '^test-\|^cqflush.sh$'))
[ -z "$bad" ] && ok "guard: no script writes a queued comment named by a bare issue number" || ko "bare writers: $bad"
rm -rf "$T"; [ $fail = 0 ] && echo "cqflush address: all green" || { echo "cqflush address: FAILED"; exit 1; }
