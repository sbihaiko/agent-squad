#!/bin/bash
# Regression test for #12: after a hand resolution, a resolution may not drop a line neither parent dropped.
# The evidence is gui/1893: resolving watch.sh in the conflict path deleted the merged line
# `echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT`. The script still parsed, so `bash -n` proved nothing, and the loop paid
# for it later: `watch.sh already running pid=` with an empty pid, a lock never released, two suites red. The audit reads the two
# parents (`git show :2:<file>`, `:3:<file>`) and the resolution, and fails on that deletion before the merge is committed.
# Offline by construction: text level, no network (stub gh/curl/wget that fail the run if anything reaches for one), no gate.
# usage: bash test-merge-audit.sh [path/to/merge-audit.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/merge-audit.sh}; case $src in /*) ;; *) src="$PWD/$src";; esac   # the cases run from inside the fixture repo, so the tool is named absolutely
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
[ -f "$src" ] && ok "merge-audit.sh is there: $src" || ko "merge-audit.sh is not there: $src"
mkdir -p "$T/bin" "$T/r"
for w in gh curl wget; do printf '#!/bin/bash\ntouch %s/net\necho "merge-audit reached for the network" >&2\nexit 97\n' "$T" > "$T/bin/$w"; chmod +x "$T/bin/$w"; done
export PATH="$T/bin:$PATH"
G=/usr/bin/git; g() { $G -C "$T/r" -c user.email=a@b -c user.name=t "$@"; }
# The fixture: watch.sh carries the merged line on BOTH parents (it had been merged before this conflict), and the two sides
# disagree on the line below it -- the conflict the resolver hand-edits. The dropped line is the one the loop needed.
g init -q -b base
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 1
E
g add watch.sh; g commit -q -m "base: the lock"
g checkout -q -b branch
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 2
E
g add watch.sh; g commit -q -m "branch: wait twice as long"
g checkout -q base
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 1; exec "$@"
E
g add watch.sh; g commit -q -m "base: run the command"
g merge branch >/dev/null 2>&1
[ "$(g ls-files -u watch.sh | wc -l | tr -d ' ')" = 3 ] && ok "fixture: watch.sh is a 3-stage conflict (its two parents carry the merged line)" || ko "fixture: watch.sh is not conflicted"
# 1: the fabrication -- the resolution drops the line both parents keep, the way gui/1893 did
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
sleep 1; exec "$@"
E
out=$(cd "$T/r" && bash "$src" watch.sh 2>&1); rc=$?
[ "$rc" != 0 ] && ok "a dropped line neither parent dropped fails the merge (exit $rc)" || ko "the fabrication passed: '$out'"
printf '%s' "$out" | $(command -v grep) -qF "watch.sh" && ok "the failure names the file" || ko "no file named: '$out'"
printf '%s' "$out" | $(command -v grep) -qF 'echo $$ > "$wlock/pid"; trap' && ok "the failure names the dropped line" || ko "no line named: '$out'"
# 2: keeping one side's text over the other's is not a failure -- the dropped line is the side that lost, not a fabrication
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 2
E
(cd "$T/r" && bash "$src" watch.sh) >/dev/null 2>&1 && ok "the branch's side alone passes (the base's line is dropped, and it is not both parents')" || ko "keeping theirs failed"
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 1; exec "$@"
E
(cd "$T/r" && bash "$src" watch.sh) >/dev/null 2>&1 && ok "the base's side alone passes (both parents' line kept, the branch's dropped)" || ko "keeping ours failed"
# 3: a resolution that deleted the whole file dropped what both parents kept, and says so
rm -f "$T/r/watch.sh"
out=$(cd "$T/r" && bash "$src" watch.sh 2>&1); rc=$?
[ "$rc" != 0 ] && printf '%s' "$out" | $(command -v grep) -qF 'echo $$ > "$wlock/pid"' && ok "a resolution that removed the file fails" || ko "a missing resolution passed: '$out'"
# 4: staged (or never conflicted) leaves no parents to read: the audit says so instead of passing silently
cat > "$T/r/watch.sh" <<'E'
#!/bin/bash
wlock="$1"
echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT
sleep 1; exec "$@"
E
g add watch.sh
out=$(cd "$T/r" && bash "$src" watch.sh 2>&1); rc=$?
[ "$rc" != 0 ] && ok "a file whose stages are gone is not audited as clean (exit $rc)" || ko "an unauditable file passed: '$out'"
out=$(cd "$T/r" && bash "$src" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "no argument is a usage failure" || ko "no argument passed"
[ ! -e "$T/net" ] && ok "nothing reached for the network (no gh, no curl, no wget)" || ko "the audit went online"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
