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
  *"/check-runs"*) echo success;;   # a head with a check run: the autopilot holds a head with none (CI MISSING) before it reaches the state hold
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
echo "#10 aaaaaaaaa clean CI=success:9 VERDICT: APPROVE" > "$T/st/revci-10.out"; echo "#12 ccccccccc blocked CI=success:9 VERDICT: APPROVE" > "$T/st/revci-12.out"
run() { bash "$T/s/autopilot.sh" 2>/dev/null; }
r=$(run)
case "$r" in *"HOLD #10 @ aaaaaaaaa: label"*) ok "APPROVE PR held by a label says so";; *) ko "no label HOLD: '$r'";; esac
case "$r" in *"HOLD #12 @ ccccccccc: GitHub reports 'blocked'"*) ok "APPROVE PR not clean says the state";; *) ko "no state HOLD: '$r'";; esac
r=$(run); case "$r" in *HOLD*) ko "HOLD repeated for the same head and reason: '$r'";; *) ok "same head and reason: no repeat";; esac
rm -rf "$T"; exit $fail
