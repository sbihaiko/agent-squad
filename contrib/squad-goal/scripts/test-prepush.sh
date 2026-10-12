#!/bin/bash
# Regression test for prepush.sh (throwaway repos, stubbed gh) and the autopilot's prepush gate. usage: bash test-prepush.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/bin"; cp "$d/prepush.sh" "$d/autopilot.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_CHECKOUT=%s SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/co" > "$T/s/env.sh"
$G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }
mkdir -p UI scripts; echo x > UI/a.cs; echo y > scripts/b.py; $G add .; c -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
$G checkout -q -b fix/1-x; echo z > UI/c.cs; $G add .; c -m fix; $G push -q origin fix/1-x 2>/dev/null; SHA=$($G rev-parse HEAD)
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"pulls?state=open"*) echo '[{"number":7,"head":{"sha":"$SHA"},"title":"t","body":"Refs #1200","labels":[],"base":{"ref":"main"},"draft":false}]';;
  *"pulls/7 -q .head.ref"*) echo "fix/1-x $SHA";;
  *"pulls/7 -q .title"*) echo "\${GH_TITLE:-Play: thing (Refs #1200)}";;
  *"pulls/7 -q .body"*) printf '%s\n' "\${GH_BODY:-Refs #1200}";;
  *mergeable_state*) echo clean;;
  *) echo "";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
pp() { bash "$T/s/prepush.sh" "$@" 2>&1; }
r=$(pp 7) && ok "clean PR: no output, exit 0" || ko "clean PR flagged: $r"
r=$(GH_BODY="Closes #1200" pp 7); case "$r" in *"refs: closing keyword"*) ok "Closes #N flagged";; *) ko "Closes not flagged: '$r'";; esac
r=$(GH_BODY='Adds `UI/c.cs` and tests in UI/missing_tests.cs' pp 7); case "$r" in *"cited: the body cites UI/missing_tests.cs"*) case "$r" in *UI/c.cs*) ko "present file flagged";; *) ok "cited file missing from the head flagged, present one not";; esac;; *) ko "missing cited file not flagged: '$r'";; esac
r=$(GH_BODY='runner squad/gui_test_supervise.py and probe tmp/x/run.json; the fork-side scripts/zz.py' pp 7) && ok "paths outside the repo's dirs and fork lines skipped" || ko "false positive: $r"
c --allow-empty -m local; r=$(cd "$T/co" && GH_BODY="Refs #1200" pp --local); case "$r" in *"unpushed: 1 commit"*) ok "--local: unpushed commit flagged";; *) ko "--local unpushed not flagged: '$r'";; esac
$G reset -q --hard "$SHA"
# autopilot gate: a process-only problem becomes a soft CHANGES with no model review (revci never starts)
r=$(GH_BODY="Closes #1200" bash "$T/s/autopilot.sh" 2>&1)
case "$r" in *"PREPUSH #7"*) ok "autopilot: prepush failure -> PREPUSH, no revci";; *) ko "autopilot gate: '$r'";; esac
/usr/bin/grep -q "VERDICT: CHANGES (prepush)" "$T/st/revci-7.out" 2>/dev/null && /usr/bin/grep -q "closing keyword" "$T/st/review-7.md" && ok "autopilot: verdict line + findings for autofix" || ko "no prepush verdict/findings written"
rm -rf "$T"; exit $fail
