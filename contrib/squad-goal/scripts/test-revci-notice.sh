#!/bin/bash
# #1767: a run's progress and terminal notices stay out of the `#<pr> <sha9> ` summary namespace.
# That namespace is the selector every reader uses (autopilot.sh's `line`, autofix.sh, hub.py's pr_state, watch.sh,
# janitor.py). A notice printed inside it reads as a result with no `CI=` and no verdict, so the head stalls: the loop
# stops re-queueing reviews for it and the card claims a state derived from a progress message (PR #1706, 2026-10-11).
# Two seams: revci.sh (the writer that must keep the namespace summary-only) and autopilot.sh's line selection.
# usage: bash test-revci-notice.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
PR=7; ISS=1700; SHA=1e38e5a7b41c0d2e9f4b; S9=${SHA:0:9}
mkdir -p "$T/st/auto" "$T/st/cq" "$T/bin" "$T/clk" "$T/s"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true SG_PREPUSH_GATE=no SG_CI_WORKFLOW=checks.yml\n' "$T/st" > "$T/s/env.sh"

# stubbed gh: one open PR (7, head $SHA, Refs #$ISS). The head has no check run until the dispatch touches $T/dispatched,
# which is the state a push that created no workflow run leaves behind.
created=$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)   # a fresh PR, so the harness never depends on the wall clock
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"pulls?state=open"*) echo '[{"number":$PR,"head":{"sha":"$SHA","ref":"gui/$ISS"},"title":"t","draft":false,"body":"Refs #$ISS","labels":[],"base":{"ref":"main"},"created_at":"$created"}]';;
  *"pulls/$PR -q .head.sha"*) echo "$SHA";;
  *"pulls/$PR -q .head.ref"*) echo "gui/$ISS";;
  *mergeable_state*) echo clean;;
  *"workflow run"*) touch "$T/dispatched";;
  *"check-runs?per_page=1"*) [ -e "$T/dispatched" ] && echo 1 || echo 0;;
  *"check-runs"*join*) [ -e "$T/dispatched" ] && echo "success" || echo "";;
  *"check-runs"*) if [ -e "$T/dispatched" ]; then echo '[{"name":"checks","status":"completed","conclusion":"success"}]'; else echo '[]'; fi;;
  *) echo "";;
esac
J
# a fake clock, only for the revci run: revci.sh's dispatch waits out a 90 s grace window and its gate times out at 45 min.
# Each date call jumps 45 s and sleep is a no-op, so the run reaches both points in milliseconds.
cat > "$T/clk/date" <<J
#!/bin/bash
n=\$(cat "$T/clock" 2>/dev/null || echo 0); n=\$((n+1)); echo \$n > "$T/clock"
[ "\$1" = "+%s" ] && { echo \$(( \$(/bin/date +%s) + n * 45 )); exit 0; }
exec /bin/date "\$@"
J
printf '#!/bin/bash\nexit 0\n' > "$T/clk/sleep"
chmod +x "$T/bin/gh" "$T/clk/date" "$T/clk/sleep"

# --- the writer: revci.sh never prints a notice in the summary namespace ---
cp "$d/revci.sh" "$T/s/"
cat > "$T/s/review.sh" <<J
#!/bin/bash
printf 'VERDICT: CHANGES\n1. x\n' > "\$SG_STATE/review-\$1.md"
echo "model=stub"
J
PATH="$T/clk:$T/bin:$PATH" bash "$T/s/revci.sh" $PR $ISS > "$T/st/revci-$PR.out" 2>&1   # the loop runs revci detached with the run's output appended here
settled=$(/usr/bin/grep -cE "^#$PR $S9 " "$T/st/revci-$PR.out")
[ "$settled" = 1 ] && ok "revci: the namespace holds the summary line only" || ko "revci: $settled namespace lines (#$PR $S9 ) -- a notice took the summary's place: $(/usr/bin/grep -E "^#$PR $S9 " "$T/st/revci-$PR.out" | head -3 | tr '\n' '|')"
/usr/bin/grep -qE "^#$PR $S9 .*CI=" "$T/st/revci-$PR.out" && ok "revci: the namespace line carries CI=" || ko "revci: the namespace line carries no CI="
/usr/bin/grep -q "CI_DISPATCH" "$T/st/revci-$PR.out" && ok "revci: the dispatch is still reported" || ko "revci: the dispatch notice is gone (the run got no check run but said nothing)"
/usr/bin/grep -qE "^#$PR $S9 .*CI_DISPATCH" "$T/st/revci-$PR.out" && ko "revci: CI_DISPATCH printed inside the summary namespace" || ok "revci: CI_DISPATCH printed outside the summary namespace"

# --- the reader: autopilot's line selection ---
cp "$d/autopilot.sh" "$T/s/"
cat > "$T/s/revci.sh" <<'J'
#!/bin/bash
echo "stub revci $*"
J
cat > "$T/s/autofix.sh" <<'J'
#!/bin/bash
case "$1" in
  --dupissue) exit 1;;
  --threads) echo 0; exit 0;;
  *) exit 0;;
esac
J
chmod +x "$T/s"/*.sh
rm -f "$T/dispatched"; echo "#$PR $S9 CI_DISPATCH: the head has no check run at all, so the gate would wait forever; checks.yml dispatched on gui/$ISS" > "$T/st/revci-$PR.out"
r=$(PATH="$T/bin:$PATH" bash "$T/s/autopilot.sh" 2>&1)
case "$r" in *"AUTOREVIEW #$PR"*) ok "autopilot: a lone notice is not a result, so the head is queued for review";; *) ko "autopilot: a lone notice was taken for the run's result; no review queued: $r";; esac
r=$(PATH="$T/bin:$PATH" bash "$T/s/autopilot.sh" 2>&1)
case "$r" in *"AUTOREVIEW #$PR"*) ko "autopilot: queued a second review for the same head (launched marker ignored)";; *) ok "autopilot: the head is queued once";; esac
# positive control: a real summary line still is the result, so the head is not reviewed again
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"
echo "#$PR $S9 clean CI=none VERDICT: APPROVE" > "$T/st/revci-$PR.out"
r=$(PATH="$T/bin:$PATH" bash "$T/s/autopilot.sh" 2>&1)
case "$r" in *"AUTOREVIEW #$PR"*) ko "autopilot: re-reviewed a head that already has a summary line: $r";; *) ok "autopilot: a summary line is still selected as the result";; esac
case "$r" in *"CI MISSING #$PR"*) ok "autopilot: read the summary's verdict (APPROVE, no check run -> merge held)";; *) ko "autopilot: the summary line was not read as a verdict: $r";; esac
rm -rf "$T"; exit $fail
