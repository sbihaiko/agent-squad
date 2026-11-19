#!/bin/bash
# seconds: 1.32
# Regression test for merge-audit.sh: after a hand resolution of a conflicted file, a line both parents hold must survive. The fixture reproduces the watch.sh line a resolution deleted; the script still parsed, and nothing else looked. Keeping one side's text over the other's is not a failure. Throwaway repo, no network.
src=${1:-$(dirname "$0")/merge-audit.sh}; src=$(cd "$(dirname "$src")" && pwd)/$(basename "$src"); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/r"; G=/usr/bin/git; g() { $G -C "$T/r" -c user.email=a@b -c user.name=t "$@"; }
PIDLINE='echo $$ > "$wlock/pid"; trap '"'"'rm -rf "$wlock"'"'"' EXIT; trap '"'"'rm -rf "$wlock"; exit 0'"'"' INT TERM'
# any network or gate call from the audit leaves a marker
for c in gh curl wget ssh; do printf '#!/bin/bash\ntouch "%s/netcall"\nexit 1\n' "$T" > "$T/bin/$c"; chmod +x "$T/bin/$c"; done; export PATH="$T/bin:$PATH"
mkbase() { rm -rf "$T/r"; mkdir -p "$T/r"; g init -q -b base; printf '%s\n' '#!/bin/bash' 'wlock=/tmp/w' 'mkdir "$wlock" || { echo "watch.sh already running pid=$(cat "$wlock/pid")"; exit 1; }' "$PIDLINE" 'work' 'exit 0' > "$T/r/watch.sh"; g add watch.sh; g commit -q -m base; }
conflict() { mkbase; g checkout -q -b ours; sed -i.bak 's/^work$/work ours/' "$T/r/watch.sh"; rm "$T/r/watch.sh.bak"; g commit -q -am ours
  g checkout -q base; g checkout -q -b theirs; sed -i.bak 's/^work$/work theirs/' "$T/r/watch.sh"; rm "$T/r/watch.sh.bak"; g commit -q -am theirs
  g checkout -q ours; g merge theirs >/dev/null 2>&1; [ -n "$(g ls-files -u watch.sh)" ]; }
audit() { (cd "$T/r" && bash "$src" "$@" 2>&1); }
resolve() { printf '%s\n' '#!/bin/bash' 'wlock=/tmp/w' 'mkdir "$wlock" || { echo "watch.sh already running pid=$(cat "$wlock/pid")"; exit 1; }' "$@" 'exit 0' > "$T/r/watch.sh"; }

conflict && ok "setup: watch.sh is in conflict, both parents hold the pid line" || ko "setup: no conflict"
resolve "$PIDLINE" 'work ours'; out=$(audit watch.sh); rc=$?
[ $rc = 0 ] && ok "a resolution that keeps our side passes" || ko "keeping ours failed rc=$rc: $out"
resolve "$PIDLINE" 'work theirs'; out=$(audit watch.sh); rc=$?
[ $rc = 0 ] && ok "a resolution that keeps their side passes" || ko "keeping theirs failed rc=$rc: $out"
resolve "$PIDLINE" 'work ours' 'work theirs'; out=$(audit watch.sh); rc=$?
[ $rc = 0 ] && ok "a resolution that keeps both sides passes" || ko "keeping both failed rc=$rc: $out"
resolve 'work ours'; out=$(audit watch.sh); rc=$?
bash -n "$T/r/watch.sh" && ok "the fabricated script still parses (bash -n proves nothing here)" || ko "fabricated script does not parse"
[ $rc = 1 ] && ok "the deleted pid line fails the audit" || ko "the deleted pid line passed"
case "$out" in *watch.sh*) ok "the output names the file";; *) ko "file not named: $out";; esac
case "$out" in *'watch.sh:4'*) ok "the output names the line (4 in the parents)";; *) ko "line not named: $out";; esac
case "$out" in *'echo $$ > "$wlock/pid"'*) ok "the output shows the dropped text";; *) ko "text not shown: $out";; esac
resolve "$PIDLINE" 'work ours' 'echo added by the resolution'; out=$(audit watch.sh); rc=$?
[ $rc = 0 ] && ok "text a resolution adds is not a failure" || ko "an added line failed rc=$rc: $out"
resolve "$PIDLINE" 'work ours'; sed -i.bak 's/^exit 0$/exit 0 # edited/' "$T/r/watch.sh"; rm "$T/r/watch.sh.bak"; out=$(audit watch.sh); rc=$?
[ $rc = 1 ] && ok "editing a line both parents hold counts as dropping it" || ko "an edited shared line passed rc=$rc"
rm -f "$T/r/watch.sh"; out=$(audit watch.sh); rc=$?
[ $rc = 1 ] && ok "a resolution that deletes the file drops the shared lines and fails" || ko "a deleted file passed"
resolve "$PIDLINE" 'work ours'; g add watch.sh; out=$(audit watch.sh); rc=$?
[ $rc = 2 ] && ok "a file with no conflict stages is refused with exit 2" || ko "no stages: rc=$rc: $out"
# The same rule after the fact: a merge commit is audited through its two parents, so a resolution a child made in its own
# worktree -- the shape that cost #9 two hours -- is caught when the landing reads the tip, not when someone notices.
mergecommit() {   # mergecommit <drop the shared line?> -- <commit> on stdout
  mkbase; g checkout -q -b side; sed -i.bak 's/^work$/work side/' "$T/r/watch.sh"; rm "$T/r/watch.sh.bak"; g commit -q -am side
  g checkout -q base; sed -i.bak 's/^exit 0$/exit 1/' "$T/r/watch.sh"; rm "$T/r/watch.sh.bak"; g commit -q -am moved
  g checkout -q side; g merge -q --no-commit base >/dev/null 2>&1 || true
  MK='mkdir "$wlock" || { echo "watch.sh already running pid=$(cat "$wlock/pid")"; exit 1; }'
  if [ "$1" = drop ]; then printf '%s\n' '#!/bin/bash' 'wlock=/tmp/w' "$MK" 'work side' 'exit 1' > "$T/r/watch.sh"
  else printf '%s\n' '#!/bin/bash' 'wlock=/tmp/w' "$MK" "$PIDLINE" 'work side' 'exit 1' > "$T/r/watch.sh"; fi
  g add watch.sh; g -c core.editor=true commit -q --no-edit; g rev-parse HEAD; }
c=$(mergecommit keep); out=$(audit watch.sh "$c"); rc=$?
[ $rc = 0 ] && ok "a merge commit that keeps both sides passes" || ko "a kept commit failed rc=$rc: $out"
c=$(mergecommit drop); out=$(audit watch.sh "$c"); rc=$?
bash -n "$T/r/watch.sh" && ok "the fabricated merge commit still parses its tree" || ko "the fabricated tree does not parse"
[ $rc = 1 ] && ok "a merge commit whose resolution dropped a shared line fails" || ko "the fabricated commit passed rc=$rc"
case "$out" in *'echo $$ > "$wlock/pid"'*) ok "and the commit mode shows the dropped text";; *) ko "no text: $out";; esac
out=$(audit watch.sh "$(g rev-parse HEAD~1)"); rc=$?
[ $rc = 2 ] && ok "a commit that is not a merge is refused with exit 2" || ko "a single-parent commit: rc=$rc: $out"
out=$(audit); rc=$?; [ $rc = 2 ] && ok "no file argument is refused with exit 2" || ko "no argument: rc=$rc"
[ ! -e "$T/netcall" ] && ok "offline: no network or gate command ran" || ko "the audit called out"
rm -rf "$T"; exit $fail
