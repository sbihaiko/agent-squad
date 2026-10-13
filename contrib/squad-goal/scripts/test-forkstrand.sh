#!/bin/bash
# Regression test for #1837 (parent #1831): the loop reports a fork-only commit no landing path reads.
# The three shapes seen live on 2026-10-11: a child that commits off the branch the land reads (#1823's `fix/1823-...` and
# #1821's `fix/1821-...`, the MesenAI habit) leaves a commit the land never enumerates; a `gui/<N>-<slug>` the land's own
# pattern matches but its bare-number filter drops; and two branches answering one issue number, where the loop must refuse
# instead of picking one. A branch the land reads (gui/<N>) is the land's business and is never reported, work that is not
# fork work is not this pass's to name, and a card still In Progress belongs to a live run. The report is once per head: the
# same stranded commit says nothing new on a second pass.
# Offline: throwaway fork, its own HOME and state; never the live loop's.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
git=/usr/bin/git
S=$T/scripts; mkdir -p "$S" "$T/st/cq" "$T/st/auto" "$T/home" "$T/co/runs/.hub" "$T/co/runs/r1"
cp "$d"/{forkstrand.py,forkrescue.py,forkonly.sh} "$S/" 2>/dev/null
cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$T/st; SG_CHECKOUT=$T/co; SG_BASE=main
E
export HOME="$T/home" SG_STATE=$T/st SG_FORKONLY="$S/forkonly.sh"
F=$T/fork; $git init -q -b local/next "$F"; gc() { $git -C "$F" -c user.email=a@b -c user.name=n "$@"; }
gc commit -q --allow-empty -m base
mk() { local b=$1 f=$2 m=$3; gc checkout -q local/next; gc checkout -q -b "$b"; mkdir -p "$F/$(dirname "$f")"; echo "$m" > "$F/$f"; gc add -A; gc commit -q -m "$m"; }
mk fix/21-tooling contrib/squad-goal/scripts/thing.sh tooling      # (a) the MesenAI habit: no landing path reads it
mk gui/22-extra-suffix CHANGELOG.md changelog                      # (a) the land's pattern matches, its number filter drops it
mk fix/23-both-a contrib/squad-goal/scripts/a.sh a                 # (b) two branches answer one issue number
mk fix/23-both-b contrib/squad-goal/scripts/b.sh b
mk gui/24 contrib/squad-goal/scripts/d.sh landed                   # (c) the land reads this one
mk fix/25-game UI/Foo.cs game                                      # MesenAI paths: not this pass's to name
mk fix/26-live contrib/squad-goal/scripts/live.sh live             # fork paths, but the card is still In Progress
gc checkout -q local/next
# The card: what the hub publishes for this loop. 26 is In Progress (a run may still be on it).
cat > "$T/co/runs/.hub/hub-board.json" <<'J'
{"t":0,"items":[{"n":21,"st":"Code Complete","s":"open"},{"n":22,"st":"Testing","s":"open"},
                {"n":23,"st":"Code Complete","s":"open"},{"n":24,"st":"Code Complete","s":"open"},
                {"n":25,"st":"Code Complete","s":"open"},{"n":26,"st":"In Progress","s":"open"}],"prs":[],"err":null}
J
printf '#!/bin/bash\necho "$@" >> "$SG_STATE/moves"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
export SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_FORK_DIR=$F SG_FORK_BASE=local/next SG_FORK_BRANCH='gui/%n' SG_BOARD="$T/board.sh" SG_HUB_DIR="$T/co/runs/.hub"
h() { $git -C "$F" rev-parse --short=9 "$1"; }
has() { local hay=$1; shift; local x; for x in "$@"; do case "$hay" in *"$x"*) ;; *) return 1;; esac; done; }
run() { (cd "$T/co" && python3 "$S/forkstrand.py" >"$T/out" 2>&1); cat "$T/out" >> "$T/log"; }
notes() { ls "$T/st/cq" 2>/dev/null | /usr/bin/grep -c "#$1-forkstrand" || true; }
line() { /usr/bin/grep "^FORKSTRAND #$1" "$T/log"; }
: > "$T/log"; run

# (a) a branch no landing path reads, card at the landing stage: one report naming branch, commit and the ref the land reads.
for p in "21:fix/21-tooling" "22:gui/22-extra-suffix"; do n=${p%%:*}; b=${p#*:}; head=$(h "$b")
  l=$(line "$n")
  if has "$l" "$b" "$head" "refs/heads/gui/$n"; then ok "stranded $b reported by branch, commit and the ref the land reads"
    else ko "no report naming $b, $head and refs/heads/gui/$n (got: '$l')"; fi
  [ "$(notes "$n")" = 1 ] && ok "one note on the issue for #$n" || ko "#$n: $(notes "$n") notes queued"
  f=$(ls "$T/st/cq"/*#$n-forkstrand-* 2>/dev/null | head -1)
  if [ -n "$f" ] && has "$(cat "$f")" "$b" "refs/heads/gui/$n"; then ok "the note for #$n names the branch and the ref the land reads"
    else ko "the note for #$n does not name $b and refs/heads/gui/$n"; fi
done

# (b) two branches answering one issue number: refuse, name both, land neither on a guess.
l=$(line 23)
if has "$l" "fix/23-both-a" "fix/23-both-b" && { case "$l" in *refuse*|*"will not pick"*) true;; *) false;; esac; }; then
  ok "two branches for #23 refused, both named"; else ko "no refusal naming both branches for #23 (got: '$l')"; fi
$git -C "$F" rev-parse -q --verify gui/23 >/dev/null && ko "a gui/23 branch was created for the ambiguous pair" || ok "neither branch was re-pointed: no gui/23"
[ -e "$T/st/moves" ] && /usr/bin/grep -q " 23\$" "$T/st/moves" && ko "the card for the ambiguous #23 was moved" || ok "no card move for the ambiguous #23"

# (c) a branch the land reads, work that is not fork work, and a card still In Progress: no report.
for n in 24 25 26; do if [ -z "$(line "$n")" ] && [ "$(notes "$n")" = 0 ]; then ok "#$n left alone"; else ko "#$n reported: '$(line "$n")'"; fi; done

# once per head: a second pass over the same commits says nothing new.
n1=$(ls "$T/st/cq" | wc -l | tr -d ' '); r1=$(/usr/bin/grep -c '^FORKSTRAND' "$T/log"); run
n2=$(ls "$T/st/cq" | wc -l | tr -d ' '); r2=$(/usr/bin/grep -c '^FORKSTRAND' "$T/log")
if [ "$n1" = "$n2" ] && [ "$r1" = "$r2" ]; then ok "a second pass over the same head writes no second note"
  else ko "second pass repeated itself ($n1/$r1 -> $n2/$r2)"; fi

# per head, not per issue: a moved head is a new strand and is reported again.
gc checkout -q gui/22-extra-suffix; echo more >> "$F/CHANGELOG.md"; gc add -A; gc commit -q -m more; gc checkout -q local/next
run
[ "$(notes 22)" = 2 ] && ok "a new head on the stranded branch is reported again" || ko "a moved head was not reported ($(notes 22) notes)"

# the name comes from the one source (#1836), not a literal: another setting moves the ref the report names.
export SG_STATE=$T/st2 SG_FORK_BRANCH='fkx/%n'; mkdir -p "$T/st2/cq" "$T/st2/auto"; : > "$T/log"; run
has "$(line 21)" "refs/heads/fkx/21" && ok "the ref the report names follows SG_FORK_BRANCH (#1836)" || ko "the report ignores the branch setting (got: '$(line 21)')"
has "$(line 24)" "refs/heads/fkx/24" && ok "the branch the land reads follows the setting too" || ko "gui/24 was not read as a strand under fkx/%n (got: '$(line 24)')"
export SG_STATE=$T/st SG_FORK_BRANCH='gui/%n'

# (b, the land's own half) forkrescue must refuse the same ambiguity instead of re-pointing stray[0].
mkdir -p "$T/st3/cq" "$T/st3/auto"; echo r1 > "$T/st3/active-runs.txt"
{ echo '{"event": "spawn_accepted", "child": "c23", "definition": "GitHub issue #23"}'
  echo '{"event": "outcome", "node": "c23", "outcome": "pass", "reasons": ["clean pass"]}'; } > "$T/co/runs/r1/record.jsonl"
(cd "$T/co" && SG_STATE=$T/st3 python3 "$S/forkrescue.py") > "$T/out2" 2>&1
if has "$(cat "$T/out2")" "FORKRESCUE #23" "fix/23-both-a" "fix/23-both-b"; then ok "forkrescue refuses the ambiguous pair, naming both"
  else ko "forkrescue did not refuse the ambiguity: $(cat "$T/out2")"; fi
$git -C "$F" rev-parse -q --verify gui/23 >/dev/null && ko "forkrescue re-pointed one of the pair" || ok "forkrescue re-pointed neither"
rm -rf "$T"; exit $fail
