#!/bin/bash
# Regression test for the fix-round ledger autofix.sh charges (#1775): a round is spent by a fix run, not by an attempt to
# start one. autofix.sh writes the head sha into auto/rounds-<pr> BEFORE the dispatch is known to have started, and
# autopilot.sh turns that file into the cap: `n=$(grep -c . "$A/rounds-$pr")`, and once n reaches AP_MAX_ROUNDS (2) the PR
# goes to autopanel.sh / needs-owner instead of another fix run. So a dispatch that never started (mkreq.sh refuses, #1770;
# launch.py prints no run id) ate a round of a PR that no fix run ever worked, and a third problem on that head reached the
# panel with zero rounds spent (PR #1706 @ 27aaaf5b5, 2026-10-11).
# usage: bash test-autofix-rounds.sh [path/to/autofix.sh]
src=${1:-$(dirname "$0")/autofix.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/st/auto" "$T/s"; cp "$src" "$T/s/autofix.sh"; M="$T/st/auto"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *'.head.ref+" "'*) echo "feat/child aaaaaaaaa clean" ;;
  *graphql*) echo 0 ;;
  *issues/*) echo "open - Some bug" ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf 'SG_REPO=x/y; SG_STATE=%s; SG_BASE=main; SG_CHECKOUT=%s\n' "$T/st" "$T" > "$T/s/env.sh"
# a review verdict keeps `probs` non-empty, so the script reaches the dispatch instead of returning early
echo "#9 aaaaaaaaa clean CI=success:1 VERDICT: CHANGES" > "$T/st/revci-9.out"
printf 'VERDICT: CHANGES\n1. fix it\n' > "$T/st/review-9.md"
rounds() { cat "$M/rounds-9" 2>/dev/null; }
# 1) (#1770) a dispatch mkreq.sh refuses writes nothing to the ledger: the round already recorded stays exactly as it was
printf '#!/bin/bash\necho "refusing: request $1 names tests/a.py, which open PR #7 already changes." >&2; exit 1\n' > "$T/s/mkreq.sh"; chmod +x "$T/s/mkreq.sh"
printf 'print("run-4242")\n' > "$T/s/launch.py"
printf 'bbbbbbbbb\n' > "$M/rounds-9"; touch "$M/queued-9-aaaaaaaaa"
r=$(bash "$T/s/autofix.sh" 9 8 2>&1); rc=$?
[ "$(rounds)" = bbbbbbbbb ] && ok "1 a refused dispatch leaves rounds-9 unchanged" || ko "1 a refused dispatch changed rounds-9 to '$(rounds)'"
[ -e "$M/fix-9-aaaaaaaaa" ] && ko "1 a refused dispatch left auto/fix-9-aaaaaaaaa behind" || ok "1 a refused dispatch frees the fix marker"
[ "$rc" = 75 ] && ok "1 a refused dispatch exits 75" || ko "1 a refused dispatch exited $rc"
# 2) (#1775) launch.py printing no run id is the second way a dispatch fails to start: no run, so no round, and the head is
# freed for the next pass instead of sitting behind a claim with a round charged against it
printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "$SG_STATE/req/$1.txt"\n' > "$T/s/mkreq.sh"; chmod +x "$T/s/mkreq.sh"
: > "$T/s/launch.py"   # exits 0 and prints nothing: the launch started no run
printf 'bbbbbbbbb\n' > "$M/rounds-9"; rm -f "$M/fix-9-aaaaaaaaa"; touch "$M/queued-9-aaaaaaaaa"
r=$(bash "$T/s/autofix.sh" 9 8 2>&1); rc=$?
[ "$(rounds)" = bbbbbbbbb ] && ok "2 a launch with no run id charges no round" || ko "2 a launch with no run id charged a round: '$(rounds)'"
[ -e "$M/fix-9-aaaaaaaaa" ] && ko "2 a failed launch left auto/fix-9-aaaaaaaaa claimed" || ok "2 a failed launch frees the fix marker"
[ -e "$M/queued-9-aaaaaaaaa" ] && ko "2 a failed launch left auto/queued-9-aaaaaaaaa: the next pass skips the head" || ok "2 a failed launch frees the queued marker"
[ "$rc" != 0 ] && ok "2 a failed launch exits non-zero ($rc)" || ko "2 a failed launch exited 0: '$r'"
# 3) the dispatch that does start charges exactly one round, and only once for the head
printf 'print("run-4242")\n' > "$T/s/launch.py"
printf 'bbbbbbbbb\n' > "$M/rounds-9"; rm -f "$M/fix-9-aaaaaaaaa"; touch "$M/queued-9-aaaaaaaaa"
r=$(bash "$T/s/autofix.sh" 9 8 2>&1); rc=$?
[ "$(rounds)" = "$(printf 'bbbbbbbbb\naaaaaaaaa')" ] && ok "3 a launched dispatch adds exactly one round" || ko "3 rounds-9 is '$(rounds)' after a launched dispatch"
[ "$rc" = 0 ] && ok "3 a launched dispatch exits 0" || ko "3 a launched dispatch exited $rc: '$r'"
case "$r" in *run-4242*) ok "3 the run id is reported";; *) ko "3 no run id in '$r'";; esac
r=$(bash "$T/s/autofix.sh" 9 8 2>&1)
[ "$(grep -c . "$M/rounds-9")" = 2 ] && ok "3 the same head is charged once" || ko "3 the same head was charged twice: '$(rounds)'"
rm -rf "$T"; exit $fail
