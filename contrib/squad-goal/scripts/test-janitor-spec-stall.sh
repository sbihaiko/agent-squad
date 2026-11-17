#!/bin/bash
# Rule (#1962): a spec whose open tickets made no commit in dev past the bound (docs/squad/defaults.yaml, janitor.spec_stall_seconds)
# raises one SPEC-STALL alert, naming each open ticket. It repeats at most once per bound. A ticket that lands inside the bound
# keeps its spec quiet. Rule (#2002, ADR-0142 clause 5): the alert names only a spec whose card sits in Todo or a later column,
# because only the owner's move starts a spec; a spec in Backlog, or with no card, is no call for the owner. Only a dev commit
# that closes or fixes a ticket is progress (tests/test_spec_stall.py holds that half).
# Stubbed gh and board; a temp state dir and a temp checkout with an origin/dev ref.
# usage: bash test-janitor-spec-stall.sh [janitor.py]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
export SG_TAKEOVER_SECONDS=1000000000000
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/docs/squad"
cp "$src" "$T/s/janitor.py"; cp "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$d/specstall.py" "$d/spec_fold.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$T/s/"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/notify"; export PATH="$T/bin:$PATH"
# The one items read answers the cards in $T/cards, one `<number> TAB <Status>` a line; every other route answers nothing.
printf '#!/bin/bash\ncase "$*" in\n  *projectsV2/4/fields*) echo FID4;;\n  *projectsV2/4/items*) cat "%s/cards" 2>/dev/null;;\nesac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
cards() { printf '%b' "$1" > "$T/cards"; }   # a card per line, `10\tTodo`: the caller states the whole board
quiet() { rm -f "$T/st/alerts.log" "$T/st/spec-stall-10"; }   # a clean slate, so no earlier alert or its repeat marker decides the case
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
quiet; cards '10\tTodo\n'
pass; [ "$(alerts)" = 1 ] && ok "a spec in Todo with no dev progress past the bound raises one alert" || ko "no alert for a stalled spec ($(alerts)): $(tail -5 "$T/janitor.err")"
/usr/bin/grep 'SPEC-STALL #10' "$T/st/alerts.log" | /usr/bin/grep -q '#11.*#12\|#12.*#11' && ok "the alert names each stuck ticket" || ko "the alert names no tickets: $(cat "$T/st/alerts.log" 2>/dev/null)"
pass; [ "$(alerts)" = 1 ] && ok "the same stall raises nothing again inside the bound" || ko "the same stall alerted twice ($(alerts))"
quiet; cards '10\tBacklog\n'
pass; [ "$(alerts)" = 0 ] && ok "a spec in Backlog raises no alert" || ko "a Backlog spec alerted ($(alerts)): the owner has not started it"
quiet; cards ''
pass; [ "$(alerts)" = 0 ] && ok "a spec with no card raises no alert" || ko "a spec with no card alerted ($(alerts))"
quiet; cards '10\tTodo\n'
pass; [ "$(alerts)" = 1 ] && ok "a spec the owner moved to Todo alerts again" || ko "a Todo spec stopped alerting ($(alerts))"
rm -f "$T/st/alerts.log"; commit "$NOW" "Closes #11: land the first ticket"
pass; [ "$(alerts)" = 0 ] && ok "a spec whose ticket closed inside the bound raises no alert" || ko "a progressing spec alerted ($(alerts))"
[ "$fail" = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
