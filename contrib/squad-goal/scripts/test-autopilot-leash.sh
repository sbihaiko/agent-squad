#!/bin/bash
# #1501: a BLOCKED review that is only the aging leash timeout is not re-queued with that same leash (stub gh, no network).
# usage: bash test-autopilot-leash.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"; cp "$src" "$T/s/autopilot.sh"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" > "$T/s/env.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) echo '[{"number":10,"head":{"sha":"aaaaaaaaaaaa"},"title":"t","body":"Refs #1100","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]';;
  *"check-runs"*) echo success;;
  *"pulls/10 -q .mergeable_state"*) echo clean;;
esac
J
printf '#!/bin/bash\n[ "$1" = --threads ] && echo 0 && exit 0\n[ "$1" = --dupissue ] && exit 1\nexit 0\n' > "$T/s/autofix.sh"
printf '#!/bin/bash\necho "TIMEOUT=${SG_REVIEW_TIMEOUT-unset} DEPTH=${SG_REVIEW_CHAIN_DEPTH-} RETRY=${SG_REVIEW_RETRY_BUDGET-}" >> "$SG_STATE/revci-env.log"\n' > "$T/s/revci.sh"
chmod +x "$T/bin/gh" "$T/s/autofix.sh" "$T/s/revci.sh"; export PATH="$T/bin:$PATH"
echo $(( $(date +%s) - 3600 )) > "$T/st/auto/seen-10"
echo "#10 aaaaaaaaa clean CI=success:9 VERDICT: BLOCKED (every review model failed)" > "$T/st/revci-10.out"
echo "VERDICT: BLOCKED (every review model failed)" > "$T/st/review-10.md"
printf '%s\t%s\t%s\t%s\t%s\n' "2026-10-11T00:00:00" 10 "claude:claude-deepseek-v4-flash" 120 142 > "$T/st/review-times.tsv"
# hermetic: a live profile exporting SG_REVIEW_TIMEOUT (the coordinator workaround sets 360) must not reach the case
run() { env -u SG_REVIEW_TIMEOUT -u SG_REVIEW_CHAIN_DEPTH -u SG_REVIEW_RETRY_BUDGET bash "$T/s/autopilot.sh" 2>/dev/null; }
r=$(run)
for i in 1 2 3 4 5 6 7 8 9 10; do [ -s "$T/st/revci-env.log" ] && break; sleep 0.1; done
envl=$(cat "$T/st/revci-env.log" 2>/dev/null)
case "$r" in *REREVIEW*) ok "BLOCKED aging PR is re-queued";; *) ko "no REREVIEW: '$r'";; esac
case "$envl" in
  *TIMEOUT=unset*|*TIMEOUT=*)
    case "$envl" in *TIMEOUT=120*) ko "leash-only BLOCKED re-queued with the same 120s leash: '$envl'";;
      *) ok "leash-only BLOCKED is not re-queued with the same leash ($envl)";; esac;;
  *) ko "revci env not recorded: r='$r' env='$envl'";; esac
# a BLOCKED that is not a leash timeout still carries the aging leash
: > "$T/st/revci-env.log"; rm -f "$T/st/auto/rereview-10-aaaaaaaaa"
printf '%s\t%s\t%s\t%s\t%s\n' "2026-10-11T00:00:00" 10 "claude:claude-deepseek-v4-flash" 8 1 > "$T/st/review-times.tsv"
r=$(run)
for i in 1 2 3 4 5 6 7 8 9 10; do [ -s "$T/st/revci-env.log" ] && break; sleep 0.1; done
envl=$(cat "$T/st/revci-env.log" 2>/dev/null)
case "$envl" in *TIMEOUT=120*) ok "non-leash BLOCKED still uses the aging leash";; *) ko "non-leash BLOCKED lost the leash: '$envl' r='$r'";; esac
rm -rf "$T"; exit $fail
