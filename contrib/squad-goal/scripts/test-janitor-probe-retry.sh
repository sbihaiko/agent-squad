#!/bin/bash
# seconds: 4.19
# A refuted probe's refutation is retried by the loop itself (#888). The janitor's rule O is the only caller of `wave.sh fail`,
# and it reads a run once (auto/counted-<run>). The real janitor drives the real wave.sh here; only gh, the board and
# `squad premise-check` are stand-ins:
#  (A) premise-check fails for a transient cause (exit 75: a gh or I/O error): the run is not marked counted, so the next
#      janitor pass sends the refutation again; once it is recorded, the run is read no more and a third pass sends nothing.
#  (B) premise-check refuses the refutation (any other exit: the premise was never recorded as needs a probe): the refusal is
#      named to the owner once and never retried, since the same refusal cannot change (ADR-0134).
#  (C) a probe whose child entered its wave is requeued by the wave entry (`wave.sh requeue`), and rule O skips the run: a
#      transient failure there is retried by the janitor's probe sweep on its next pass; once recorded, never again.
#  (D) the same, where the retry is refused for good: the refusal is named to the owner once and never retried.
# usage: bash test-janitor-probe-retry.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs"
cp "$d"/*.py "$d"/*.sh "$d"/*.json "$T/s/" 2>/dev/null
printf 'export SG_STATE=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_CHECKOUT=%s BOARD=%s/s/board.sh SG_PREMISE_CHECK=%s/bin/pc\n. %s/address.sh\n' \
  "$T/st" "$T/co" "$T" "$T" "$T/s" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
# The premise-check stand-in logs one line per call and exits with the next code of its issue's script (pc-<spec>), the last one kept.
cat > "$T/bin/pc" <<J
#!/bin/bash
echo "\$*" >> "$T/pc.log"; f="$T/pc-\$1"; rc=\$(head -1 "\$f"); [ "\$(wc -l < "\$f")" -gt 1 ] && sed -i '' 1d "\$f"
[ "\$rc" = 0 ] && echo "updated: refuted" || echo "premise-check: rc \$rc" >&2; exit "\$rc"
J
chmod +x "$T/bin/pc"
# gh: the janitor's REST reads and wave.sh's GraphQL issue read (ghread.py); every probe issue is open and labelled probe.
issue() { python3 -c 'import json,sys
n,body=int(sys.argv[1]),sys.argv[2]
node={"number":n,"title":"t","body":body,"state":"OPEN","labels":{"nodes":[{"name":"probe"}]}}
print(json.dumps({"data":{"repository":{f"i{n}":node}}}))' "$@" > "$T/issue-$1.json"; }
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\$*"; echo "\$a" >> "$T/gh.log"
n=\$(printf '%s' "\$a" | sed -n 's/.*issue(number: \([0-9]*\)).*/\1/p')
if [ -n "\$n" ] && [ -f "$T/issue-\$n.json" ]; then cat "$T/issue-\$n.json"; exit 0; fi
case "\$a" in *"issues(states: OPEN"*) cat "$T/open.json"; exit 0;; esac
case "\$a" in
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) for n in 60 61; do printf '%s\tIn Progress\n' "\$n"; done;;
  *repos/x/y/branches*) ;;
  *"repos/x/y/issues/6"[01]" "*"-q"*) printf 'open\t\tt\tprobe\n';;
  *) printf '{}';;
esac
J
# The open-issue list (the janitor's one read a pass): every probe is open, labelled probe and frozen for the owner.
python3 -c 'import json; print(json.dumps({"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":False},"nodes":[{"number":n,"title":"t","body":"","state":"OPEN","labels":{"nodes":[{"name":"probe"},{"name":"frozen:owner"}]}} for n in (60,61,62,63)]}}}}))' > "$T/open.json"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() {   # <run> <issue> <spec>: a probe run whose acceptance command was unmet, ended long ago
  mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '{"event": "run_start"}\n{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #%s"}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed"], "exit_code": 1}\n' "$2" > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1/record.jsonl"
  issue "$2" $'## Parent\n\n#'"$3"$'\n\nPremise: the premise of '"$2"$'\n\n## What to build\n\nx'
}
mk run-a 60 870; printf '75\n0\n' > "$T/pc-870"   # A: transient once, then recorded
mk run-b 61 871; printf '2\n' > "$T/pc-871"       # B: refused for good
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_TODO=Todo SG_BASE=main SG_HUB_DIR="$T/st" \
  SG_PREMISE_CHECK="$T/bin/pc" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" >> "$T/janitor.out" 2>&1; }
calls() { /usr/bin/grep -c "^$1 " "$T/pc.log" 2>/dev/null; }
# A run is read no more once its line left active-runs and no retry marker names its issue: a finished run leaves the list on
# its first pass (FINISHED-RUN, 7fba525ed, #2138), and its read-once marker auto/counted-<run> goes with the line (#693).
settled() { ! /usr/bin/grep -qx "$1" "$T/st/active-runs.txt" 2>/dev/null && [ ! -e "$T/st/auto/probe-retry-$2" ]; }

pass
[ "$(calls 870)" = 1 ] && [ ! -e "$T/st/auto/counted-run-a" ] \
  && ok "A: a refutation that failed for a transient cause leaves its run uncounted" || ko "A1: calls $(calls 870), counted $(ls "$T/st/auto" | tr '\n' ' '): $(tail -20 "$T/janitor.out")"
/usr/bin/grep -q 'PROBE-RETRY #60' "$T/st/cycle.log" "$T/janitor.out" 2>/dev/null && ok "A: the retry is named in the pass log" || ko "A: no retry line: $(cat "$T/st/cycle.log" 2>/dev/null)"
pass
[ "$(calls 870)" = 2 ] && settled run-a 60 \
  && ok "A: the next janitor pass sends the refutation again, and then the run is read no more" || ko "A2: calls $(calls 870), runs $(tr '\n' ' ' < "$T/st/active-runs.txt"), auto $(ls "$T/st/auto" | tr '\n' ' '): $(tail -20 "$T/janitor.out")"
pass
[ "$(calls 870)" = 2 ] && ok "A: a recorded refutation is not sent a third time" || ko "A3: calls $(calls 870)"

[ "$(calls 871)" = 1 ] && settled run-b 61 \
  && ok "B: a refused refutation is sent once and never retried" || ko "B: calls $(calls 871), runs $(tr '\n' ' ' < "$T/st/active-runs.txt"), auto $(ls "$T/st/auto" | tr '\n' ' ')"
/usr/bin/grep -q 'PROBE-REFUSED #61' "$T/st/cycle.log" "$T/janitor.out" 2>/dev/null && ls "$T/st/cq/" 2>/dev/null | /usr/bin/grep -q '#61-' \
  && ok "B: the refusal is named to the owner on the issue" || ko "B: refusal not named: $(cat "$T/st/cycle.log" 2>/dev/null) cq: $(ls "$T/st/cq")"
# C and D: the wave entry's requeue (rule O never reads a child that entered its wave) meets a transient failure.
mk2() {   # <run> <issue> <spec>: a probe run whose acceptance command was unmet; it is no run active-runs.txt lists
  mkdir -p "$T/co/runs/$1"; printf '{"event": "run_start"}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed"], "exit_code": 1}\n' > "$T/co/runs/$1/record.jsonl"
  issue "$2" $'## Parent\n\n#'"$3"$'\n\nPremise: the premise of '"$2"$'\n\n## What to build\n\nx'
}
mk2 run-c 62 872; printf '75\n0\n' > "$T/pc-872"   # C: transient in the wave entry, then recorded
mk2 run-d 63 873; printf '75\n2\n' > "$T/pc-873"   # D: transient in the wave entry, then refused for good
for x in "62 run-c" "63 run-d"; do set -- $x
  (cd "$T/s" && SG_REQUEUE_RUN=$2 SG_PREMISE_CHECK="$T/bin/pc" bash "$T/s/wave.sh" requeue "$1" "the run's final gate failed: run $2 failed ac-verify") >> "$T/requeue.out" 2>&1
done
/usr/bin/grep -q 'PROBE-RETRY #62' "$T/requeue.out" && [ "$(calls 872)" = 1 ] && ok "C: the wave entry's transient failure is named once" || ko "C0: calls $(calls 872): $(cat "$T/requeue.out")"
touch -t 202001010000 "$T/st/auto/"probe-retry-* 2>/dev/null
pass
[ "$(calls 872)" = 2 ] && ok "C: the next janitor pass sends the wave entry's refutation again" || ko "C1: calls $(calls 872): $(tail -20 "$T/janitor.out")"
[ "$(calls 873)" = 2 ] && /usr/bin/grep -q 'PROBE-REFUSED #63' "$T/st/cycle.log" "$T/janitor.out" 2>/dev/null && ls "$T/st/cq/" 2>/dev/null | /usr/bin/grep -q '#63-' \
  && ok "D: a retry that is refused is named to the owner on the issue" || ko "D1: calls $(calls 873), cq $(ls "$T/st/cq" | tr '\n' ' '): $(tail -20 "$T/janitor.out")"
pass
[ "$(calls 872)" = 2 ] && [ "$(calls 873)" = 2 ] && ok "C/D: a recorded or refused refutation is not sent again" || ko "C2/D2: calls $(calls 872) $(calls 873)"
rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
