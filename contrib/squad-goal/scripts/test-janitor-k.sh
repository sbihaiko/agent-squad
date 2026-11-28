#!/bin/bash
# seconds: 1.57
# Regression test for janitor rule K: a `disp-<n>` marker whose request log says `Nothing ran` (10 min old, no live run on the
# issue) is dropped like rule E does for `verdict failed`, so autofrontier/autobug dispatch the issue again.
# usage: bash test-janitor-k.sh [janitor.py]
# (#3104) The janitor loads squad/procs.py from the tree it runs in: it walks up from its own directory, then SG_SQUAD,
# SG_CHECKOUT and cwd. The daily pass clears every SG_* and runs from a cwd outside the repository, so the suite drops the
# inherited names and places the repository's squad package beside the janitor copy. The product walk-up then answers with
# no SG_SQUAD to stand in for the tree.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)'); do unset "$v"; done   # a caller's SG_SQUAD or SG_CHECKOUT never answers for this suite
repo=$(cd "$(dirname "$src")/../../.." && pwd -P)
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/co/runs"; ln -s "$repo/squad" "$T/s/squad"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo "$*" >> "${GH_LOG:-/dev/null}"
case "$*" in
  *issues/601*) echo open;;
  *issues/602*) echo closed;;
  *issues/603*) echo open;;
  *repos/x/y/branches*) printf 'dev d1\nfix/603-x a1\n';;
  *repos/x/y/compare/*) echo ahead;;
  *search/issues*) echo 0;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
echo req-x > "$T/st/auto/disp-601"; touch -t 202001010000 "$T/st/auto/disp-601"
echo "Nothing ran: vendor/skills is not clean" > "$T/st/req-x.log"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" 2>&1)
[ ! -e "$T/st/auto/disp-601" ] && ok "K: disp marker of a refused start dropped" || ko "K disp marker kept: $o"
# #523: a launch.py refusal (the model gate) says `refusing to start`, not `Nothing ran`: it frees the marker the same way.
echo req-y > "$T/st/auto/disp-601"; touch -t 202001010000 "$T/st/auto/disp-601"
echo "refusing to start: node 'route' runs m, whose hours window excludes now" > "$T/st/req-y.log"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" 2>&1)
[ ! -e "$T/st/auto/disp-601" ] && ok "K: disp marker of a launch.py refusal dropped" || ko "K refusal marker kept: $o"
echo req-z > "$T/st/auto/bug-601"; touch -t 202001010000 "$T/st/auto/bug-601"
echo "refusing to start: node 'route' runs m, whose hours window excludes now" > "$T/st/req-z.log"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" 2>&1)
[ ! -e "$T/st/auto/bug-601" ] && ok "K: the autobug marker of a launch.py refusal dropped too (no run folder, so rule J never sees it)" || ko "K refusal bug marker kept: $o"
# #525: a closed issue's marker is dropped, so the next pass never reads its state again; a work branch's standing is kept by
# both tips, so a second pass with no tip moved reads no compare.
echo req-c > "$T/st/auto/disp-602"; touch -t 202001010000 "$T/st/auto/disp-602"; echo "verdict failed" > "$T/st/req-c.log"
echo req-w > "$T/st/auto/disp-603"; touch -t 202001010000 "$T/st/auto/disp-603"; echo "verdict failed" > "$T/st/req-w.log"   # its work branch keeps it: each pass reads the branches
: > "$T/gh.log"; rm -f "$T/st/compare-standing.json"
for i in 1 2; do o=$(GH_LOG="$T/gh.log" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" 2>&1); done
[ ! -e "$T/st/auto/disp-602" ] && ok "K: the marker of a closed issue is dropped" || ko "closed marker kept: $o"
[ "$(/usr/bin/grep -c 'issues/602' "$T/gh.log")" = 1 ] && ok "a closed issue's state is read once over two passes" || ko "issue 602 read $(/usr/bin/grep -c 'issues/602' "$T/gh.log") times"
[ "$(/usr/bin/grep -c 'compare/' "$T/gh.log")" = 1 ] && ok "a branch whose tips did not move is compared once over two passes" || ko "compare read $(/usr/bin/grep -c 'compare/' "$T/gh.log") times"
rm -rf "$T"; exit $fail
