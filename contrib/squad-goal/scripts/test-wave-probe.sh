#!/bin/bash
# A refuted probe sends its spec back to the check (#888). When a ticket labelled `probe` ends with its acceptance command
# unmet (its run's verdict failed on ac-verify), the loop's close path -- `wave.sh fail` from the janitor and `wave.sh
# requeue` from the wave entry -- calls `squad premise-check <spec> --refute <premise>`, where the spec is the ticket's
# `## Parent` and the premise is its `Premise:` line. The probe stays open, frozen to the owner, and is not requeued, so
# its dependents wait through the blocker guard. A confirming probe, or any other ticket, takes the close path as before.
# usage: bash test-wave-probe.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
exec 3>&2 2>>"$T.err"
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/address.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh SG_PREMISE_CHECK=${SG_PREMISE_CHECK-%s/pc}\n. %s/address.sh\n' \
  "$T/st" "$T/co" "$T" "$T/bin" "$T/s" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
# The premise-check stub writes one line per call, its argv joined by '|', and exits PC_RC.
printf '#!/bin/bash\n(IFS="|"; echo "$*") >> "%s/pc.log"\nexit "${PC_RC:-0}"\n' "$T" > "$T/bin/pc"; chmod +x "$T/bin/pc"
# issue <n> <labels csv> <body>: the issue's GraphQL answer, as ghread.py reads one issue.
issue() { python3 -c 'import json,sys
n,labels,body=int(sys.argv[1]),[l for l in sys.argv[2].split(",") if l],sys.argv[3]
node={"number":n,"title":"t","body":body,"state":"OPEN","labels":{"nodes":[{"name":l} for l in labels]}}
print(json.dumps({"data":{"repository":{f"i{n}":node}}}))' "$@" > "$T/issue-$1.json"; }
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/gh.log"\nn=$(printf "%%s" "$*" | sed -n "s/.*issue(number: \\([0-9]*\\)).*/\\1/p")\nif [ -n "$n" ] && [ -f "%s/issue-$n.json" ]; then cat "%s/issue-$n.json"; else printf "{}"; fi\n' "$T" "$T" "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
# verdict <run> <verdict> [reason...]: the run's record, as the run writes it.
verdict() { mkdir -p "$T/co/runs/$1"; python3 -c 'import json,sys
print(json.dumps({"event":"run_start"})); print(json.dumps({"event":"verdict","verdict":sys.argv[1],"reasons":sys.argv[2:] or [""]}))' "${@:2}" > "$T/co/runs/$1/record.jsonl"; }
w() { PATH="$T/bin:$PATH" bash "$T/s/wave.sh" "$@" 2>&1; }
cnt() { SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" count "x/y#$1" 2>/dev/null; }
froze() { /usr/bin/grep -Fqx -- "api -X POST repos/x/y/issues/$1/labels -f labels[]=frozen:owner" "$T/gh.log"; }
pcs() { cat "$T/pc.log" 2>/dev/null | /usr/bin/grep -c ""; }
PROBE=$'## Parent\n\n#878\n\nPremise: the check comments on the spec\n\n## What to build\n\nx'
verdict run-red failed ac-verify:ac_failed; verdict run-ok failed review_held; verdict run-done completed

# A: the janitor's fail on a probe whose acceptance command was unmet refutes its premise, freezes it, and counts nothing.
issue 50 probe,wave:01 "$PROBE"
o=$(w fail 50 run-red "ended failed: ac-verify:ac_failed")
[ "$(cat "$T/pc.log" 2>/dev/null)" = "878|--refute|the check comments on the spec|--repo|x/y" ] \
  && ok "A: an unmet probe calls premise-check <spec> --refute <premise>" || ko "A: premise-check argv '$(cat "$T/pc.log" 2>/dev/null)'"
froze 50 && ok "A: the refuting probe is frozen to the owner" || ko "A: no freeze: '$(tr '\n' ' ' < "$T/gh.log")'"
! /usr/bin/grep -q 'issues/50 .*state' "$T/gh.log" && ! /usr/bin/grep -q 'Todo 50' "$T/board.log" 2>/dev/null \
  && ok "A: the probe stays open and keeps its column" || ko "A: closed or moved: '$(cat "$T/board.log" 2>/dev/null)'"
[ "$(cnt 50)" = 0 ] && ok "A: a refutation is no failed dispatch" || ko "A: counted '$(cnt 50)'"
case "$o" in *"WAVE PROBE #50"*"refuted"*"#878"*) ok "A: the refutation is logged on one line";; *) ko "A: no probe line: '$o'";; esac

# B: the same probe a second time (a later pass) retries the refutation, which premise-check answers once and then as a
# no-op, so a refutation that failed once is never lost; the probe is not frozen twice.
issue 50 probe,wave:01,frozen:owner "$PROBE"; : > "$T/gh.log"
o=$(w fail 50 run-red "ended failed: ac-verify:ac_failed")
[ "$(pcs)" = 2 ] && ! froze 50 && case "$o" in *"WAVE PROBE #50"*"refuted"*) true;; *) false;; esac \
  && ok "B: a frozen probe retries its refutation and is not frozen again" || ko "B: premise-check ran $(pcs) times: '$o'"

# C: a probe whose run failed for another reason, or a ticket with no probe label, takes the close path as before.
: > "$T/pc.log"; issue 51 probe "$PROBE"; issue 52 "" "$PROBE"
o=$(w fail 51 run-ok "ended failed: review_held"); o2=$(w fail 52 run-red "ended failed: ac-verify:ac_failed")
[ "$(pcs)" = 0 ] && [ "$(cnt 51)" = 1 ] && [ "$(cnt 52)" = 1 ] \
  && ok "C: a probe that met its gate, or a non-probe ticket, refutes nothing and counts as before" || ko "C: pc $(pcs), counts $(cnt 51) $(cnt 52): '$o' '$o2'"

# D: the wave entry's requeue of a refuting probe refutes it too, and does not put it back in Todo.
: > "$T/pc.log"; : > "$T/board.log"; issue 53 probe "$PROBE"; echo req > "$T/st/auto/disp-53"
o=$(SG_REQUEUE_RUN=run-red w requeue 53 "the run's final gate failed: run run-red failed ac-verify")
[ "$(pcs)" = 1 ] && froze 53 && [ -e "$T/st/auto/disp-53" ] && ! /usr/bin/grep -q 'Todo 53' "$T/board.log" \
  && ok "D: a requeued refuting probe is refuted and held, never requeued" || ko "D: pc $(pcs), board '$(cat "$T/board.log")': '$o'"
# A requeue with no run behind it, or of a run that completed, is unchanged.
: > "$T/pc.log"; echo req > "$T/st/auto/disp-53"; o=$(SG_REQUEUE_RUN=run-done w requeue 53 "the review asked for changes: x")
[ "$(pcs)" = 0 ] && /usr/bin/grep -q 'Todo 53' "$T/board.log" && ok "D: a confirming probe's requeue is unchanged" || ko "D2: pc $(pcs): '$o'"

# E: a probe that names no premise cannot be refuted: it is frozen for the owner, and the line says why.
: > "$T/pc.log"; issue 54 probe $'## Parent\n\n#878\n\nno premise line'
o=$(w fail 54 run-red "ended failed: ac-verify:ac_failed")
[ "$(pcs)" = 0 ] && froze 54 && case "$o" in *"WAVE PROBE #54"*"no Premise"*) true;; *) false;; esac \
  && ok "E: a probe with no premise line is frozen and named" || ko "E: pc $(pcs): '$o'"

# F: a refutation premise-check refuses (exit 2: the premise was never recorded as needs a probe) still freezes the probe; the
# refusal is named to the owner on the issue, and the line carries no retry token, so the janitor counts the run and never
# sends the same refusal again (ADR-0134).
: > "$T/pc.log"; issue 55 probe "$PROBE"
o=$(PC_RC=2 w fail 55 run-red "ended failed: ac-verify:ac_failed")
[ "$(pcs)" = 1 ] && froze 55 && case "$o" in *"WAVE PROBE-REFUSED #55"*) true;; *) false;; esac && case "$o" in *PROBE-RETRY*) false;; *) true;; esac \
  && ls "$T/st/cq/" | /usr/bin/grep -q '#55-' \
  && ok "F: a refused refutation freezes the probe, is named to the owner, and asks no retry" || ko "F: pc $(pcs), cq '$(ls "$T/st/cq")': '$o'"

# G: a refutation that fails for a transient cause (exit 75: gh or I/O) freezes the probe and prints the retry token the
# janitor reads to leave the run uncounted, so its next pass sends the refutation again (test-janitor-probe-retry.sh).
: > "$T/pc.log"; issue 57 probe "$PROBE"
o=$(PC_RC=75 w fail 57 run-red "ended failed: ac-verify:ac_failed")
[ "$(pcs)" = 1 ] && froze 57 && case "$o" in *"WAVE PROBE-RETRY #57"*) true;; *) false;; esac \
  && ok "G: a transient failure asks the next pass to retry the refutation" || ko "G: pc $(pcs): '$o'"

# H: premise-check runs on the plugin's interpreter (SG_SQUAD's venv), never the host's gate interpreter (SG_GATE_PY).
mkdir -p "$T/sq/.venv/bin" "$T/host"
printf '#!/bin/bash\necho "squad $*" >> "%s/py.log"\n' "$T" > "$T/sq/.venv/bin/python"; chmod +x "$T/sq/.venv/bin/python"
printf '#!/bin/bash\necho "host $*" >> "%s/py.log"\n' "$T" > "$T/host/python"; chmod +x "$T/host/python"
issue 56 probe "$PROBE"
o=$(SG_PREMISE_CHECK= SG_SQUAD="$T/sq" SG_GATE_PY="$T/host/python" w fail 56 run-red "ended failed: ac-verify:ac_failed")
[ "$(cat "$T/py.log" 2>/dev/null)" = "squad -m squad.cli premise-check 878 --refute the check comments on the spec --repo x/y" ] \
  && ok "H: premise-check runs on the plugin's interpreter" || ko "H: interpreter calls '$(cat "$T/py.log" 2>/dev/null)': '$o'"

# I: only an unmet acceptance command refutes. A prose command (malformed), a child tree the gate could not find
# (child_tree_missing, the loop's false red of #862), or a mix of either with ac_failed is no answer from the probe: premise-check
# is not called, and the run takes the normal fail path (it is counted).
verdict run-mal failed "ac-verify:malformed:check that the spec reads well"
verdict run-miss failed "ac-verify:child_tree_missing:git log -1 @wt-58"
verdict run-mix failed "ac-verify:ac_failed:true" "ac-verify:malformed:prose here"
for r in mal:58 miss:59 mix:60; do
  : > "$T/pc.log"; n=${r#*:}; issue "$n" probe "$PROBE"
  o=$(w fail "$n" "run-${r%%:*}" "ended failed: ac-verify")
  [ "$(pcs)" = 0 ] && [ "$(cnt "$n")" = 1 ] && ! froze "$n" \
    && ok "I: a ${r%%:*} ac-verify finding refutes nothing and takes the fail path" || ko "I: ${r%%:*}: pc $(pcs), count $(cnt "$n"): '$o'"
done

exec 2>&3; if /usr/bin/grep -q "command not found" "$T.err"; then ko "stderr: $(cat "$T.err")"; fi; rm -f "$T.err"
rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
