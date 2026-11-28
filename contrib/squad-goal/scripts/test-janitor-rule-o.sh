#!/bin/bash
# seconds: 1.24
# Rule O (#1811): `landed` is the pass of the stage that lands the work, and the release must actually happen.
#  (A) a failed run whose only pass is the router's landed nothing -> its marker is dropped, and rule R (the card's one owner) writes Todo;
#  (B) a failed run that DID land (a `land` event with outcome pass, even when a later stage failed), and whose landing reached
#      the landing branch (its head is an ancestor of origin/dev), keeps card and marker, and the card stops at Batching (#239: not Done);
#  (G) a failed run whose landing is only its local land commit (the head is in no origin/dev) delivered nothing (#691, ADR-0143):
#      the card goes back to Todo and the marker is dropped, so the issue dispatches again;
#  (C) an issue a work branch names keeps its marker (#382: a branch, never a pull request), but a failed run's branch is stale:
#      the card goes back to Todo, never forward to Batching (#660; ADR-0143: nothing delivered is the one backward move).
# The gh stub answers the same way the live API does, including its refusal of a POST to /search/issues:
# `gh api search/issues -f q=...` returns "Not Found", `gh api -X GET search/issues -f q=...` answers the real count.
# Stubbed gh/board; a temp state dir, so the live watch.log, markers and board are never read or written.
# usage: bash test-janitor-rule-o.sh [janitor.py]
# (#3107) The janitor loads squad/procs.py from the tree it runs in: it walks up from its own directory, then SG_SQUAD,
# SG_CHECKOUT and cwd. The daily pass clears every SG_* and runs from a cwd outside the repository, so the suite drops the
# inherited names and symlinks the repository's squad package beside the janitor copy. The product walk-up then answers
# with no SG_SQUAD and no cwd to stand in for the tree.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)'); do unset "$v"; done   # a caller's SG_SQUAD or SG_CHECKOUT never answers for this suite
repo=$(cd "$(dirname "$src")/../../.." && pwd -P)
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; ln -s "$repo/squad" "$T/s/squad"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/board_facts.py" "$T/s/"
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
  # a work branch names the issue (#382: the janitor reads branches, never pull requests): #803 has one, ahead of the landing branch
  *repos/x/y/branches*) echo "$a" >> "TMPDIR/branch.calls"; printf 'fix/803-x sha803\n';;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 801 802 803 807 808; do printf '%s\tIn Progress\n' "$n"; done;;   # one read, every card
  *projectsV2/1/items*) for n in 801 802 803; do printf '%s\tDoing\n' "$n"; done;;
  *issues/806*) [ -e "TMPDIR/806ok" ] && echo open || exit 1;;   # F: the first state read is refused, the next answers
  *issues/80*) echo open;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The checkout's origin/dev holds one commit; a second commit sits only on a local work branch, as a run's own land commit does (#691).
g() { /usr/bin/git -C "$T/co" -c user.name=t -c user.email=t@t -c commit.gpgsign=false "$@"; }
g init -q; g commit -q --allow-empty -m base; g update-ref refs/remotes/origin/dev HEAD; inbase=$(g rev-parse HEAD)
g commit -q --allow-empty -m 'squad: land route.router.1 (run-local)'; local=$(g rev-parse HEAD)
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"; }
# A: failed run, nothing landed, the router's own pass is the only pass in the record
mk run-router 801
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #801 on fix/801-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": ["provisional: dispatched the child"], "pos": 3}\n{"event": "verdict", "verdict": "failed", "reasons": ["no_landed_change"], "exit_code": 1}\n' > "$T/co/runs/run-router/record.jsonl"
echo run-router > "$T/st/auto/disp-801"
# B: failed run that landed a change: a land pass, then a later stage that failed
mk run-land 802
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #802 on fix/802-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "%s"}}\n{"event": "outcome", "node": "final_gate", "entry": 1, "outcome": "fail", "reasons": ["ac-verify:no_ac_commands"]}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:no_ac_commands"], "exit_code": 1}\n' "$inbase" > "$T/co/runs/run-land/record.jsonl"
echo run-land > "$T/st/auto/disp-802"
# C: failed run that landed nothing, but a work branch names the issue
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
# F: a failed run that landed nothing, whose issue's state read is refused on the first pass (#534): the next pass releases it
mk run-flaky 806
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #806 on fix/806-x"}\n{"event": "verdict", "verdict": "failed", "reasons": ["run_spend_cap"], "exit_code": 1}\n' > "$T/co/runs/run-flaky/record.jsonl"
echo run-flaky > "$T/st/auto/disp-806"
# G: failed run that landed only locally: its land commit is on its work branch and never reached origin/dev (#691)
mk run-local 807
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #807 on gui/807-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "before": "%s", "head": "%s"}}\n{"event": "outcome", "node": "final_gate", "entry": 1, "outcome": "fail", "reasons": ["ac-verify:fast-gate"]}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:fast-gate"], "exit_code": 1}\n' "$inbase" "$local" > "$T/co/runs/run-local/record.jsonl"
echo run-local > "$T/st/auto/disp-807"
# G (#771): the child of run-local left its commit in its own tree, on a branch no remote holds: the release names it for salvage
g worktree add -q -b gui/807-x "$T/co/runs/run-local/ws/c1" "$local"
touch -t 202001010000 "$T/co/runs"/run-*/record.jsonl
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 801$' "$T/board.calls" 2>/dev/null && ok "O: failed run whose only pass is the router's -> card back to Todo" || ko "O #801 not demoted: $(cat "$T/board.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-801" ] && ok "O: failed run whose only pass is the router's -> marker dropped" || ko "O disp-801 kept: $o"
/usr/bin/grep -q 'DISPATCH-FAILED #801' "$T/st/cycle.log" 2>/dev/null && ok "O: reported run + reason" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'no_landed_change' "$T/st/cq/"*801* >/dev/null 2>&1 && /usr/bin/grep -q 'run-router' "$T/st/cq/"*801* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Batching 802$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Done 802' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-802" ] && ok "O: a failed run whose landing reached origin/dev stops at Batching, never Done, and keeps its marker" || ko "O #802 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Todo 803$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Batching 803' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-803" ] && ok "O: a failed run's stale work branch keeps its marker, and its card goes back to Todo" || ko "O #803 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Todo 807$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -Eq 'Batching 807|Done 807' "$T/board.calls" 2>/dev/null && ok "R: a failed run whose landing never reached origin/dev delivered nothing, so its card goes back to Todo (#691)" || ko "R #807 not back in Todo: $(cat "$T/board.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/disp-807" ] && /usr/bin/grep -q 'DISPATCH-FAILED #807' "$T/st/cycle.log" 2>/dev/null && ok "O: a failed run's local-only landing drops its marker, so the issue dispatches again (#691)" || ko "O disp-807 kept: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q "DISPATCH-FAILED #807.*salvage: gui/807-x ${local:0:9}" "$T/st/cycle.log" 2>/dev/null && /usr/bin/grep -q "gui/807-x ${local:0:9}" "$T/st/cq/"*807* 2>/dev/null && ok "O: a failed run's unpushed child commits are named for salvage in the log and the comment (#771)" || ko "O #807 salvage not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -Eqx "gui/807-x ${local:0:9} in .*/co/runs/run-local/ws/c1" "$T/st/auto/salvage-807" 2>/dev/null && ok "O: the salvage tree is kept as auto/salvage-807, so the next dispatch names it (#771)" || ko "O #807 no salvage fact: $(cat "$T/st/auto/salvage-807" 2>&1)"
/usr/bin/grep -q "never pushes" "$T/st/cq/"*807* 2>/dev/null && ok "O: the comment says the loop flags the commits and never pushes them (#361, #771)" || ko "O #807 comment does not say the loop never pushes: $(cat "$T/st/cq/"*807* 2>/dev/null)"
/usr/bin/grep -q 'repos/x/y/branches' "$T/branch.calls" 2>/dev/null && ok "O: the work-branch lookup reached the API" || ko "O: no work-branch lookup answered: $(cat "$T/branch.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/bug-804" ] && ok "O: a deleted child-worktree run releases its bug marker" || ko "O bug-804 kept: $o"
/usr/bin/grep -q 'MISSING.*#804.*bug-804.*dropped' "$T/st/cycle.log" 2>/dev/null && ok "O: the missing-run release is named in cycle.log" || ko "O missing-run cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -qx "$T/co/worktrees/child/autobug-20261013-224513" "$T/st/active-runs.txt" 2>/dev/null && ko "O: missing run stays active" || ok "O: a deleted run is dropped from active-runs"
[ -e "$T/st/auto/bug-805" ] && /usr/bin/grep -qx "$T/co/worktrees/child/autobug-live" "$T/st/active-runs.txt" 2>/dev/null && ok "O: a live child-worktree run keeps its marker" || ko "O bug-805 not kept with live run: $o"
[ -e "$T/st/auto/disp-806" ] && ok "O: a refused state read keeps the marker on that pass" || ko "O: disp-806 dropped on a refused read"
touch "$T/806ok"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
[ ! -e "$T/st/auto/disp-806" ] && ok "O: the next pass releases an issue a refused read skipped (#534)" || ko "O: disp-806 held for good after one refused read: $o"
# H (#3107): the daily pass clears every SG_* and runs from a cwd outside the repository. The suite resolves the squad
# root from the copy's own tree, never from the cwd. A failed run whose only pass is the router's is still demoted.
mkdir -p "$T/co/runs/run-outside"; echo run-outside >> "$T/st/active-runs.txt"
printf 'CHILD — GitHub issue(s) 808 (bug; one PR):\n' > "$T/co/runs/run-outside/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #808 on fix/808-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": ["provisional: dispatched the child"], "pos": 3}\n{"event": "verdict", "verdict": "failed", "reasons": ["no_landed_change"], "exit_code": 1}\n' > "$T/co/runs/run-outside/record.jsonl"
echo run-outside > "$T/st/auto/disp-808"
touch -t 202001010000 "$T/co/runs/run-outside/record.jsonl"
o=$(cd "$T" && env -u SG_SQUAD SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 808$' "$T/board.calls" 2>/dev/null && ok "O: a run from a cwd outside the repository resolves the squad root from the copy's own tree (#3107)" || ko "O #808 not demoted from outside the repository: $o"
rm -rf "$T"; exit $fail
