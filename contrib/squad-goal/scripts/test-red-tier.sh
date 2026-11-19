#!/bin/bash
# seconds: 4.13
# The red tier's files (#2203): a red complete tier names the test files of its failing jobs in one record the loop reads, and
# the dev-to-stg promotion runs those files on the dev tip before it moves stg. Offline: a bare remote, a stub CI seam, a stub
# gh for the pull request, and a stub test runner. usage: bash test-red-tier.sh
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done   # hermetic: the live loop exports its own SG_* (#1410)
unset SG_SERIAL_HELD SG_CHILD_SESSION SERIAL_TRY
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git; ST=$T/state
export SG_STATE=$ST SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SQUAD_CAPS_DIR=$T/caps
mkdir -p "$ST" "$T/bin" "$T/w/tests"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main
cat > "$T/w/tests/ci_shards.py" <<'PY'
import sys
SHARDS = {"1": ["tests/test_one.py"], "2": ["tests/test_two.py", "tests/test_three.py"]}
print(" ".join(SHARDS[sys.argv[1]]))
PY
$W add tests/ci_shards.py; $W commit -q -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B dev origin/dev; $W commit -q --allow-empty -m "$1"; $W push -q origin dev; }
cat > "$T/ci.yml" <<'YAML'
jobs:
  full-serial-1:
    name: full-serial-1
    if: github.event.label.name == 'promote'
    steps:
      - run: python3 -m pytest -p no:xdist $(python3 tests/ci_shards.py 1)
  full-xdist-2:
    name: full-xdist-2
    if: github.event.label.name == 'promote'
    steps:
      - run: python3 -m pytest -n auto $(python3 tests/ci_shards.py 2)
  gui-e2e:
    name: gui-e2e
    if: github.event.label.name == 'promote'
    steps:
      - run: python3 -m pytest tests/test_gui.py
YAML
cat > "$T/runner-pass" <<EOF
#!/bin/bash
echo "RUN \$* @ \$(git rev-parse HEAD)" >> "$T/runner.log"
exit 0
EOF
cat > "$T/runner-fail" <<EOF
#!/bin/bash
echo "RUN \$* @ \$(git rev-parse HEAD)" >> "$T/runner.log"
echo "FAILED \$1"
exit 1
EOF
printf '#!/bin/bash\necho green\n' > "$T/ci"
chmod +x "$T/runner-pass" "$T/runner-fail" "$T/ci"; export SG_CI_STATE_CMD=$T/ci
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?state=open"*) ;;
  *"-X POST repos/x/emu/pulls"*) echo '{"number":7}';;
  *"-X PUT repos/x/emu/pulls/7/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/stg; then echo '{"merged":true}'; else echo '{"merged":false,"message":"push refused"}'; fi;;
  *"commits/"*"check-runs"*) echo '{"check_runs":[{"id":99,"name":"full-xdist-2","status":"completed","conclusion":"failure"}]}';;
  *"actions/jobs/99/logs"*) cat "$T/job-log" 2>/dev/null;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
record() { printf '%s\n' full-xdist-2 | bash "$d/red-tier.sh" record "$ST" "$T/w" "$(tip stg)" "$T/ci.yml"; }

record >/dev/null 2>&1
if [ "$(bash "$d/red-tier.sh" files "$ST" | tr '\n' ' ')" = "tests/test_two.py tests/test_three.py " ] && [ "$(head -n 1 "$ST/promote-red-tier" 2>/dev/null)" = "$(tip stg)" ]; then
  ok "1 a red tier names its failing jobs' test files in one record"
else ko "1 record names '$(bash "$d/red-tier.sh" files "$ST" 2>&1 | tr '\n' ' ')'"; fi

record >/dev/null 2>&1; had=0; [ -e "$ST/promote-red-tier" ] && had=1
bash "$d/red-tier.sh" clear "$ST"
if [ $had = 1 ] && [ -z "$(bash "$d/red-tier.sh" files "$ST" 2>/dev/null)" ] && [ ! -e "$ST/promote-red-tier" ]; then ok "2 clear removes the record"; else ko "2 clear: a record was written $had, and it stays after clear"; fi

adv "fix one"; D1=$(tip dev); record >/dev/null 2>&1
out=$(SG_RED_TIER_PYTEST=$T/runner-pass bash "$d/promote-stg.sh" 2>&1); rc=$?
if [ $rc = 0 ] && [ "$(tip stg)" = "$D1" ] && grep -qxF "RUN tests/test_two.py tests/test_three.py @ $D1" "$T/runner.log" 2>/dev/null; then
  ok "3 a red stg runs the failing files on the dev tip before stg moves"
else ko "3 red stg: rc $rc, stg $(tip stg), runner '$(cat "$T/runner.log" 2>/dev/null)', out '$out'"; fi

adv "fix two"; D2=$(tip dev); : > "$T/runner.log"; record >/dev/null 2>&1
SG_RED_TIER_PYTEST=$T/runner-fail bash "$d/promote-stg.sh" >/dev/null 2>&1; rc=$?
SG_RED_TIER_PYTEST=$T/runner-fail bash "$d/promote-stg.sh" >/dev/null 2>&1
if [ $rc != 0 ] && [ "$(tip stg)" = "$D1" ] && [ "$(grep -c 'tests/test_two.py' "$ST/alerts.log" 2>/dev/null)" = 1 ]; then
  ok "4 a dev tip that fails the files stays out of stg, with one alert that names them"
else ko "4 failing files: rc $rc, stg $(tip stg), alerts '$(cat "$ST/alerts.log" 2>/dev/null)'"; fi

adv "fix three"; D3=$(tip dev); : > "$T/runner.log"; record >/dev/null 2>&1
out=$(SG_RED_TIER_PYTEST=$T/runner-pass bash "$d/promote-stg.sh" 2>&1); rc=$?
if [ $rc = 0 ] && [ "$(tip stg)" = "$D3" ] && [ "$(wc -l < "$T/runner.log" | tr -d ' ')" = 1 ] && printf '%s' "$out" | grep -q PROMOTED && ! printf '%s' "$out" | grep -q pending; then
  ok "5 a red stg with a passing dev tip promotes at once"
else ko "5 red stg, passing tip: rc $rc, stg $(tip stg), out '$out'"; fi

adv "fix four"; D4=$(tip dev); bash "$d/red-tier.sh" clear "$ST"; : > "$T/runner.log"
out=$(SG_RED_TIER_PYTEST=$T/runner-fail bash "$d/promote-stg.sh" 2>&1); rc=$?
if [ $rc = 0 ] && [ "$(tip stg)" = "$D4" ] && [ ! -s "$T/runner.log" ]; then ok "6 no red record: the promotion runs no file"
else ko "6 no record: rc $rc, stg $(tip stg), runner '$(cat "$T/runner.log")'"; fi

cat > "$T/runner-named" <<EOF
#!/bin/bash
echo "RUN \$*" >> "$T/runner.log"
echo "collected 2 items"
echo "FAILED tests/test_two.py::test_b - AssertionError"
echo "1 failed, 1 passed"
exit 1
EOF
chmod +x "$T/runner-named"

adv "fix five"; D5=$(tip dev); record >/dev/null 2>&1
SG_RED_TIER_PYTEST=$T/runner-named bash "$d/promote-stg.sh" >/dev/null 2>&1
if [ -s "$ST/red-tier-run.log" ] && grep -qF "FAILED tests/test_two.py::test_b" "$ST/red-tier-run.log"; then
  ok "7 a failed red tier run keeps its pytest output in a log under the state directory"
else ko "7 red tier log: '$(cat "$ST/red-tier-run.log" 2>/dev/null)'"; fi

adv "fix six"; D6=$(tip dev); record >/dev/null 2>&1
out=$(SG_RED_TIER_PYTEST=$T/runner-named bash "$d/promote-stg.sh" 2>&1)
alert=$(tail -n 1 "$ST/alerts.log" 2>/dev/null)
if printf '%s' "$out" | grep -qF "tests/test_two.py::test_b" && ! printf '%s' "$out" | grep -qF "tests/test_three.py" \
  && printf '%s' "$alert" | grep -qF "tests/test_two.py::test_b" && ! printf '%s' "$alert" | grep -qF "tests/test_three.py"; then
  ok "8 the refusal and the alert name the failing tests, never the whole shard"
else ko "8 refusal '$out' alert '$alert'"; fi

adv "fix seven"; record_log() { printf '%s\n' full-xdist-2 | bash "$d/red-tier.sh" record "$ST" "$T/w" "$(tip dev)" "$T/ci.yml"; }
printf '%s\n' "2026-11-18T05:00:00Z ##[group]Run python3 -m pytest -n auto tests/test_two.py tests/test_three.py" \
  "2026-11-18T05:00:09Z FAILED tests/test_three.py::test_c - AssertionError" "2026-11-18T05:00:10Z ##[error]Process completed with exit code 1." > "$T/job-log"
record_log >/dev/null 2>&1
named=$(bash "$d/red-tier.sh" files "$ST" | tr '\n' ' ')
printf '%s\n' "2026-11-18T05:00:09Z all tests passed" > "$T/job-log"
record_log >/dev/null 2>&1
silent=$(bash "$d/red-tier.sh" files "$ST" | tr '\n' ' ')
if [ "$named" = "tests/test_three.py " ] && [ "$silent" = "tests/test_two.py tests/test_three.py " ]; then
  ok "9 the record holds the failing files the job log names, and the shard when it names none"
else ko "9 record named '$named' silent '$silent'"; fi

rm -rf "$T"
exit $fail
