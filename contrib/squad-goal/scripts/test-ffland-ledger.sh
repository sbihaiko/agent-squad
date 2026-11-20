#!/bin/bash
# seconds: 1.22
# Regression test for the landing facts a hand landing by ffland.sh writes (ADR-0149, #2532). usage: bash test-ffland-ledger.sh
# A landing writes a `landed` fact to the issue ledger of every issue its pushed commits name, so one branch that carries two
# issues marks both. Offline: SG_LAND_REMOTE names a local bare repository, or a path that holds none.
d=$(dirname "$0"); T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
unset $(compgen -e | /usr/bin/grep '^SG_') 2>/dev/null || true
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
G=/usr/bin/git; export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
R=$T/r; $G init -q -b dev "$R"; echo a > "$R/a"; $G -C "$R" add a; $G -C "$R" commit -qm base
$G init -q --bare "$T/remote.git"; $G -C "$R" remote add origin "$T/remote.git"; $G -C "$R" push -q origin dev
$G -C "$R" checkout -q -b gui/2512-2514
echo b > "$R/b"; $G -C "$R" add b; $G -C "$R" commit -qm "first (Refs #2512)"
echo c > "$R/c"; $G -C "$R" add c; $G -C "$R" commit -qm "second (Refs #2514)"
$G -C "$R" push -q origin gui/2512-2514
export SG_STATE=$T/st SG_REPO=sbihaiko/agent-squad SG_BASE=dev SG_LAND_STATUS=off SG_LAND_REMOTE=$T/remote.git
mkdir -p "$SG_STATE"
L=$SG_STATE/state
out=$(bash "$d/ffland.sh" gui/2512-2514 2512 2>/dev/null)
case "$out" in "FFLAND "*) ok "1 the branch lands in dev";; *) ko "1 the branch did not land: $out";; esac
for n in 2512 2514; do
  grep -q '"gui/2512-2514"' "$L/sbihaiko__agent-squad__$n.json" 2>/dev/null && ok "2 #$n carries the landing fact of the branch" || ko "2 #$n carries no landing fact"
done
[ -e "$L/sbihaiko__agent-squad__2513.json" ] && ko "3 an issue no commit names got a fact" || ok "3 an issue no commit names gets no fact"
out=$(SG_LAND_REMOTE=$T/none bash "$d/ffland.sh" gui/2600-none 2600 2>/dev/null)
case "$out" in FFLAND-FALLBACK*) ok "4 a refused landing answers FFLAND-FALLBACK";; *) ko "4 a refused landing did not fall back: $out";; esac
[ -e "$L/sbihaiko__agent-squad__2600.json" ] && ko "4 a landing that pushed nothing wrote a fact" || ok "4 a landing that pushed nothing writes no fact"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
