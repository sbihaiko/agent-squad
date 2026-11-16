#!/bin/bash
# Rule (#1962): a spec whose open tickets made no commit in dev past the bound (docs/squad/defaults.yaml, janitor.spec_stall_seconds)
# raises one SPEC-STALL alert, naming each open ticket. It repeats at most once per bound. A ticket that lands inside the bound
# keeps its spec quiet. Stubbed gh and board; a temp state dir and a temp checkout with an origin/dev ref.
# usage: bash test-janitor-spec-stall.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_TAKEOVER_SECONDS=1000000000000
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/docs/squad"
cp "$src" "$T/s/janitor.py"; cp "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$d/specstall.py" "$d/spec_fold.py" "$d/ghcall.sh" "$T/s/"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\ncat "$GH_ISSUES"\n' > "$T/ghsnap"; chmod +x "$T/ghsnap"
NOW=$(date +%s); OLD=$(( NOW - 3 * 86400 ))
cat > "$T/issues.json" <<J
[{"number":10,"title":"Spec","body":"","labels":[{"name":"spec"}],"parent":null,"sub_issues":[11,12],"created_at":"2026-01-01T00:00:00Z"},
 {"number":11,"title":"Ticket one","body":"","labels":[],"parent":10,"sub_issues":[],"created_at":"2026-01-01T00:00:00Z"},
 {"number":12,"title":"Ticket two","body":"","labels":[],"parent":10,"sub_issues":[],"created_at":"2026-01-01T00:00:00Z"}]
J
git -C "$T/co" init -q
commit() { GIT_AUTHOR_DATE="@$1 +0000" GIT_COMMITTER_DATE="@$1 +0000" git -C "$T/co" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$2"; git -C "$T/co" update-ref refs/remotes/origin/dev HEAD; }
commit "$OLD" "Start the spec"
printf 'janitor:\n  spec_stall_seconds: 3600\n' > "$T/co/docs/squad/defaults.yaml"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" SG_GHSNAP="$T/ghsnap" GH_ISSUES="$T/issues.json" SQUAD_CAPS_DIR="$T/caps" python3 -I "$T/s/janitor.py" >/dev/null 2>"$T/janitor.err"; }
alerts() { cat "$T/st/alerts.log" 2>/dev/null | /usr/bin/grep -c 'SPEC-STALL #10'; }
pass; [ "$(alerts)" = 1 ] && ok "a spec with no dev progress past the bound raises one alert" || ko "no alert for a stalled spec ($(alerts)): $(tail -5 "$T/janitor.err")"
/usr/bin/grep 'SPEC-STALL #10' "$T/st/alerts.log" | /usr/bin/grep -q '#11.*#12\|#12.*#11' && ok "the alert names each stuck ticket" || ko "the alert names no tickets: $(cat "$T/st/alerts.log" 2>/dev/null)"
pass; [ "$(alerts)" = 1 ] && ok "the same stall raises nothing again inside the bound" || ko "the same stall alerted twice ($(alerts))"
rm -f "$T/st/alerts.log"; commit "$NOW" "Refs #11: land the first ticket"
pass; [ "$(alerts)" = 0 ] && ok "a spec whose ticket landed inside the bound raises no alert" || ko "a progressing spec alerted ($(alerts))"
[ "$fail" = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
