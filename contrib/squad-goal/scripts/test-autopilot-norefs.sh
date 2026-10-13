#!/bin/bash
# Regression for a PR with no Refs #N (JSON-issue #1499, healed by #1507): the autopilot must not ignore it silently.
# When the issue can be derived -- the branch is gui/<N>, or the run that produced the branch names it -- the pass
# appends `Refs #N` to the PR body with ONE REST PATCH and logs REFSADDED #<pr> #<n>. When it cannot, #1499 stands:
# one skip comment (and one NOREFS log line) per head, and no body write. Stub gh, no network.
# usage: bash test-autopilot-norefs.sh [path/to/autopilot.sh]
# The report is buffered so the first line says TDD RED while the fix is missing (run it against the base autopilot:
# `bash test-autopilot-norefs.sh <(git show <base>:contrib/squad-goal/scripts/autopilot.sh)`) and TDD GREEN once it is in.
src=${1:-$(dirname "$0")/autopilot.sh}; here=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
G=/usr/bin/grep

scene() {  # $1 = scene name: builds $T/<name>/{s,st,co,bin}; leftovers reachable as $SCENE
  local S=$T/$1
  mkdir -p "$S/s" "$S/st/auto" "$S/st/cq" "$S/bin" "$S/co/runs"
  cp "$src" "$S/s/autopilot.sh"
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT=%s BOARD=true\n' "$S/st" "$S/co" > "$S/s/env.sh"
  echo ". \"$(dirname "$src")/address.sh\"" >> "$S/s/env.sh"   # the writers name a queued comment through the seam, as the real env.sh loads it
  printf '#!/bin/bash\nexit 0\n' > "$S/s/autofix.sh"
  printf '#!/bin/bash\nexit 0\n' > "$S/s/revci.sh"
  printf '#!/bin/bash\nexit 0\n' > "$S/s/prepush.sh"
  chmod +x "$S/s/autofix.sh" "$S/s/revci.sh" "$S/s/prepush.sh"
  : > "$S/body11"; : > "$S/body10"; : > "$S/patchlog"; : > "$S/ghlog"
  export BODY11="$S/body11" BODY10="$S/body10" PATCHLOG="$S/patchlog" GHLOG="$S/ghlog" OPENJSON="$S/open.json"
  cat > "$S/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "$GHLOG"
if [ "$1" = api ] && [ "$2" = -X ] && [ "$3" = PATCH ]; then
  pr=; body=
  for a in "$@"; do
    case "$a" in
      */pulls/[0-9]*) pr=${a##*/pulls/};;
      body=*) body=${a#body=};;
    esac
  done
  printf 'PATCH %s\n%s\n' "$pr" "$body" >> "$PATCHLOG"
  if [ "$pr" = 11 ]; then printf '%s' "$body" > "$BODY11"
  elif [ "$pr" = 10 ]; then printf '%s' "$body" > "$BODY10"; fi
  exit 0
fi
case "$*" in
  *"-q .body")
    case "$*" in *pulls/11*) printf '%s' "$(cat "$BODY11")";; *pulls/10*) printf '%s' "$(cat "$BODY10")";; esac;;
  *"pulls?state=open"*) cat "$OPENJSON";;
  *"check-runs"*) echo success;;
  *"mergeable_state"*) echo clean;;
  *"pulls/10"*) echo "aaaaaaaaaaaa clean";;
  *"pulls/11"*) echo "bbbbbbbbbbbb clean";;
esac
J
  chmod +x "$S/bin/gh"
  export PATH="$S/bin:$PATH" SG_STATE="$S/st"
  SCENE=$S
}

run() { bash "$SCENE/s/autopilot.sh" 2>/dev/null; }
pr11() { printf '[{"number":11,"head":{"sha":"bbbbbbbbbbbb","ref":"%s"},"title":"Play: a pad press","body":"No issue pointer here.","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]' "$1" > "$OPENJSON"; }
skipped() { case "$1" in *"NOREFS #11"*) return 0;; *) return 1;; esac; }

# --- #1499: no Refs #N and nothing derives the issue -> one skip comment per head, no body write
scene skip; printf 'No issue pointer here.' > "$BODY11"; pr11 "fix/no-issue"
r=$(run)
if skipped "$r" && [ "$(ls "$SCENE/st/cq/x__y#"*norefs* 2>/dev/null | wc -l | tr -d ' ')" = 1 ]; then ok "underivable PR: skip comment + NOREFS line"; else ko "underivable PR not reported: out='$r'"; fi
[ ! -s "$PATCHLOG" ] && ok "underivable PR: no body PATCH" || ko "underivable PR was PATCHed: $(cat "$PATCHLOG")"
[ "$(cat "$BODY11")" = "No issue pointer here." ] && ok "underivable PR body unchanged" || ko "underivable body became '$(cat "$BODY11")'"
n1=$(ls "$SCENE/st/cq/x__y#"*norefs* 2>/dev/null | wc -l | tr -d ' '); r2=$(run); n2=$(ls "$SCENE/st/cq/x__y#"*norefs* 2>/dev/null | wc -l | tr -d ' ')
case "$r2" in *"NOREFS #11"*) ko "skip repeated for the same head: '$r2'";; *) ok "same head: skip stays once";; esac
[ "$n1" = "$n2" ] && ok "skip comment once per head" || ko "skip comment posted again: $n1 -> $n2"

# --- the run that produced the branch names issue #1101 -> the pass appends Refs #1101 itself
scene heal; printf 'No issue pointer here.' > "$BODY11"; pr11 "fix/1101-pad"
mkdir -p "$SCENE/co/runs/run-1101"
printf '{"event":"spawn_accepted","definition":"GitHub issue #1101 on fix/1101-pad"}\n{"event":"verdict","verdict":"pass"}\n' > "$SCENE/co/runs/run-1101/record.jsonl"
r=$(run)
$G -q 'No issue pointer here.' "$BODY11" && $G -q '^Refs #1101$' "$BODY11" && ok "derived issue: body keeps the old text and gains Refs #1101" \
  || ko "body did not heal: '$(cat "$BODY11")' out='$r'"
case "$r" in *"REFSADDED #11 #1101"*) ok "derived issue: REFSADDED #11 #1101";; *) ko "no REFSADDED line: '$r'";; esac
case "$r" in *"NOREFS #11"*) ko "healed PR still logged NOREFS: '$r'";; *) ok "healed PR did not skip";; esac
[ -z "$(ls "$SCENE/st/cq/x__y#"*norefs* 2>/dev/null)" ] && ok "healed PR: no skip comment" || ko "healed PR got a skip comment"
[ "$(printf '%s' "$(cat "$PATCHLOG")" | $G -c '^PATCH 11$')" = 1 ] && ok "healed PR: exactly one body PATCH" || ko "PATCH count=$(printf '%s' "$(cat "$PATCHLOG")" | $G -c '^PATCH 11$')"
printf '%s' "$(cat "$PATCHLOG")" | $G -q 'body=Refs\|Refs #1101' && ok "the PATCH carries the Refs line" || ko "PATCH body lacks the line: $(cat "$PATCHLOG")"

# --- fork work: gui/<N> derives N with no run record at all
scene gui; printf 'fork body' > "$BODY11"; pr11 "gui/1107"
r=$(run)
$G -q '^Refs #1107$' "$BODY11" && ok "gui/<N>: body ends the pass with Refs #1107" || ko "gui/<N> body: '$(cat "$BODY11")' out='$r'"
case "$r" in *"REFSADDED #11 #1107"*) ok "gui/<N>: REFSADDED #11 #1107";; *) ko "gui/<N>: no REFSADDED: '$r'";; esac

# --- a PR that already carries Refs #N is left alone: no duplicate line, no body rewrite
scene keep; printf 'intro\nRefs #1100\n' > "$BODY10"
printf '[{"number":10,"head":{"sha":"aaaaaaaaaaaa","ref":"gui/1100"},"title":"t","body":"intro\nRefs #1100\n","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]' > "$OPENJSON"
r=$(run)
[ ! -s "$PATCHLOG" ] && ok "existing Refs #N: no body PATCH" || ko "existing Refs was rewritten: $(cat "$PATCHLOG")"
[ "$(cat "$BODY10")" = "$(printf 'intro\nRefs #1100')" ] && ok "existing Refs #N body untouched" || ko "existing body became '$(cat "$BODY10")'"
case "$r" in *"REFSADDED #10"*) ko "existing Refs logged REFSADDED: '$r'";; *) ok "existing Refs: no REFSADDED";; esac

# --- the dispatch request template carries the literal line the child pastes
MK=$T/mk; mkdir -p "$MK/scripts" "$MK/bin" "$MK/home"
cp "$here/mkreq.sh" "$here/startability.sh" "$here/models.json" "$here/req-checklist.txt" "$MK/scripts/"   # startability.sh: the one source of the fork branch (#1836)
echo 'export SG_REPO=o/r' > "$MK/scripts/env.sh"
printf '#!/bin/bash\ncase "$*" in *"/files"*) echo scripts/shared.py;; *pulls\\?state=open*) echo 7;; esac\n' > "$MK/bin/gh"
chmod +x "$MK/bin/gh"
export HOME=$MK/home PATH="$MK/bin:$PATH"
printf 'IMPORTANT: your first and only action is X\nEdit scripts/z.py\n' > "$MK/body"
bash "$MK/scripts/mkreq.sh" ac4 "$MK/body" >/dev/null 2>&1
req=$HOME/.cache/squad-goal/req/ac4.txt
if [ -f "$req" ] && $G -qF 'Refs #<N>' "$req"; then ok "dispatch request carries the literal Refs #<N> line"
else ko "dispatch request lacks the literal line: $(head -c 400 "$req" 2>/dev/null)"; fi

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
