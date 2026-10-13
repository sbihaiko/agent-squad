#!/bin/bash
# Rule O, marker half (#1847): a dispatch marker expires when its run ended without landing -- and `failed` is not the
# only way a run ends. A run that recorded any other verdict (`needs-human`, `pass`) and landed nothing left its
# auto/disp-<n> behind, autofrontier.sh skips any issue holding that marker, and the frontier starved on a finished run.
#  (A) ended-nothing (verdict `needs-human`, no land event, no PR): marker dropped, card back to Todo, announced once;
#  (B) a run that landed (a `land` event with outcome pass) keeps its marker and reconciles its card to Done;
#  (C) a live run (no verdict yet) keeps the marker of the issue it works and writes Run fields;
#  (D) an open PR naming the issue keeps the marker and moves the card to Code Complete.
# Stubbed gh/board; a temp state dir, so the live watch.log, markers and board are never read or written.
# usage: bash test-janitor-disp-expiry.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  # the live API answers a POST to /search/issues with Not Found and a non-zero body, which a caller reading `.total_count` sees as "a PR exists" (#1981): the stub refuses it the same way
  *search/issues*) case "$*" in *"-X GET"*|*"--method GET"*) case "$*" in *"is:merged"*) echo 0;; *"Refs #904"*|*"Refs #908"*) echo 1;; *) echo 0;; esac;; *) printf '{\n  "message": "Not Found"\n}\n'; exit 1;; esac;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 901 902 903 904; do printf '%s\tIn Progress\n' "$n"; done;;   # one read, every card
  *projectsV2/1/items*) ;;
  *issues/90*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"; }
# A: the run recorded a verdict (needs-human) and landed nothing
mk run-ended 901
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #901 on fix/901-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": [], "pos": 3}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["owner_decision"], "exit_code": 0}\n' > "$T/co/runs/run-ended/record.jsonl"
echo run-ended > "$T/st/auto/disp-901"
# B: the run landed a change before it ended
mk run-landed 902
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #902 on fix/902-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "a7df8f1ecff77bdb5833721121a7a50be4066053"}}\n{"event": "verdict", "verdict": "pass", "reasons": [], "exit_code": 0}\n' > "$T/co/runs/run-landed/record.jsonl"
echo run-landed > "$T/st/auto/disp-902"
# C: a live run (no verdict yet) works its issue
mk run-live 903
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #903 on fix/903-x"}\n' > "$T/co/runs/run-live/record.jsonl"
echo run-live > "$T/st/auto/disp-903"
# D: the run ended without landing, but an open PR names the issue
mk run-pr 904
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #904 on fix/904-x"}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["label_missing"], "exit_code": 0}\n' > "$T/co/runs/run-pr/record.jsonl"
echo run-pr > "$T/st/auto/disp-904"
touch -t 202001010000 "$T/co/runs/run-ended/record.jsonl" "$T/co/runs/run-landed/record.jsonl" "$T/co/runs/run-pr/record.jsonl"
# E (#1981): the marker names the REQUEST that dispatched the issue (frontier-<id>), and rule E drops it when that
# request's own log says `verdict failed`, the issue is open and no PR names it. The PR check is what broke: a bare
# `gh api search/issues -f ...` POSTs, the API answers Not Found, and a non-zero body read as "a PR exists" forever --
# so the marker outlived every failed dispatch and autofrontier.sh skipped the issue for good.
printf 'verdict failed (exit 1)\nreasons: blocked_by_11\n' > "$T/st/frontier-old.log"
printf 'verdict pass (exit 0)\n' > "$T/st/frontier-ok.log"
echo frontier-old > "$T/st/auto/disp-906"; touch -t 202001010000 "$T/st/auto/disp-906" "$T/st/frontier-old.log"
echo frontier-ok > "$T/st/auto/disp-907"; touch -t 202001010000 "$T/st/auto/disp-907" "$T/st/frontier-ok.log"
echo frontier-old > "$T/st/auto/disp-908"; touch -t 202001010000 "$T/st/auto/disp-908"
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR= SG_FORK_BASE= SG_TESTING=Testing JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }
o=$(run)
[ ! -e "$T/st/auto/disp-901" ] && ok "O: an ended run with no landed change drops its disp marker" || ko "O disp-901 kept (the frontier starves on it): $o"
/usr/bin/grep -q '^o 4 Todo 901$' "$T/board.calls" 2>/dev/null && ok "O: ended run -> card back to Todo" || ko "O #901 not demoted: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q 'DISPATCH-FAILED #901' "$T/st/cycle.log" 2>/dev/null && ok "O: the drop is announced" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'needs-human' "$T/st/cycle.log" 2>/dev/null && ok "O: the announcement names the verdict it ended on" || ko "O announcement: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'owner_decision' "$T/st/cq/"*901* >/dev/null 2>&1 && /usr/bin/grep -q 'run-ended' "$T/st/cq/"*901* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
[ -e "$T/st/auto/disp-902" ] && /usr/bin/grep -q '^o 4 Done 902$' "$T/board.calls" 2>/dev/null && ok "O: a run that landed moves to Done and keeps its marker" || ko "O #902 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
[ -e "$T/st/auto/disp-903" ] && /usr/bin/grep -q -- '^--run-fields o 4 903 run-live ' "$T/board.calls" 2>/dev/null && ok "O: a live run keeps the marker and writes Run fields" || ko "O #903 not live-marked: $(cat "$T/board.calls" 2>/dev/null)"
[ -e "$T/st/auto/disp-904" ] && /usr/bin/grep -q '^o 4 Code Complete 904$' "$T/board.calls" 2>/dev/null && ok "O: an open PR naming the issue moves to Code Complete and keeps its marker" || ko "O #904 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/disp-906" ] && ok "E: a dispatch whose own request ended failed is dropped once it is old" || ko "E disp-906 kept: the frontier stays starved"
/usr/bin/grep -q 'stale disp-906' "$T/st/cycle.log" 2>/dev/null && ok "E: the drop is announced with the request that failed" || ko "E announcement: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ -e "$T/st/auto/disp-907" ] && ok "E: a request that ended any other way keeps its marker" || ko "E disp-907 dropped by mistake"
[ -e "$T/st/auto/disp-908" ] && ok "E: an open PR naming the issue keeps its marker" || ko "E disp-908 dropped although a PR names it"
run >/dev/null   # the announcement is once per run, not once per pass
[ "$(/usr/bin/grep -c 'DISPATCH-FAILED #901' "$T/st/cycle.log" 2>/dev/null)" = 1 ] && [ "$(/usr/bin/grep -c '^o 4 Todo 901$' "$T/board.calls" 2>/dev/null)" = 1 ] && ok "O: the drop is announced once" || ko "O re-announced: $(cat "$T/st/cycle.log" 2>/dev/null)"
rm -rf "$T"; exit $fail
