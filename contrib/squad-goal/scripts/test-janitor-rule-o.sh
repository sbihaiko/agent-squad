#!/bin/bash
# Rule O (#1811): `landed` is the pass of the stage that lands the work, and the release must actually happen.
#  (A) a failed run whose only pass is the router's landed nothing -> its marker is dropped, and rule R (the card's one owner) writes Todo;
#  (B) a failed run that DID land (a `land` event with outcome pass, even when a later stage failed) keeps card and marker;
#  (C) an issue an open PR names keeps its card and its marker.
# The gh stub answers the same way the live API does, including its refusal of a POST to /search/issues:
# `gh api search/issues -f q=...` returns "Not Found", `gh api -X GET search/issues -f q=...` answers the real count.
# Stubbed gh/board; a temp state dir, so the live watch.log, markers and board are never read or written.
# usage: bash test-janitor-rule-o.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*)
    case "$a" in
      *"-X GET"*|*"--method GET"*) ;;
      *) printf '{\n  "message": "Not Found",\n  "documentation_url": "https://docs.github.com/rest"\n}\n'; exit 1;;
    esac
    echo "$a" >> "TMPDIR/search.calls"
    case "$a" in *"is:merged"*) echo 0;; *"Refs #803"*) echo 1;; *) echo 0;; esac;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 801 802 803; do printf '%s\tIn Progress\n' "$n"; done;;   # one read, every card
  *projectsV2/1/items*) for n in 801 802 803; do printf '%s\tDoing\n' "$n"; done;;
  *issues/80*) echo open;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"; }
# A: failed run, nothing landed, the router's own pass is the only pass in the record
mk run-router 801
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #801 on fix/801-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": ["provisional: dispatched the child"], "pos": 3}\n{"event": "verdict", "verdict": "failed", "reasons": ["no_landed_change"], "exit_code": 1}\n' > "$T/co/runs/run-router/record.jsonl"
echo run-router > "$T/st/auto/disp-801"
# B: failed run that landed a change: a land pass, then a later stage that failed
mk run-land 802
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #802 on fix/802-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "a7df8f1ecff77bdb5833721121a7a50be4066053"}}\n{"event": "outcome", "node": "final_gate", "entry": 1, "outcome": "fail", "reasons": ["ac-verify:no_ac_commands"]}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:no_ac_commands"], "exit_code": 1}\n' > "$T/co/runs/run-land/record.jsonl"
echo run-land > "$T/st/auto/disp-802"
# C: failed run that landed nothing, but an open PR names the issue
mk run-pr 803
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #803 on fix/803-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": [], "pos": 3}\n{"event": "verdict", "verdict": "failed", "reasons": ["adapter_error"], "exit_code": 1}\n' > "$T/co/runs/run-pr/record.jsonl"
echo run-pr > "$T/st/auto/disp-803"
# D: a child worktree took this run folder with it. Its marker still names the deleted run, so Rule O must release it.
echo "$T/co/worktrees/child/autobug-20261013-224513" >> "$T/st/active-runs.txt"
echo autobug-20261013-224513 > "$T/st/auto/bug-804"
# E: a run folder still present inside a child worktree remains its issue's owner.
mkdir -p "$T/co/worktrees/child/autobug-live"
printf '{"event": "spawn_accepted", "definition": "GitHub issue #805"}\n' > "$T/co/worktrees/child/autobug-live/record.jsonl"
echo "$T/co/worktrees/child/autobug-live" >> "$T/st/active-runs.txt"
echo autobug-live > "$T/st/auto/bug-805"
touch -t 202001010000 "$T/co/runs"/run-*/record.jsonl
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 801$' "$T/board.calls" 2>/dev/null && ok "O: failed run whose only pass is the router's -> card back to Todo" || ko "O #801 not demoted: $(cat "$T/board.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-801" ] && ok "O: failed run whose only pass is the router's -> marker dropped" || ko "O disp-801 kept: $o"
/usr/bin/grep -q 'DISPATCH-FAILED #801' "$T/st/cycle.log" 2>/dev/null && ok "O: reported run + reason" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'no_landed_change' "$T/st/cq/"*801* >/dev/null 2>&1 && /usr/bin/grep -q 'run-router' "$T/st/cq/"*801* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Done 802$' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-802" ] && ok "O: a run that landed is reconciled to Done and keeps its marker" || ko "O #802 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Code Complete 803$' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-803" ] && ok "O: an issue an open PR names moves to Code Complete and keeps its marker" || ko "O #803 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q 'Refs #803' "$T/search.calls" 2>/dev/null && ok "O: the PR lookup reached the API (GET, not a rejected POST)" || ko "O: no PR lookup answered: $(cat "$T/search.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/bug-804" ] && ok "O: a deleted child-worktree run releases its bug marker" || ko "O bug-804 kept: $o"
/usr/bin/grep -q 'MISSING.*#804.*bug-804.*dropped' "$T/st/cycle.log" 2>/dev/null && ok "O: the missing-run release is named in cycle.log" || ko "O missing-run cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -qx "$T/co/worktrees/child/autobug-20261013-224513" "$T/st/active-runs.txt" 2>/dev/null && ko "O: missing run stays active" || ok "O: a deleted run is dropped from active-runs"
[ -e "$T/st/auto/bug-805" ] && /usr/bin/grep -qx "$T/co/worktrees/child/autobug-live" "$T/st/active-runs.txt" 2>/dev/null && ok "O: a live child-worktree run keeps its marker" || ko "O bug-805 not kept with live run: $o"
rm -rf "$T"; exit $fail
