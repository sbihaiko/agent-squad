#!/bin/bash
# Regression test for the autopilot's HOLD lines: an APPROVE PR that does not merge says why, once per head and reason (stub gh, no network). usage: bash test-autopilot-hold.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin"; cp "$src" "$T/s/autopilot.sh"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" > "$T/s/env.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) echo '[{"number":10,"head":{"sha":"aaaaaaaaaaaa"},"title":"t","body":"Refs #1100","labels":[{"name":"hold"}],"base":{"ref":"main"},"draft":false},{"number":12,"head":{"sha":"cccccccccccc"},"title":"t","body":"Refs #1102","labels":[],"base":{"ref":"main"},"draft":false}]';;
  *"pulls/12 -q .head.sha"*) echo "cccccccccccc blocked";;
  *"issues/10/comments"*) cat "$GHC10" 2>/dev/null;;
  *"issues/1100/comments"*) cat "$GHC1100" 2>/dev/null;;
  *"-X DELETE"*"/labels/"*) echo "$*" >> "$GHLOG";;
  *"/check-runs"*) echo success;;   # a head with a check run: the autopilot holds a head with none (CI MISSING) before it reaches the state hold
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
echo "#10 aaaaaaaaa clean CI=success:9 VERDICT: APPROVE" > "$T/st/revci-10.out"; echo "#12 ccccccccc blocked CI=success:9 VERDICT: APPROVE" > "$T/st/revci-12.out"
export GHLOG="$T/ghlog" GHC10="$T/c10" GHC1100="$T/c1100"; : > "$GHLOG"
run() { bash "$T/s/autopilot.sh" 2>/dev/null; }
r=$(run)
case "$r" in *"HOLD #10 @ aaaaaaaaa: label"*) ok "APPROVE PR held by a label says so";; *) ko "no label HOLD: '$r'";; esac
case "$r" in *"HOLD #12 @ ccccccccc: GitHub reports 'blocked'"*) ok "APPROVE PR not clean says the state";; *) ko "no state HOLD: '$r'";; esac
r=$(run); case "$r" in *HOLD*) ko "HOLD repeated for the same head and reason: '$r'";; *) ok "same head and reason: no repeat";; esac
# /release: only a command comment (the whole body) releases the hold; prose and loop-written text that merely mention it do not
printf '%s\n' '[{"id":501,"body":"Needs the owner. Reply /release to let the loop continue."},{"id":502,"body":"looks fine to me"}]' > "$GHC10"
run >/dev/null; [ -s "$GHLOG" ] && ko "ordinary/loop comment released the hold: $(cat "$GHLOG")" || ok "ordinary comment does not release the hold"
printf '%s\n' '[{"id":503,"body":"/release\n"}]' > "$GHC1100"
r=$(run); grep -q 'issues/10/labels/hold' "$GHLOG" && ok "/release on the issue clears the hold label" || ko "no label delete after /release on the issue: $(cat "$GHLOG")"
case "$r" in *RELEASE*#10*) ok "release is announced";; *) ko "no RELEASE line: '$r'";; esac
: > "$GHLOG"; run >/dev/null; [ -s "$GHLOG" ] && ko "the same /release comment fired twice" || ok "a handled /release is not re-applied"
printf '%s\n' '[{"id":504,"body":"  /release  "}]' > "$GHC10"; : > "$GHLOG"; run >/dev/null; grep -q 'issues/10/labels/hold' "$GHLOG" && ok "/release on the PR clears the hold label" || ko "no label delete after /release on the PR"
rm -rf "$T"; exit $fail
