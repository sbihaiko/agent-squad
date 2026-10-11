#!/bin/bash
# Regression for a PR with no Refs #N: the autopilot must not ignore it silently.
# Either the PR enters the review path, or it gets exactly one skip comment (and a log
# line) per head. Stub gh, no network. usage: bash test-autopilot-norefs.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"; cp "$src" "$T/s/autopilot.sh"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" > "$T/s/env.sh"
printf '#!/bin/bash\nexit 0\n' > "$T/s/autofix.sh"
printf '#!/bin/bash\necho "#$1 REVCI" >> "$SG_STATE/revci-$1.out"\n' > "$T/s/revci.sh"
printf '#!/bin/bash\nexit 0\n' > "$T/s/prepush.sh"
chmod +x "$T/s/autofix.sh" "$T/s/revci.sh" "$T/s/prepush.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) echo '[{"number":11,"head":{"sha":"bbbbbbbbbbbb"},"title":"Play: a pad press","body":"No issue pointer here.","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"},{"number":10,"head":{"sha":"aaaaaaaaaaaa"},"title":"t","body":"Refs #1100","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]';;
  *"check-runs"*) echo success;;
  *"mergeable_state"*) echo clean;;
  *"pulls/10"*) echo "aaaaaaaaaaaa clean";;
  *"pulls/11"*) echo "bbbbbbbbbbbb clean";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { bash "$T/s/autopilot.sh" 2>/dev/null; }
r=$(run)
cq_skip() { ls "$T/st/cq/"*.md >/dev/null 2>&1 && grep -l 'Refs #N' "$T/st/cq/"*.md 2>/dev/null; }
entered() { case "$r" in *"AUTOREVIEW #11"*|*"PREPUSH #11"*|*"AUTOFIX queued #11"*|*"REREVIEW #11"*) return 0;; *) return 1;; esac; }
logged() { case "$r" in *"NOREFS #11"*) return 0;; *) return 1;; esac; }
if entered; then ok "PR without Refs #N entered the review path"
elif logged && [ -n "$(cq_skip)" ]; then ok "PR without Refs #N got a skip comment and a log line"
else ko "PR without Refs #N was silently ignored: out='$r' cq='$(ls "$T/st/cq" 2>/dev/null)'"; fi
if ! entered; then
  n1=$(ls "$T/st/cq/"*.md 2>/dev/null | wc -l | tr -d ' ')
  r2=$(run)
  n2=$(ls "$T/st/cq/"*.md 2>/dev/null | wc -l | tr -d ' ')
  case "$r2" in *"NOREFS #11"*) ko "skip log repeated for the same head: '$r2'";; *) ok "same head: no repeat skip log";; esac
  [ "$n1" = "$n2" ] && ok "skip comment once per PR head" || ko "skip comment posted again: $n1 -> $n2"
fi
rm -rf "$T"; exit $fail
