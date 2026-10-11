#!/bin/bash
# Regression test for the autopilot's gate record (#1766): every pass writes auto/gate-<pr> carrying the head sha, the
# epoch and the one-line reason the loop did not merge the PR, and auto/.pass carrying the epoch of the pass itself.
# A later pass overwrites the record, so the file never accumulates reasons and always names the latest one.
# Stub gh, no network. usage: bash test-autopilot-gate.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; T=$(mktemp -d); T0=$(date +%s); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/cq" "$T/bin" "$T/fix"; cp "$src" "$T/s/autopilot.sh"
created=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 BOARD=true AP_MIN_ISSUE=1090 AP_MAX_ROUNDS=2\n' "$T/st" > "$T/s/env.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
F=$GHFIX
case "$*" in
  *'head.sha+" "+.mergeable_state'*) printf '%s %s\n' "$(cat "$F/headsha")" "$(cat "$F/mstate" 2>/dev/null || echo clean)";;
  *"pulls?state=open"*) cat "$F/list.json";;
  *"pulls?state=closed"*) : ;;
  *"/check-runs"*) c=$(cat "$F/checkruns" 2>/dev/null); echo "${c-success}";;
  *"pulls/"*"-q .mergeable_state"*) cat "$F/mstate" 2>/dev/null || echo clean;;
  *"pulls/"*"-q .head.ref"*) cat "$F/ref" 2>/dev/null || echo gui/1100;;
  *"pulls/"*"-q .changed_files"*) cat "$F/changed" 2>/dev/null || echo 1;;
  *"pulls/"*"-q .body"*) cat "$F/body" 2>/dev/null;;
  *"pulls/"*"-q .state"*) echo closed;;
  *"issues/"*"/comments"*) echo '[]';;
  *) : ;;
esac
exit 0
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" GHFIX="$T/fix" PF="$T/fix/prepush"
# prepush.sh is the pass's process gate; it passes unless the case asks for a problem
cat > "$T/s/prepush.sh" <<'P'
#!/bin/bash
[ -n "$PF" ] && [ -f "$PF" ] && { echo "process problem: the body has a closing keyword"; exit 1; }
exit 0
P
A="$T/st/auto"
run() { bash "$T/s/autopilot.sh" 2>/dev/null; }
reset() { rm -rf "$A" "$T/st/cq"; mkdir -p "$A" "$T/st/cq"; rm -f "$T"/fix/* "$T"/st/revci-*.out; }
# pr <n> <sha> <body> <labels json> [base]
pr() { printf '[{"number":%s,"head":{"sha":"%s"},"title":"t","body":"%s","labels":%s,"base":{"ref":"%s"},"draft":false,"created_at":"%s"}]\n' "$1" "$2" "$3" "$4" "${5:-main}" "$created" > "$T/fix/list.json"; printf '%s\n' "$2" > "$T/fix/headsha"; }
line() { printf '#%s %s clean CI=%s VERDICT: %s\n' "$1" "${2:0:9}" "$3" "$4" > "$T/st/revci-$1.out"; }
assert_pass() { local p; p=$(cat "$A/.pass" 2>/dev/null)
  case "$p" in ''|*[!0-9]*) ko "$1: auto/.pass is '${p:-missing}'";; *)
    [ "$p" -ge "$T0" ] && [ "$p" -le "$(date +%s)" ] && ok "$1: auto/.pass carries the pass epoch" || ko "$1: auto/.pass epoch $p outside the pass window";; esac; }
# assert_gate <name> <pr> <sha9> <reason>
assert_gate() { local name=$1 p=$2 s=$3 w=$4 f="$A/gate-$p" e
  [ -f "$f" ] || { ko "$name: no auto/gate-$p for a PR the pass examined"; return; }
  [ "$(wc -l < "$f" | tr -d ' ')" = 1 ] || { ko "$name: auto/gate-$p has $(wc -l < "$f" | tr -d ' ') lines, not one reason"; return; }
  [ "$(awk -F'\t' 'NF==4 && $1 ~ /^[0-9a-f]+$/ && $2 ~ /^[0-9]+$/ && $3 != "" && $4 != ""' "$f" | wc -l | tr -d ' ')" = 1 ] \
    || { ko "$name: auto/gate-$p is not '<sha><TAB><epoch><TAB><reason><TAB><text>': $(cat "$f")"; return; }
  [ "$(awk -F'\t' '{print $1}' "$f")" = "$s" ] || { ko "$name: gate head sha is '$(awk -F'\t' '{print $1}' "$f")', want $s"; return; }
  e=$(awk -F'\t' '{print $2}' "$f"); { [ "$e" -ge "$T0" ] && [ "$e" -le "$(date +%s)" ]; } || { ko "$name: gate epoch $e outside the pass window"; return; }
  [ "$(awk -F'\t' '{print $3}' "$f")" = "$w" ] || { ko "$name: gate reason is '$(awk -F'\t' '{print $3}' "$f")', want '$w'"; return; }
  ok "$name -> $w"; }

# the kill switch examines no PR, but the pass itself is still stamped
reset; touch "$A/off"; run; assert_pass "auto/off pass"
[ -z "$(ls "$A"/gate-* 2>/dev/null)" ] && ok "auto/off examines no PR (no gate file)" || ko "auto/off wrote a gate file: $(ls "$A"/gate-*)"
rm -f "$A/off"

# an issue below the autopilot's minimum
reset; pr 1050 aa0000000001 "Refs #1050" '[]'; run; assert_gate "PR below the minimum issue" 1050 aa0000000 below-min-issue
# no Refs #N and the issue is not derivable from the branch
reset; pr 1051 bb0000000002 "no issue reference" '[]'; echo feature/x > "$T/fix/ref"; run; assert_gate "PR with no Refs #N" 1051 bb0000000 no-refs
# hold / needs-human label
reset; pr 1052 cc0000000003 "Refs #1052" '[{"name":"hold"}]'; run; assert_gate "hold label" 1052 cc0000000 hold-label
# SUPERSEDED and CI_TIMEOUT review lines
reset; pr 1053 dd0000000004 "Refs #1053" '[]'; line 1053 dd0000000004 success:9 SUPERSEDED; run; assert_gate "SUPERSEDED" 1053 dd0000000 superseded
reset; pr 1054 ee0000000005 "Refs #1054" '[]'; line 1054 ee0000000005 success:9 CI_TIMEOUT; run; assert_gate "CI_TIMEOUT" 1054 ee0000000 ci-timeout
# a head with no approval
reset; pr 1055 ff0000000006 "Refs #1055" '[]'; line 1055 ff0000000006 success:9 COMMENT; run; assert_gate "no approval on the head" 1055 ff0000000 no-approval
# APPROVE but GitHub has no check run for the head
reset; pr 1056 110000000007 "Refs #1056" '[]'; line 1056 110000000007 success:9 APPROVE; : > "$T/fix/checkruns"; run; assert_gate "CI missing" 1056 110000000 ci-missing
# APPROVE, clean, but the head's diff against the base is empty
reset; pr 1057 220000000008 "Refs #1057" '[]'; line 1057 220000000008 success:9 APPROVE; echo 0 > "$T/fix/changed"; run; assert_gate "empty diff" 1057 220000000 empty-diff
# a fix round is already queued for this head / the fix switch is off
reset; pr 1058 330000000009 "Refs #1058" '[]'; line 1058 330000000009 failure:9 CHANGES; touch "$A/queued-1058-330000000"; run; assert_gate "round already queued" 1058 330000000 round-queued
reset; pr 1059 44000000000a "Refs #1059" '[]'; line 1059 44000000000a failure:9 CHANGES; touch "$A/nofix"; run; assert_gate "nofix" 1059 440000000 nofix
# the round cap is spent: the panel decides
reset; pr 1060 55000000000b "Refs #1060" '[]'; line 1060 55000000000b failure:9 CHANGES; printf 'x\nx\n' > "$A/rounds-1060"; run; assert_gate "round cap -> panel" 1060 550000000 panel
# APPROVE with unresolved threads only: a soft fix round
reset; pr 1061 66000000000c "Refs #1061" '[]'; line 1061 66000000000c success:9 APPROVE; echo 1 > "$A/threads-1061-660000000"; run; assert_gate "fixing threads" 1061 660000000 fixing-threads
# a head that conflicts with its base: fixed before it is reviewed
reset; pr 1062 77000000000d "Refs #1062" '[]'; echo dirty > "$T/fix/mstate"; run; assert_gate "conflict before review" 1062 770000000 conflict
# the prepush process gate found a problem: a fix round without a model review
reset; pr 1063 88000000000e "Refs #1063" '[]'; touch "$PF"; run; assert_gate "prepush fix" 1063 880000000 prepush
# a review was launched for this head on an earlier pass
reset; pr 1066 ab0000000011 "Refs #1066" '[]'; run; assert_gate "reviewing" 1066 ab0000000 reviewing
# the issue is closed as a duplicate / not planned
reset; pr 1064 99000000000f "Refs #1064" '[]'; touch "$A/dupiss-1064-1064" "$A/isschk-1064"; run; assert_gate "issue closed as duplicate" 1064 990000000 issue-duplicate
# an APPROVE that does not merge: hold() says why and gates too
reset; pr 1065 ac0000000012 "Refs #1065" '[]' gui/9; line 1065 ac0000000012 success:9 APPROVE; run; assert_gate "hold: stacked" 1065 ac0000000 hold-stacked
assert_pass "the pass that held PR #1065"
# a second pass overwrites the record: exactly one reason, the latest
line 1065 ac0000000012 success:9 SUPERSEDED; run
assert_gate "second pass overwrites the record" 1065 ac0000000 superseded
grep -q hold-stacked "$A/gate-1065" && ko "auto/gate-1065 kept the previous reason: $(cat "$A/gate-1065")" || ok "the previous reason is gone"
assert_pass "the second pass"
rm -rf "$T"; exit $fail
