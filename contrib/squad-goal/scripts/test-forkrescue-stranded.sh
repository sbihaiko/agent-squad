#!/bin/bash
# Regression test for #1831: a fork commit stranded on fix/<N>-<slug> has no landing path.
# A child that works in the fork but commits off gui/<N> ("fix/<N>-<slug>", the MesenAI habit) used to be rescued only when it
# ended needs-human/fail AND the issue carried the fork-only brand. A plain `bug` issue (#1823) and a child that ended pass
# (#1821) therefore left their commit on a branch forkclose.sh never enumerates (it reads refs/heads/gui/* only) and the card
# parked at Code Complete forever. forkrescue.py re-points any stray branch of that shape whose card is at the landing stage.
# Offline: stub gh/board over a throwaway repo, its own HOME and state; never the live loop's.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; git=/usr/bin/git; mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
a="$*"; n=$(printf '%s' "$a" | grep -oE 'issues/[0-9]+' | head -1 | tr -dc 0-9)
case "$a" in
  *"issues/$n"*) printf '{"number":%s,"state":"open","title":"t%s","labels":[{"name":"bug"}],"body":"plain body, no Fork-only work line"}' "$n" "$n";;
esac
GH
chmod +x "$T/bin/gh"
printf '#!/bin/bash\necho "$@" >> "$SG_STATE/moves"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
S=$T/scripts; mkdir -p "$S" "$T/st/req" "$T/st/cq" "$T/st/auto" "$T/home" "$T/co/runs/r1"
cp "$d"/{forkrescue.py,forkonly.sh} "$S/"
cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$T/st; SG_CHECKOUT=$T/co; SG_BASE=main
E
export HOME="$T/home" SG_STATE=$T/st SG_FORKONLY="$S/forkonly.sh" SG_BOARD="$T/board.sh" PATH="$T/bin:$PATH"
# The fork: one base and four stray branches, none of them gui/<N>, none with an auto/fork-<N>-<head> marker.
F=$T/fork; $git init -q -b local/next "$F"; gc() { $git -C "$F" -c user.email=a@b -c user.name=n "$@"; }
gc commit -q --allow-empty -m base
mk() { local b=$1 f=$2 m=$3; gc checkout -q local/next; gc checkout -q -b "$b"; mkdir -p "$F/$(dirname "$f")"; echo "$m" > "$F/$f"; gc add -A; gc commit -q -m "$m"; }
mk fix/11-tooling contrib/squad-goal/scripts/thing.sh tooling      # fork paths, plain `bug` issue, child ended pass
mk fix/12-changelog CHANGELOG.md changelog                         # fork path (root file), child ended needs-human
mk fix/13-game UI/Foo.cs game                                      # MesenAI paths: not this pass's to move
mk fix/14-live contrib/squad-goal/scripts/live.sh live             # fork paths, but the card is not at the landing stage
gc checkout -q local/next
# The card: what the hub publishes for this loop. 14 is In Progress (a run may still be on it).
mkdir -p "$T/co/runs/.hub"
cat > "$T/co/runs/.hub/hub-board.json" <<'J'
{"t":0,"items":[{"n":11,"st":"Code Complete","s":"open"},{"n":12,"st":"Testing","s":"open"},
                {"n":13,"st":"Code Complete","s":"open"},{"n":14,"st":"In Progress","s":"open"}],"prs":[],"err":null}
J
echo r1 > "$T/st/active-runs.txt"
{ for p in 11:pass 12:needs-human 13:pass 14:pass; do i=${p%%:*}; o=${p#*:}
    echo '{"event": "spawn_accepted", "child": "c'$i'", "definition": "GitHub issue #'$i'"}'
    echo '{"event": "outcome", "node": "c'$i'", "outcome": "'$o'", "reasons": ["ended without a clean pass"]}'
  done; } > "$T/co/runs/r1/record.jsonl"
# the repro's precondition: no gui/<N> branch and no marker was ever written for these branches
hit=""; for i in 11 12 13 14; do $git -C "$F" rev-parse -q --verify "gui/$i" >/dev/null && hit="$hit gui/$i"
  ls "$T/st/auto" | $G -q "^fork-$i-" && hit="$hit fork-$i-marker"; done
[ -z "$hit" ] && ok "precondition: no gui/<N> branch and no fork-<N>-<head> marker carries these commits" || ko "precondition already holds:$hit"
export SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_FORK_DIR=$F SG_FORK_BASE=local/next SG_FORK_BRANCH='gui/%n'
run() { (cd "$T/co" && python3 "$S/forkrescue.py" >"$T/out" 2>&1); }
run
for p in "11:fix/11-tooling" "12:fix/12-changelog"; do i=${p%%:*}; b=${p#*:}
  [ "$($git -C "$F" rev-parse gui/$i 2>/dev/null)" = "$($git -C "$F" rev-parse $b)" ] && ok "stranded $b -> gui/$i" || ko "stranded $b not re-pointed to gui/$i ($(cat "$T/out"))"
  $G -q "Code Complete $i\$" "$T/st/moves" 2>/dev/null && ok "card #$i -> Code Complete" || ko "card #$i not moved"
  ls "$T/st/cq"/$i-forkrescue* >/dev/null 2>&1 && ok "note queued for #$i" || ko "no note for #$i"; done
$git -C "$F" rev-parse -q --verify gui/13 >/dev/null && ko "moved a branch carrying MesenAI paths (#13)" || ok "MesenAI-path branch (#13) left alone"
$git -C "$F" rev-parse -q --verify gui/14 >/dev/null && ko "moved a branch whose card is In Progress (#14)" || ok "In Progress card (#14) left alone"
n1=$(ls "$T/st/cq" | wc -l | tr -d ' '); run; n2=$(ls "$T/st/cq" | wc -l | tr -d ' ')
[ "$n1" = "$n2" ] && ok "once per child: a second pass writes no second note" || ko "second pass re-noted ($n1 -> $n2)"
[ "$($git -C "$F" rev-parse gui/11)" = "$($git -C "$F" rev-parse fix/11-tooling)" ] && ok "gui/11 still at the same commit" || ko "gui/11 moved on the second pass"
rm -rf "$T"; exit $fail
