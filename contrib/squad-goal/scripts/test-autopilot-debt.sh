#!/bin/bash
# Regression test for the debt-only CHANGES soft-merge path (ADR-0273 Decision 3 and 5): stub gh, stub filer, no network. usage: bash test-autopilot-debt.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin"; cp "$src" "$T/s/autopilot.sh"; cp "$(dirname "$src")/revformat.py" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" > "$T/s/env.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) echo '[{"number":10,"head":{"sha":"aaaaaaaaaaaa"},"title":"t","body":"Refs #1100","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]';;
  *"issues?labels=debt"*) i=0; while [ $i -lt ${STUB_OPEN_DEBT:-0} ]; do i=$((i+1)); echo $i; done;;
  *"check-runs"*) echo "success,success";;
  *"pulls/10 -q .head.sha"*) echo "aaaaaaaaaaaa clean";;
  *"pulls/10 -q .changed_files"*) echo 3;;
  *"pulls/10/merge"*) echo merged >> "$STUB_LOG"; echo bbbbbbbbbbbb;;
esac
J
cat > "$T/filer" <<'J'
#!/bin/bash
echo filed >> "$STUB_LOG"; [ "${STUB_FILER_FAIL:-0}" = 1 ] && exit 1; exit 0
J
printf '#!/bin/bash\n[ "$1" = --threads ] && echo 0\n' > "$T/s/autofix.sh"
chmod +x "$T/bin/gh" "$T/filer"; export PATH="$T/bin:$PATH" STUB_LOG="$T/log" SG_DEBT_FILER="$T/filer"
# case <name> <verdict-line suffix> <review body> <env...> -> sets $log
scenario() {
  rm -rf "$T/st" "$T/log"; mkdir -p "$T/st/auto"; touch "$T/log"; echo "#10 aaaaaaaaa clean CI=success:9 VERDICT: CHANGES$2" > "$T/st/revci-10.out"
  printf '%s\n' "$3" > "$T/st/review-10.md"; shift 3; ( export "$@"; bash "$T/s/autopilot.sh" >"$T/out" 2>&1 ); log=$(tr '\n' ' ' < "$T/log")
}
# #1777: the severity of a finding is the section it sits in, so the soft path reads the shape (revformat.py), not a tag
DEBT=$'VERDICT: CHANGES\n## Debt\n- a.c:3 — name is vague — the reader cannot tell what it names — rename it'
scenario 1 "" "$DEBT" X=1
[ "$log" = "filed merged " ] && ok "1 debt-only CHANGES: filed, then merged" || ko "1 expected 'filed merged', got '$log'"
scenario 2 "" $'VERDICT: CHANGES\n## Blocking\n- a.c:9 — regression — main had the old behaviour — restore it\n## Debt\n- a.c:3 — name is vague — the reader cannot tell what it names — rename it' X=1
case "$log" in *merged*|*filed*) ko "2 a BLOCKING finding must not file or merge: '$log'";; *) ok "2 any BLOCKING finding: no merge, fix round";; esac
scenario 3 " (prepush)" "$DEBT" X=1
case "$log" in *merged*|*filed*) ko "3 a prepush CHANGES must not merge: '$log'";; *) ok "3 prepush CHANGES: a round, never a merge";; esac
scenario 4 "" "$DEBT" STUB_FILER_FAIL=1
case "$log" in *merged*) ko "4 merged although the filer failed: '$log'";; *filed*) ok "4 filer fails: pull request left unmerged";; *) ko "4 filer never ran: '$log'";; esac
scenario 5 "" "$DEBT" STUB_OPEN_DEBT=16
case "$log" in *merged*|*filed*) ko "5 past SG_DEBT_MAX must cost a round: '$log'";; *) ok "5 16 open debts (> 15): soft path off";; esac
# 7) #1777: a '## Debt' heading with no bullet under it is not a finding, so there is nothing to file: the soft path
# must read the bullets of the section (revformat.py --summary), not the word in the heading
scenario 7 "" $'VERDICT: CHANGES\n## Debt\n' X=1
case "$log" in *merged*|*filed*) ko "7 an empty Debt section took the soft path: '$log'";; *) ok "7 an empty Debt section: a round, nothing to file";; esac
scenario 6 "" "$DEBT" STUB_OPEN_DEBT=3 SG_DEBT_MAX=2
case "$log" in *merged*) ko "6 SG_DEBT_MAX override ignored: '$log'";; *) ok "6 SG_DEBT_MAX is configurable";; esac
[ -n "$DBG" ] && cat "$T/out"; rm -rf "$T"; exit $fail
