#!/bin/bash
# #1768: the launched-<pr>-<sha9> marker must heal itself. autopilot.sh writes it when it starts a review of a head and
# skips that head while the marker exists. A revci.sh killed before it appended its summary line -- by a restart, by the
# janitor, by the shell -- left the marker behind with no running review and no summary line, and nothing re-queued the
# head: only a new push moves the sha, so the PR sat mergeable and CI-green with no verdict, its card showing `reviewing`
# forever (PR #1706, 2026-10-11: auto/launched-1706-c8bc786eb survived a revci-1706.out holding only `Terminated: 15`).
# The marker now records the pid of the review it started: while that pid is alive the head is respected (no duplicate
# review of the same head), and once it is gone -- with the summary line for the head still missing -- the marker is
# dropped and the head is reviewed again.
# usage: bash test-autopilot-stale-launch.sh [path/to/autopilot.sh]
# The report is buffered so the first line says TDD RED while the fix is missing (run it against the base autopilot:
# `bash test-autopilot-stale-launch.sh <(git show <base>:contrib/squad-goal/scripts/autopilot.sh)`) and TDD GREEN once it is in.
src=${1:-$(dirname "$0")/autopilot.sh}
T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
G=/usr/bin/grep
# A PR number no live loop can be holding a review for: the pass skips a head when `revci.sh <pr>` is running anywhere on
# the machine, so a real PR number (say 10) would make these assertions depend on the host's process table.
PR=4176; ISS=1768; SHA=c8bc786eb123; S9=${SHA:0:9}; MK=launched-$PR-$S9

scene() {  # $1 = scene name: builds $T/<name>/{s,st,bin}; leftovers reachable as $SCENE
  local S=$T/$1
  mkdir -p "$S/s" "$S/st/auto" "$S/st/cq" "$S/bin"
  cp "$src" "$S/s/autopilot.sh"
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$S/st" > "$S/s/env.sh"
  printf '#!/bin/bash\n[ "$1" = --threads ] && echo 0 && exit 0\n[ "$1" = --dupissue ] && exit 1\nexit 0\n' > "$S/s/autofix.sh"
  printf '#!/bin/bash\nexit 0\n' > "$S/s/prepush.sh"
  # the review stub records every launch, so a second review of the same head is visible
  printf '#!/bin/bash\nprintf "launched %%s\\n" "$1" >> "$SG_STATE/revcilog"\n' > "$S/s/revci.sh"
  chmod +x "$S/s/autofix.sh" "$S/s/prepush.sh" "$S/s/revci.sh"
  cat > "$S/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"pulls?state=open"*) echo '[{"number":$PR,"head":{"sha":"$SHA","ref":"gui/1768"},"title":"t","body":"Refs #$ISS","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]';;
  *"check-runs"*) echo success;;
  *"mergeable_state"*) echo clean;;
  *"pulls/$PR"*) echo "$SHA clean";;
esac
J
  chmod +x "$S/bin/gh"
  export PATH="$S/bin:$PATH" SG_STATE="$S/st"
  echo $(( $(date +%s) - 3600 )) > "$S/st/auto/seen-$PR"      # an old PR: it leads the queue
  printf 'Terminated: 15\n' > "$S/st/revci-$PR.out"          # #1706: everything the killed review left behind, no summary line
  SCENE=$S
}

run() { bash "$SCENE/s/autopilot.sh" 2>/dev/null; }
launched() { cat "$SCENE/st/revcilog" 2>/dev/null; }
waited() { local i; for i in 1 2 3 4 5 6 7 8 9 10; do [ -n "$(launched)" ] && break; sleep 0.1; done; launched; }
marker() { cat "$SCENE/st/auto/$MK" 2>/dev/null; }

# --- (b) the review this head's marker started is still running: the marker is respected, the head is not reviewed twice
scene alive
sleep 300 & livepid=$!
printf '%s\n' "$livepid" > "$SCENE/st/auto/$MK"
r=$(run)
if [ -z "$(launched)" ]; then ok "live review: no second review of the same head"; else ko "the head was reviewed twice: $(launched)"; fi
if [ "$(marker)" = "$livepid" ]; then ok "live review: the marker still names the running revci"; else ko "the live marker was rewritten: '$(marker)'"; fi
case "$r" in *STALE*) ko "a running review was called stale: '$r'";; *) ok "live review: not called stale";; esac
kill $livepid 2>/dev/null

# --- (a) the review died with no summary line: the marker is stale, the head is re-queued (#1706)
scene stale
printf '999999\n' > "$SCENE/st/auto/$MK"
r=$(run)
case "$(waited)" in *"launched $PR"*) ok "dead review: the head is re-queued";; *) ko "dead review: nothing re-queued the head (out='$r')";; esac
case "$r" in *"STALE-REVIEW #$PR @ $S9"*) ok "dead review: STALE-REVIEW #$PR @ $S9";; *) ko "dead review: no STALE-REVIEW line: '$r'";; esac
np=$(marker); case "$np" in ""|999999) ko "dead review: the marker was not replaced by the re-queued review's pid ('$np')";; *) ok "dead review: the marker names the re-queued review ($np)";; esac

# --- the #1706 shape itself: the marker the old code wrote carries no pid at all (`touch`), and the review is gone
scene legacy
: > "$SCENE/st/auto/$MK"
r=$(run)
case "$(waited)" in *"launched $PR"*) ok "pid-less marker: the head is re-queued";; *) ko "pid-less marker: nothing re-queued the head (out='$r')";; esac
np=$(marker); [ -n "$np" ] && ok "pid-less marker: the marker now carries a pid ($np)" || ko "pid-less marker: the marker is empty again"

# --- a head whose summary line did land is left alone: the marker's pid is irrelevant once the review answered
scene answered
printf '999999\n' > "$SCENE/st/auto/$MK"
printf '#%s %s clean CI=success:9 VERDICT: APPROVE\n' "$PR" "$S9" >> "$SCENE/st/revci-$PR.out"
r=$(run)
[ -z "$(launched)" ] && ok "answered head: no review is started again" || ko "answered head was reviewed again: $(launched)"
[ "$(marker)" = 999999 ] && ok "answered head: the marker is untouched" || ko "answered head: the marker became '$(marker)'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
