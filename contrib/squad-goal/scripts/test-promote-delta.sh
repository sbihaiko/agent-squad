#!/bin/bash
# The delta promotion proof (ADR-0156, #946): a green full proof is carried across a covered delta, and never otherwise.
# Offline: throwaway repositories, a local bare remote through SG_PROMOTE_REMOTE, a gh stub, and SG_DELTA_SUITE for the gate
# commands. usage: bash test-promote-delta.sh
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so its wait is proved
unset SG_CHILD_SESSION   # the merge door refuses a child session; the merge here is the gh stub's, into a local bare remote
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SG_DELTA_TREE=$T/dtree SG_PROMOTE_PROOF=local; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B dev origin/dev; $W commit -q --allow-empty -m "$1"; $W push -q origin dev; }
# adv_file <path> <msg>: a dev commit that changes one file (the delta's content)
adv_file() { $W fetch -q origin; $W checkout -q -B dev origin/dev; mkdir -p "$T/w/$(dirname "$1")"; echo "$2" >> "$T/w/$1"; $W add "$1"; $W commit -q -m "$2"; $W push -q origin dev; }
# line <kind> <result> <sha>: gate.sh's full result, written the way gate.sh --status writes it
line() { printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$1" "$2" "$3" > "$SG_STATE/gate-status-full"; }
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"-X POST repos/x/emu/pulls"*) echo '{"number":7}';;
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/stg; then echo '{"merged":true,"message":"Pull Request successfully merged"}'; else echo '{"merged":false,"message":"Pull Request is not mergeable"}'; fi;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
export SG_DELTA_SUITE=true   # the gate commands are stubbed: green

# (a) a green full proof and a covered delta promote
adv "p-base"; line full pass "$(tip dev)"; base_full=$(tip dev)
adv_file docs/notes.md "covered delta"
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
rec=$(cat "$SG_STATE/gate-status-delta" 2>/dev/null)
f3=$(printf '%s' "$rec" | cut -f3); f4=$(printf '%s' "$rec" | cut -f4); f5=$(printf '%s' "$rec" | cut -f5)
[ $rc = 0 ] && [ "$f3" = pass ] && [ "$f4" = "$base_full" ] && [ "$f5" = "$(tip dev)" ] && [ "$(printf '%s' "$rec" | awk -F'\t' '{print NF}')" = 6 ] \
  && ok "delta: a covered delta writes a six-field pass record" || ko "delta record: rc $rc, '$rec', out '$out'"
before=$(tip stg)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && [ "$(tip stg)" != "$before" ] \
  && ok "delta: a green full proof carried by a covered delta promotes stg" || ko "delta promote: rc $rc, stg $(tip stg), out '$out'"

# (b) a path outside the map refuses: the answer is the full proof
adv "p2"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta"
adv_file squad/uncovered.py "outside the map"; before=$(tip stg)
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && case "$out" in *uncovered*) ok "delta: a path outside the map refuses and writes no pass";; *) ko "uncovered: rc $rc, out '$out'";; esac || ko "uncovered: rc $rc, record present or no reason, out '$out'"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "delta: an uncovered change promotes nothing without the full proof" || ko "uncovered promote: rc $rc, out '$out'"

# (c) a P that is not an ancestor of the dev tip refuses
$W checkout -q -B side origin/main; $W commit -q --allow-empty -m "side"; side=$($W rev-parse HEAD); $W push -q origin "$side:refs/heads/side"
adv "p3"; line full pass "$side"; rm -f "$SG_STATE/gate-status-delta"
adv_file docs/other.md "after a side P"
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && git -C "$T/dtree" cat-file -e "$side^{commit}" 2>/dev/null && case "$out" in *"not an ancestor"*) ok "delta: a P that is not an ancestor refuses";; *) ko "ancestor: rc $rc, out '$out'";; esac || ko "ancestor: rc $rc, out '$out'"
printf '%s\tdelta\tpass\t%s\t%s\tx\n' "$(date +%s)" "$side" "$(tip dev)" > "$SG_STATE/gate-status-delta"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" != "$(tip dev)" ] && case "$out" in *"is not an ancestor"*) ok "promote: a hand-written delta with a non-ancestor P refuses";; *) ko "promote ancestor: rc $rc, out '$out'";; esac || ko "promote ancestor: rc $rc, out '$out'"

# (d) a new commit after the delta record refuses
adv "p4"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta"; rm -f "$SG_STATE/gate-carried"
adv_file docs/d.md "delta one"; bash "$d/delta-pass.sh" >/dev/null 2>&1
adv "after-record"; before=$(tip stg)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"stale because the tip moved"*) ok "delta: a commit after the delta record expires it";; *) ko "stale: rc $rc, out '$out'";; esac || ko "stale: rc $rc, stg moved or no reason, out '$out'"

# (e) a red delta refuses
adv "p5"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file docs/e.md "red delta"; SG_DELTA_SUITE=false bash "$d/delta-pass.sh" >/dev/null 2>&1
before=$(tip stg)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
case "$(cut -f3 "$SG_STATE/gate-status-delta" 2>/dev/null)" in fail) ok "delta: a red delta writes a fail record";; *) ko "red delta record not fail";; esac
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"the delta is a fail"*) ok "delta: a red delta promotes nothing";; *) ko "red delta: out '$out'";; esac || ko "red delta: rc $rc, out '$out'"

# (f) one P is carried once: a second delta from the same full pass refuses
adv "p6"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file docs/f1.md "carry one"; bash "$d/delta-pass.sh" >/dev/null 2>&1; bash "$d/promote-stg.sh" >/dev/null 2>&1
adv_file docs/f2.md "carry two"; bash "$d/delta-pass.sh" >/dev/null 2>&1; before=$(tip stg)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"already carried"*) ok "delta: a full pass is carried once";; *) ko "carried twice: out '$out'";; esac || ko "carried twice: rc $rc, out '$out'"

# (g) a change to the promotion code refuses, so it always needs the full proof
adv "p7"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file contrib/squad-goal/scripts/promote-stg.sh "promotion code"
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && case "$out" in *uncovered*) ok "delta: a change to the promotion code refuses";; *) ko "promotion code: out '$out'";; esac || ko "promotion code: rc $rc, out '$out'"

# (h) more than 25 changed paths refuse
adv "p8"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
$W fetch -q origin; $W checkout -q -B dev origin/dev; mkdir -p "$T/w/docs/bulk"
for i in $(seq 26); do echo "$i" > "$T/w/docs/bulk/$i.md"; done; $W add docs; $W commit -q -m "26 paths"; $W push -q origin dev
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && case "$out" in *"at most 25"*) ok "delta: more than 25 changed paths refuse";; *) ko "26 paths: rc $rc, out '$out'";; esac || ko "26 paths: rc $rc, out '$out'"

# (i) a binary change refuses
adv "p9"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
$W fetch -q origin; $W checkout -q -B dev origin/dev; printf '\000\001\002' > "$T/w/docs/blob.bin"; $W add docs/blob.bin; $W commit -q -m "binary"; $W push -q origin dev
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && case "$out" in *binary*) ok "delta: a binary change refuses";; *) ko "binary: rc $rc, out '$out'";; esac || ko "binary: rc $rc, out '$out'"

# (j) a dirty delta tree refuses, and the record already written stays
adv "p10"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file docs/j.md "dirty base"; bash "$d/delta-pass.sh" >/dev/null 2>&1; kept=$(cat "$SG_STATE/gate-status-delta" 2>/dev/null)
echo "local edit" >> "$T/dtree/docs/j.md"
adv_file docs/j2.md "after dirty"
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(cat "$SG_STATE/gate-status-delta" 2>/dev/null)" = "$kept" ] && case "$out" in *dirty*) ok "delta: a dirty delta tree refuses and keeps the old record";; *) ko "dirty: rc $rc, out '$out'";; esac || ko "dirty: rc $rc, out '$out'"
git -C "$T/dtree" checkout -q -- docs/j.md

# (k) a held full-proof-delta lock skips the tick: exit 73, no record, nothing run
adv "p11"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file docs/k.md "lock held"
SERIAL_HOLD_SECONDS=60 bash "$d/serial.sh" full-proof-delta sleep 60 2>/dev/null & hp=$!
for i in $(seq 50); do [ -f "$SG_STATE/locks/full-proof-delta/pid" ] && break; sleep 0.1; done
out=$(bash "$d/delta-pass.sh" 2>&1); rc=$?
kill -TERM "$hp" 2>/dev/null; wait "$hp" 2>/dev/null
[ $rc = 73 ] && [ ! -e "$SG_STATE/gate-status-delta" ] && case "$out" in *skipped*) ok "delta: a held lock skips the tick with exit 73";; *) ko "held lock: rc $rc, out '$out'";; esac || ko "held lock: rc $rc, out '$out'"

# (l) a tampered changed-file hash in the record refuses at promotion
adv "p12"; line full pass "$(tip dev)"; rm -f "$SG_STATE/gate-status-delta" "$SG_STATE/gate-carried"
adv_file docs/l.md "tamper"; bash "$d/delta-pass.sh" >/dev/null 2>&1
awk -F'\t' 'BEGIN{OFS="\t"} {$6="0000"; print}' "$SG_STATE/gate-status-delta" > "$SG_STATE/gate-status-delta.t" && mv "$SG_STATE/gate-status-delta.t" "$SG_STATE/gate-status-delta"
before=$(tip stg)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"no longer hashes"*) ok "delta: a tampered hash refuses at promotion";; *) ko "tamper: rc $rc, out '$out'";; esac || ko "tamper: rc $rc, out '$out'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
