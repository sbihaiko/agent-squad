#!/bin/bash
# promote-main.sh <sha> reads the complete tier before it pushes (#2039, ADR-0173). The sha form is the stg-to-main pass's
# call, and the script used to trust its caller: a sha with no complete-tier run moved main. Now the script reads the sha's
# complete-tier check runs through the same reader the pass uses (mainproof.sh), refuses a sha whose tier is not green, and
# names the missing, red or unproven job. SG_PROMOTE_UNPROVEN=owner is the only override, and the script says it used it.
# Offline: a bare local remote, a stub gh that answers the check runs, and a workflow fixture that names two complete-tier
# jobs. usage: bash test-promote-main-proof.sh
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done   # hermetic: the live loop exports its own SG_* (#1410)
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git; ST=$T/st
export SG_STATE=$ST SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0
mkdir -p "$ST" "$T/bin"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
base=$(tip main); adv stg sha-a; sa=$(tip stg); adv stg sha-b; sb=$(tip stg)
# The workflow fixture: one fast job and two complete-tier jobs, the tier the `promote` label starts.
printf "jobs:\n  ruff:\n    name: ruff\n  complete-guard:\n    name: complete-guard\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n  full-xdist:\n    name: full-xdist\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n  gui-e2e:\n    name: gui-e2e\n    if: github.head_ref == 'stg' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n" > "$T/ci.yml"
printf "jobs:\n  ruff:\n    name: ruff\n" > "$T/ci-fast-only.yml"
export SG_CI_WORKFLOW=$T/ci.yml
# checks <sha> <full-xdist> <gui-e2e>: the check runs of one sha. A word is a conclusion, or `none` for no run at all.
checks() { local sha=$1 out="" n c i=0; shift
  for n in full-xdist gui-e2e; do c=$1; shift; i=$((i + 1))
    case $c in none) ;;
      *) out="$out{\"id\":$i,\"name\":\"$n\",\"status\":\"completed\",\"conclusion\":\"$c\",\"started_at\":\"2026-11-17T00:00:00Z\"},";; esac
  done
  printf '{"check_runs":[%s]}\n' "${out%,}" | sed 's/,]/]/' > "$T/checks-$sha.json"; }
# The stub GitHub: it logs every call and answers a sha's check runs from $T/checks-<sha>.json.
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
a="\$*"
case "\$a" in
  *"/check-runs"*) s=\${a##*commits/}; s=\${s%%/*}; cat "$T/checks-\$s.json" 2>/dev/null || echo '{"check_runs":[]}';;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
main_move() { bash "$d/promote-main.sh" "$1" > "$T/out" 2>&1; echo $?; }   # <sha>: the exit code, and the output in $T/out

# 1) A sha with no complete-tier run at all never moves main: the script names both missing jobs.
rm -f "$T/checks-$sa.json"; git -C "$R" update-ref refs/heads/main "$base"; rc=$(main_move "$sa")
[ "$rc" = 1 ] && [ "$(tip main)" = "$base" ] && ok "1 a sha with no complete-tier run moves nothing" || ko "1 missing: rc $rc, main $(tip main), out '$(cat "$T/out")'"
/usr/bin/grep -q "refused" "$T/out" && /usr/bin/grep -q "full-xdist" "$T/out" && /usr/bin/grep -q "gui-e2e" "$T/out" \
  && ok "1 the refusal names each missing job" || ko "1 missing job unnamed: '$(cat "$T/out")'"

# 2) A red complete-tier job holds main, and the refusal names it with its conclusion.
git -C "$R" update-ref refs/heads/main "$base"; checks "$sa" failure success; rc=$(main_move "$sa")
[ "$rc" = 1 ] && [ "$(tip main)" = "$base" ] && ok "2 a red complete-tier job moves nothing" || ko "2 red: rc $rc, main $(tip main), out '$(cat "$T/out")'"
/usr/bin/grep -q "full-xdist" "$T/out" && /usr/bin/grep -q "failure" "$T/out" && ! /usr/bin/grep -q "gui-e2e (" "$T/out" \
  && ok "2 the refusal names the red job and its conclusion, and not the green one" || ko "2 red job: '$(cat "$T/out")'"

# 3) A run that measured nothing is unproven, never green: main stays.
git -C "$R" update-ref refs/heads/main "$base"; checks "$sa" success cancelled; rc=$(main_move "$sa")
[ "$rc" = 1 ] && [ "$(tip main)" = "$base" ] && /usr/bin/grep -q "gui-e2e" "$T/out" && /usr/bin/grep -q "cancelled" "$T/out" \
  && ok "3 a cancelled job is unproven and moves nothing, and the refusal names it" || ko "3 cancelled: rc $rc, main $(tip main), out '$(cat "$T/out")'"

# 4) A green complete tier on the sha moves main to that sha, and the shape of the answer is unchanged.
git -C "$R" update-ref refs/heads/main "$base"; checks "$sa" success success; rc=$(main_move "$sa")
[ "$rc" = 0 ] && [ "$(tip main)" = "$sa" ] && [ "$(tip stg)" = "$sb" ] && ok "4 a green complete tier moves main to the sha, not to the stg tip" \
  || ko "4 green: rc $rc, main $(tip main), want $sa, out '$(cat "$T/out")'"

# 5) SG_PROMOTE_UNPROVEN=owner is the only override, and the script prints that it used it.
git -C "$R" update-ref refs/heads/main "$base"; checks "$sa" failure success
rc=$(SG_PROMOTE_UNPROVEN=owner main_move "$sa")
[ "$rc" = 0 ] && [ "$(tip main)" = "$sa" ] && ok "5 the owner override skips the proof and moves main" || ko "5 override: rc $rc, main $(tip main), out '$(cat "$T/out")'"
/usr/bin/grep -q "SG_PROMOTE_UNPROVEN=owner" "$T/out" && ok "5 the script prints that it used the override" || ko "5 override unnamed: '$(cat "$T/out")'"
# Any other value is no override: the proof still holds main.
git -C "$R" update-ref refs/heads/main "$base"; rc=$(SG_PROMOTE_UNPROVEN=yes main_move "$sa")
[ "$rc" = 1 ] && [ "$(tip main)" = "$base" ] && ok "5 a value other than owner is no override" || ko "5 other value: rc $rc, main $(tip main), out '$(cat "$T/out")'"

# 6) A workflow that names no complete-tier job proves nothing: the sha never moves main.
git -C "$R" update-ref refs/heads/main "$base"; checks "$sa" success success; rc=$(SG_CI_WORKFLOW=$T/ci-fast-only.yml main_move "$sa")
[ "$rc" = 1 ] && [ "$(tip main)" = "$base" ] && /usr/bin/grep -q "no complete tier job" "$T/out" \
  && ok "6 a workflow with no complete-tier job moves nothing, and the refusal names the cause" || ko "6 no names: rc $rc, main $(tip main), out '$(cat "$T/out")'"

# 7) The reader is the pass's own: the newest completed, non-skipped run of a name answers, so a green run behind a newer
# open run still proves the sha.
git -C "$R" update-ref refs/heads/main "$base"
printf '{"check_runs":[{"id":1,"name":"full-xdist","status":"completed","conclusion":"success","started_at":"2026-11-17T00:00:00Z"},{"id":2,"name":"full-xdist","status":"in_progress","conclusion":null,"started_at":"2026-11-17T01:00:00Z"},{"id":1,"name":"gui-e2e","status":"completed","conclusion":"success","started_at":"2026-11-17T00:00:00Z"}]}\n' > "$T/checks-$sa.json"
rc=$(main_move "$sa")
[ "$rc" = 0 ] && [ "$(tip main)" = "$sa" ] && ok "7 a completed green run behind a newer open run still proves the sha" \
  || ko "7 rerun: rc $rc, main $(tip main), out '$(cat "$T/out")'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
