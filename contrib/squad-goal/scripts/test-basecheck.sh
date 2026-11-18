#!/bin/bash
# Regression test for basecheck.sh (#786): a red completed check on the base branch head holds the landings of branchland.sh and
# wave.sh, is reported once per head (one alerts.log line; one queued comment on each issue whose landed row names that head), a
# red answer is read again after SG_BASE_CI_RECHECK, a green one is kept only once its checks are older than SG_BASE_CI_GRACE, a
# cache that is not a whole answer is read again, a pending, missing or unreadable check does not block, and a cancelled or
# timed-out check is unproven: it does not block and is not cached (ADR-0164, #1532). A failure check is released only on affirmative
# evidence: a completed job whose steps list is empty never started, so an all-never-started run is unproven and holds nothing
# (#1629). Silence releases nothing: a run the door cannot read, an empty job list, a failed job with no steps evidence and a check
# that names no run all leave the fail standing, so the head stays red. The callers' holds are
# proved by the fixture runs of test-branchland.sh and test-wave-land.sh. Stub gh (every argv logged), a bare local remote, a temp state
# dir per case. One line per case.
# usage: bash test-basecheck.sh [scripts dir]
unset SG_SERIAL_HELD SG_BASE_CI_GATE SG_BASE_CI_RECHECK SG_BASE_CI_GRACE SG_BASE_CI_OK_TTL   # an ambient gate or loop setting must not decide these cases
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/bin" "$T/co" "$T/jobs"
cp "$d/basecheck.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/ghthrottle.py" "$d/address.sh" "$T/s/"
$G init -q -b dev "$T/co" && (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git" 2>/dev/null
SHA=$($G -C "$T/origin.git" rev-parse refs/heads/dev); S9=${SHA:0:9}
# The stub gh: check-runs answers CHECKS (GHFAIL makes the read fail); the jobs of a run answer the page fixture named by its id
# (a `refuse-<id>` marker refuses the read, a `fail-<id>` marker fails it, no fixture is an empty job list); every other route
# answers an empty list. The jobs route answers an object, as GitHub does.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"/check-runs"*) [ -n "\${GHFAIL:-}" ] && exit 1; echo "\${CHECKS:-{\"check_runs\":[]\}}";;
  *"/actions/runs/"*"/jobs"*)
    id=\$(printf '%s' "\$*" | sed -n 's#.*/actions/runs/\([0-9][0-9]*\)/jobs.*#\1#p')
    pg=\$(printf '%s' "\$*" | sed -n 's#.*&page=\([0-9][0-9]*\).*#\1#p'); pg=\${pg:-1}
    [ -e "$T/jobs/refuse-\$id" ] && { echo '{"message":"Resource not accessible by integration","status":"403"}'; exit 1; }
    [ -e "$T/jobs/fail-\$id" ] && exit 1
    f="$T/jobs/run-\$id-page-\$pg.json"
    if [ -f "\$f" ]; then cat "\$f"; else echo '{"total_count":0,"jobs":[]}'; fi;;
  *) echo "[]";;
esac
J
chmod +x "$T/bin/gh"
# The mv shim logs each rename: sg_base_put must move the temporary file into place, so a plain write fails the case below (#790).
printf '#!/bin/bash\necho "$*" >> "%s/mv.log"\nexec /bin/mv "$@"\n' "$T" > "$T/bin/mv"; chmod +x "$T/bin/mv"; export PATH="$T/bin:$PATH"
# The driver: one call of sg_base_red for dev, in the state directory the case names.
cat > "$T/s/driver.sh" <<J
#!/bin/bash
export SG_STATE=\$STATE SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 SG_LAND_REMOTE=$T/origin.git SG_GHREAD="python3 $T/s/ghthrottle.py" SG_GH_PASS=basecheck
. "$T/s/ghcall.sh"; . "$T/s/address.sh"; . "$T/s/basecheck.sh"
sg_base_red dev
J
# Completed checks carry completed_at: the green answer is kept only when that is old enough (SG_BASE_CI_GRACE, default 900 s).
NOW=$(date -u +%FT%TZ)
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
FRESH='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"'"$NOW"'"}]}'
# RED names a run whose one job failed after it ran a step, so it is a genuine red (#1629); the fixture is written below.
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure","completed_at":"2001-01-01T00:00:00Z","details_url":"https://github.com/x/y/actions/runs/9001/job/10"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
# run <state name> [VAR=value ...]: the driver in a fresh state dir; sets OUT and RC, the state dir is $T/st-<name>.
run() { local n=$1; shift; export STATE="$T/st-$n"; mkdir -p "$STATE/auto"; : > "$T/gh.log"; OUT=$(env "$@" bash "$T/s/driver.sh" 2>&1); RC=$?; }
landed() { mkdir -p "$T/st-$1"; printf '1700000000\tgui/7-x\t%s\t7\n' "$SHA" > "$T/st-$1/landed.tsv"; }
reads() { /usr/bin/grep -c '/check-runs' "$T/gh.log"; }
alerts() { /usr/bin/grep -c 'BASE CI' "$T/st-$1/alerts.log" 2>/dev/null; }
cqs() { ls "$T/st-$1/cq" 2>/dev/null | /usr/bin/grep -c "$2"; }
# (#1629) The fixture helpers: ckred builds one failure check whose details_url names a workflow run; jobpage writes one page of
# that run's jobs in GitHub's object shape; jobrefuse/jobfail make the jobs read refuse or fail. Every run id is unique per case.
ckred() { printf '{"check_runs":[{"id":10,"name":"%s","status":"completed","conclusion":"failure","completed_at":"2001-01-01T00:00:00Z","details_url":"https://github.com/x/y/actions/runs/%s/job/10"}]}' "$1" "$2"; }
jobpage() { local n; n=$(printf '%s' "$3" | jq 'length' 2>/dev/null); printf '{"total_count":%s,"jobs":%s}\n' "${n:-0}" "$3" > "$T/jobs/run-$1-page-$2.json"; }
jobrefuse() { : > "$T/jobs/refuse-$1"; }
jobfail() { : > "$T/jobs/fail-$1"; }
# The RED fixture's run: one job that ran a step and failed.
jobpage 9001 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":[{"name":"Run tests","number":1,"conclusion":"failure"}]}]'

# A green head does not block. Its checks are old enough, so the answer is cached as "ok <epoch>" and not read again.
landed green; run green CHECKS="$GREEN"
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a green base head does not block" || ko "green: rc $RC out [$OUT]"
/usr/bin/grep -qE "^ok [0-9]+$" "$T/st-green/auto/basecheck-$SHA" 2>/dev/null && ok "an old green answer is cached as ok" || ko "no ok cache: $(cat "$T/st-green/auto/basecheck-$SHA" 2>/dev/null)"
run green CHECKS="$GREEN"
[ "$(reads)" = 0 ] && ok "a cached green answer is not read again" || ko "the cached head was read $(reads) times"
# A green whose checks finished just now is not cached: a late job can still appear, so the head is read again.
landed fresh; run fresh CHECKS="$FRESH"
[ ! -e "$T/st-fresh/auto/basecheck-$SHA" ] && ok "a green answer whose checks are fresh is not cached" || ko "a fresh green was cached: $(cat "$T/st-fresh/auto/basecheck-$SHA")"

# A red completed check blocks, names the base and the check, is reported once, and is commented on the issue landed at that head.
landed red; run red CHECKS="$RED"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && ok "a red base head blocks and names the check" || ko "red: rc $RC out [$OUT]"
[ "$(alerts red)" = 1 ] && ok "the red head raises one alerts.log line" || ko "alerts.log: $(cat "$T/st-red/alerts.log" 2>/dev/null)"
[ "$(cqs red '#7-basecheck')" = 1 ] && ok "one queued comment on the issue landed at that head" || ko "cq: $(ls "$T/st-red/cq" 2>/dev/null)"
/usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-red/auto/basecheck-$SHA" && ok "the red answer is cached as red2, with its read time" || ko "red cache: $(cat "$T/st-red/auto/basecheck-$SHA" 2>/dev/null)"
/usr/bin/grep -q 'per_page=100&filter=latest' "$T/gh.log" && ok "the check runs are read with per_page=100 and filter=latest" || ko "the route: $(cat "$T/gh.log")"
# Within SG_BASE_CI_RECHECK the red answer is kept: no read, no second line, no second comment.
run red CHECKS="$RED"
[ "$RC" = 0 ] && [ "$(reads)" = 0 ] && ok "a red answer inside SG_BASE_CI_RECHECK is not read again" || ko "red again: rc $RC reads $(reads)"
[ "$(alerts red)" = 1 ] && [ "$(cqs red '#7-basecheck')" = 1 ] && ok "the red head is reported once, not per call" || ko "a repeat report: alerts [$(cat "$T/st-red/alerts.log")] cq [$(ls "$T/st-red/cq")]"

# Once SG_BASE_CI_RECHECK has passed, a red answer is read again. A job the owner re-ran turns green, and the head stops blocking.
run red CHECKS="$GREEN" SG_BASE_CI_RECHECK=0
[ "$RC" = 1 ] && [ "$(reads)" = 1 ] && ok "an expired red answer is read again, and a green re-run releases the head" || ko "re-run: rc $RC reads $(reads) out [$OUT]"
# A red that stays red after the window is read again, and still reported once.
run red2 CHECKS="$RED"
run red2 CHECKS="$RED" SG_BASE_CI_RECHECK=0
[ "$RC" = 0 ] && [ "$(reads)" = 1 ] && [ "$(alerts red2)" = 1 ] && ok "a red still red after the window is read again, reported once" || ko "red window: rc $RC reads $(reads) alerts $(alerts red2)"

# A cache that is not a whole answer is no answer: a partial line from a killed write is read again, and never holds the landings.
landed partial; mkdir -p "$T/st-partial/auto"; printf 'check (3.11) ended fail' > "$T/st-partial/auto/basecheck-$SHA"
run partial CHECKS="$GREEN"
[ "$(reads)" = 1 ] && [ "$RC" = 1 ] && ok "a partial cache line is no answer: the head is read again" || ko "partial: reads $(reads) rc $RC out [$OUT]"
landed torn; mkdir -p "$T/st-torn/auto"; printf 'red 99999999999' > "$T/st-torn/auto/basecheck-$SHA"
run torn CHECKS="$RED"
[ "$RC" = 0 ] && [ "$(reads)" = 1 ] && ok "a torn red line (no name) is no answer: the head is read again" || ko "torn: reads $(reads) rc $RC out [$OUT]"
# A write goes through a temporary file that is renamed: no tmp file is left in the state directory.
[ -z "$(ls "$T/st-red/auto" | /usr/bin/grep '\.tmp\.')" ] && ok "the cache is written by one rename: no temporary file is left" || ko "a temporary file is left: $(ls "$T/st-red/auto")"

# No landed row at that head: nothing is commented on an unrelated issue; the alerts.log line is still written.
mkdir -p "$T/st-last"; printf '1700000000\tgui/5-x\tdeadbeef\t5\n1700000100\tgui/9-x\tcafef00d\t9\n' > "$T/st-last/landed.tsv"; run last CHECKS="$RED"
[ "$(alerts last)" = 1 ] && [ "$(ls "$T/st-last/cq" 2>/dev/null | wc -l | tr -d ' ')" = 0 ] && ok "without a landed row at the head, only alerts.log is written" || ko "last: alerts $(alerts last) cq $(ls "$T/st-last/cq" 2>/dev/null)"

# A pending check and a missing check do not block; neither is cached.
run pending CHECKS="$RUNNING"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-pending/auto/basecheck-$SHA" ] && ok "a pending check does not block and is not cached" || ko "pending: rc $RC out [$OUT]"
run missing
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "a head with no check runs does not block" || ko "missing: rc $RC out [$OUT]"

# An unreadable check list does not block, and is named once per head on alerts.log.
run unreadable GHFAIL=1
[ "$RC" = 1 ] && ok "an unreadable check list does not block" || ko "unreadable: rc $RC out [$OUT]"
[ "$(/usr/bin/grep -c 'is not read' "$T/st-unreadable/alerts.log" 2>/dev/null)" = 1 ] && ok "an unreadable read is named on alerts.log" || ko "no unreadable line: $(cat "$T/st-unreadable/alerts.log" 2>/dev/null)"
run unreadable GHFAIL=1
[ "$(/usr/bin/grep -c 'is not read' "$T/st-unreadable/alerts.log" 2>/dev/null)" = 1 ] && ok "an unreadable read is named once per head" || ko "unreadable repeat: $(cat "$T/st-unreadable/alerts.log" 2>/dev/null)"

# SG_BASE_CI_GATE=0 turns the whole check off, red or not.
run off CHECKS="$RED" SG_BASE_CI_GATE=0
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-off/alerts.log" ] && ok "SG_BASE_CI_GATE=0 turns it off" || ko "off: rc $RC out [$OUT]"

# The shared classifier: red, running, green, none (zero runs) and not-a-list, as promote-stg.sh reads them.
c() { printf '%s' "$1" | bash -c '. "$1"; sg_ci_class' _ "$T/s/basecheck.sh" 2>/dev/null; }
[ "$(c "$RED")" = red ] && [ "$(c "$RUNNING")" = running ] && [ "$(c "$GREEN")" = green ] && [ "$(c '{"check_runs":[]}')" = none ] && ok "the classifier reads red, running, green and none" || ko "classifier: [$(c "$RED")] [$(c "$RUNNING")] [$(c "$GREEN")] [$(c '{"check_runs":[]}')]"
[ -z "$(c '{}')" ] && ok "the classifier names no state for an answer that is not a check-run list" || ko "classifier on {}: [$(c '{}')]"
bash -c '. "$1"; r=$(printf "%s" "$2" | sg_ci_class) || r=none; [ "$r" = red ] && [ "$(printf "%s" "$2" | sg_ci_bad)" = "check (3.11) ended failure" ]' _ "$T/s/basecheck.sh" "$RED" && ok "the classifier names the first red check" || ko "sg_ci_bad: [$(printf '%s' "$RED" | bash -c '. "$1"; sg_ci_bad' _ "$T/s/basecheck.sh")]"

# (#790) The answer is written by one rename: the mv shim sees the temporary file moved onto the cache, so a plain write fails here.
landed rename; : > "$T/mv.log"; run rename CHECKS="$RED"
/usr/bin/grep -q -- "-f $T/st-rename/auto/basecheck-$SHA.tmp." "$T/mv.log" && [ -s "$T/st-rename/auto/basecheck-$SHA" ] && ok "the red answer is moved into place by mv" || ko "no rename of the red answer: mv.log [$(cat "$T/mv.log")]"

# (#790) An ok answer expires: inside SG_BASE_CI_OK_TTL it is kept, and once the TTL has passed the head is read again, so a
# check the owner re-ran red is seen.
landed okkept; run okkept CHECKS="$GREEN"; run okkept CHECKS="$RED"
[ "$RC" = 1 ] && [ -z "$OUT" ] && ok "an ok answer inside SG_BASE_CI_OK_TTL is kept" || ko "ok kept: rc $RC out [$OUT]"
landed okttl; run okttl CHECKS="$GREEN"; run okttl CHECKS="$RED" SG_BASE_CI_OK_TTL=0
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && ok "an expired ok answer is read again and a failed re-run blocks" || ko "ok expired: rc $RC out [$OUT]"

# (#790) The base is read once per watch cycle, though no landing runs: basecheckpass.sh is that read, and reports a red head.
cp "$d/basecheckpass.sh" "$T/s/"; sed '$d' "$T/s/driver.sh" > "$T/s/cycle.sh"; echo "bash \"$T/s/basecheckpass.sh\"" >> "$T/s/cycle.sh"
landed cycle; mkdir -p "$T/st-cycle/auto"; : > "$T/gh.log"
OUT=$(env STATE="$T/st-cycle" CHECKS="$RED" bash "$T/s/cycle.sh" 2>&1); RC=$?
[ "$RC" = 0 ] && [ "$(alerts cycle)" = 1 ] && ok "a watch cycle with no landing reads the base and reports a red head" || ko "cycle pass: rc $RC alerts $(alerts cycle) out [$OUT]"
OUT=$(env STATE="$T/st-cycle" CHECKS="$RED" bash "$T/s/cycle.sh" 2>&1)
[ "$(alerts cycle)" = 1 ] && [ "$(reads)" = 1 ] && ok "the next cycle keeps the red answer: no second read, no second line" || ko "cycle repeat: alerts $(alerts cycle) reads $(reads)"

# (#1532, #1629) A cancelled or timed-out check measured nothing (ADR-0164): the complete tier ends cancelled at its job timeout.
# Such a head is unproven: it does not block, is not cached, writes one alerts.log line naming the cause, and is read again.
CANC='{"check_runs":[{"id":10,"name":"full-serial","status":"completed","conclusion":"cancelled","completed_at":"2001-01-01T00:00:00Z"},{"id":11,"name":"ruff","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
TOUT='{"check_runs":[{"id":10,"name":"full-xdist","status":"completed","conclusion":"timed_out","completed_at":"2001-01-01T00:00:00Z"},{"id":11,"name":"ruff","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
for k in canc tout; do v=CANC; [ $k = tout ] && v=TOUT
  landed $k; run $k CHECKS="${!v}"
  [ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-$k/auto/basecheck-$SHA" ] && [ "$(alerts $k)" = 1 ] && [ "$(cqs $k basecheck)" = 0 ] && /usr/bin/grep -q 'is unproven:' "$T/st-$k/alerts.log" && ok "$k: an unproven head does not block, is not cached, and names its cause once" || ko "$k: rc $RC out [$OUT] cache [$(cat "$T/st-$k/auto/basecheck-$SHA" 2>/dev/null)] alerts $(alerts $k)"
  run $k CHECKS="${!v}"
  [ "$(reads)" = 1 ] && [ "$(alerts $k)" = 1 ] && ok "$k: an unproven head is read again, and its line is said once" || ko "$k: reads $(reads) alerts $(alerts $k)"
  [ "$(c "${!v}")" = unproven ] && ok "$k: the classifier reads unproven, never green or red" || ko "$k classifier: [$(c "${!v}")]"
done
MIXED='{"check_runs":[{"id":10,"name":"full-serial","status":"completed","conclusion":"cancelled","completed_at":"2001-01-01T00:00:00Z"},{"id":11,"name":"ruff","status":"completed","conclusion":"failure","completed_at":"2001-01-01T00:00:00Z"}]}'
[ "$(c "$MIXED")" = red ] && [ "$(printf '%s' "$MIXED" | bash -c '. "$1"; sg_ci_bad' _ "$T/s/basecheck.sh")" = "ruff ended failure" ] && ok "a failure beside a cancelled check is red and names the failure" || ko "mixed: [$(c "$MIXED")] [$(printf '%s' "$MIXED" | bash -c '. "$1"; sg_ci_bad' _ "$T/s/basecheck.sh")]"

# (#1629) sg_ci_refine: the red a base head carries is decided by the evidence its jobs hold, not by the conclusion alone. Each
# case drives the REAL sg_base_red through the driver and a stub door, and reads the exit code, the cache and alerts.log.
ref() { bash -c '. "$1"; sg_ci_refine "$2"' _ "$T/s/basecheck.sh" "$1" 2>/dev/null; }
[ "$(ref "$GREEN")" = green ] && [ "$(ref "$RUNNING")" = running ] && [ "$(ref "$CANC")" = unproven ] && [ "$(ref '{"check_runs":[]}')" = none ] \
  && [ -z "$(ref '{}')" ] && ok "sg_ci_refine prints a non-red class unchanged and names nothing for a non-list" || ko "sg_ci_refine class: [$(ref "$GREEN")] [$(ref "$RUNNING")] [$(ref "$CANC")] [$(ref '{}')]"

# (a) Every job of the run failed before its first step: nothing measured the head, so it is unproven, holds no landing, is not
# cached, and names its cause once on alerts.log.
jobpage 1611 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":[]}]'
q=$(ckred 'check (3.11)' 1611); run quiet CHECKS="$q"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-quiet/auto/basecheck-$SHA" ] && [ "$(alerts quiet)" = 1 ] && /usr/bin/grep -q 'is unproven:' "$T/st-quiet/alerts.log" \
  && ok "(a) all jobs failed before their first step: unproven, uncached, one alerts line" || ko "(a): rc $RC out [$OUT] cache [$(cat "$T/st-quiet/auto/basecheck-$SHA" 2>/dev/null)] alerts [$(cat "$T/st-quiet/alerts.log" 2>/dev/null)]"

# (b) One job ran a step and failed: a genuine red holds the landing, is cached as red2, and is reported once.
q=$(ckred 'check (3.11)' 9001); run genuine CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-genuine/auto/basecheck-$SHA" && [ "$(alerts genuine)" = 1 ] \
  && ok "(b) a job that ran a step and failed: red, cached as red2, reported once" || ko "(b): rc $RC out [$OUT] cache [$(cat "$T/st-genuine/auto/basecheck-$SHA" 2>/dev/null)] alerts $(alerts genuine)"

# (c) The passed jobs ran while the failed jobs never started: no failed job ran a step, so the run is quiet and the head is unproven.
jobpage 1613 1 '[{"name":"full-serial","status":"completed","conclusion":"failure","steps":[]},{"name":"ruff","status":"completed","conclusion":"success","steps":[{"name":"Run tests"}]}]'
q=$(ckred 'full-serial' 1613); run passedran CHECKS="$q"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-passedran/auto/basecheck-$SHA" ] && /usr/bin/grep -q 'is unproven:' "$T/st-passedran/alerts.log" \
  && ok "(c) passed jobs ran, failed jobs never started: unproven, uncached" || ko "(c): rc $RC out [$OUT] cache [$(cat "$T/st-passedran/auto/basecheck-$SHA" 2>/dev/null)]"

# (d) Two runs, one quiet and one with a genuine failure: the red run answers for the head, whatever the quiet run said.
jobpage 1614 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":[]}]'
jobpage 1615 1 '[{"name":"ruff","status":"completed","conclusion":"failure","steps":[{"name":"Run tests"}]}]'
q='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure","details_url":"https://github.com/x/y/actions/runs/1614/job/10"},{"id":11,"name":"ruff","status":"completed","conclusion":"failure","details_url":"https://github.com/x/y/actions/runs/1615/job/11"}]}'
run twored CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: ruff ended failure" ] && ok "(d) a quiet run beside a genuine failure: red wins and names the measured check" || ko "(d): rc $RC out [$OUT]"

# (e) The jobs read is refused: silence releases nothing, so the fail stands, the head is red, and it is cached and reported.
jobrefuse 1616; q=$(ckred 'check (3.11)' 1616); run refused CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-refused/auto/basecheck-$SHA" && [ "$(alerts refused)" = 1 ] \
  && ok "(e) a refused jobs read: the fail stands, red and cached" || ko "(e): rc $RC out [$OUT] cache [$(cat "$T/st-refused/auto/basecheck-$SHA" 2>/dev/null)] alerts [$(cat "$T/st-refused/alerts.log" 2>/dev/null)]"
# A read that fails before GitHub reaches a verdict releases nothing either: red, never unproven.
jobfail 1623; q=$(ckred 'check (3.11)' 1623); run jobfailed CHECKS="$q"
[ "$RC" = 0 ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-jobfailed/auto/basecheck-$SHA" \
  && ok "(e) a failed jobs read: red too" || ko "(e failed): rc $RC cache [$(cat "$T/st-jobfailed/auto/basecheck-$SHA" 2>/dev/null)]"

# (f) A genuine failure beside a refused run: the head is red either way. The refused check's fail now stands, so it may answer
# first; what the guard holds is that a head with a measured red never lands, whatever else the runs say.
jobrefuse 1617
jobpage 1618 1 '[{"name":"mypy","status":"completed","conclusion":"failure","steps":[{"name":"Run mypy"}]}]'
q='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure","details_url":"https://github.com/x/y/actions/runs/1617/job/10"},{"id":11,"name":"mypy","status":"completed","conclusion":"failure","details_url":"https://github.com/x/y/actions/runs/1618/job/11"}]}'
run mixref CHECKS="$q"
case "$OUT" in "base dev is red at $S9: "*" ended failure") r=0;; *) r=1;; esac
[ "$RC" = 0 ] && [ "$r" = 0 ] && ok "(f) a genuine failure beside a refused run: red, and it holds" || ko "(f): rc $RC out [$OUT]"

# (g) A run's jobs split across two pages: every page is read, and a failure on the second page is still red.
jobpage 1619 1 "$(jq -n '[range(100) | {name:"job", status:"completed", conclusion:"success", steps:[{name:"Run"}]}]')"
jobpage 1619 2 '[{"name":"full-serial","status":"completed","conclusion":"failure","steps":[{"name":"Run tests"}]}]'
q=$(ckred 'full-serial' 1619); run paged CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: full-serial ended failure" ] && /usr/bin/grep -q 'actions/runs/1619/jobs?per_page=100&page=1' "$T/gh.log" && /usr/bin/grep -q 'actions/runs/1619/jobs?per_page=100&page=2' "$T/gh.log" \
  && ok "(g) a job list split across two pages: both pages read, the second page's failure is red" || ko "(g): rc $RC out [$OUT] reads [$(/usr/bin/grep -o 'actions/runs/1619/jobs[^ ]*' "$T/gh.log" | tr '\n' ' ')]"

# (h) The jobs read answers an empty job list: nothing shows the failed job never started, so the fail stands.
q=$(ckred 'check (3.11)' 1620); run emptyjobs CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: check (3.11) ended failure" ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-emptyjobs/auto/basecheck-$SHA" \
  && ok "(h) an empty job list: red, never unproven" || ko "(h): rc $RC out [$OUT] cache [$(cat "$T/st-emptyjobs/auto/basecheck-$SHA" 2>/dev/null)]"

# (i) A `red` line left by an older build is no answer: the head is read again, and a green re-run releases it.
mkdir -p "$T/st-oldred/auto"; printf 'red 9999999999 check (3.11) ended failure\n' > "$T/st-oldred/auto/basecheck-$SHA"
run oldred CHECKS="$FRESH"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ "$(reads)" = 1 ] && /usr/bin/grep -qx 'red 9999999999 check (3.11) ended failure' "$T/st-oldred/auto/basecheck-$SHA" \
  && ok "(i) an old red line is no answer: the head is read again" || ko "(i): rc $RC out [$OUT] reads $(reads) cache [$(cat "$T/st-oldred/auto/basecheck-$SHA" 2>/dev/null)]"

# (j) A failed job whose steps evidence is missing or malformed never releases the fail: the head stays red.
jobpage 1621 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure"}]'
q=$(ckred 'check (3.11)' 1621); run nosteps CHECKS="$q"
[ "$RC" = 0 ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-nosteps/auto/basecheck-$SHA" \
  && ok "(j) a job with no steps evidence (missing): red" || ko "(j missing): rc $RC cache [$(cat "$T/st-nosteps/auto/basecheck-$SHA" 2>/dev/null)]"
jobpage 1622 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":"nope"}]'
q=$(ckred 'check (3.11)' 1622); run badsteps CHECKS="$q"
[ "$RC" = 0 ] && /usr/bin/grep -qx "red2 [0-9]* check (3.11) ended failure" "$T/st-badsteps/auto/basecheck-$SHA" \
  && ok "(j) a job with malformed steps evidence: red" || ko "(j malformed): rc $RC cache [$(cat "$T/st-badsteps/auto/basecheck-$SHA" 2>/dev/null)]"

# (k) A job still running beside a failed job that never started: a job in flight is neither a pass nor a fail, so the run is quiet
# and the head is unproven. Reading the running job's steps as a fail would hold every landing on nothing.
jobpage 1624 1 '[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":[]},{"name":"full-xdist","status":"in_progress","conclusion":null,"steps":[{"name":"Run tests","conclusion":null}]}]'
q=$(ckred 'check (3.11)' 1624); run running CHECKS="$q"
[ "$RC" = 1 ] && [ -z "$OUT" ] && [ ! -e "$T/st-running/auto/basecheck-$SHA" ] && /usr/bin/grep -q 'is unproven:' "$T/st-running/alerts.log" \
  && ok "(k) a running sibling never makes a quiet run red" || ko "(k): rc $RC out [$OUT] alerts [$(cat "$T/st-running/alerts.log" 2>/dev/null)]"

# (l) A failed check that names no workflow run (a check outside Actions) has no evidence to read: the fail stands.
q='{"check_runs":[{"id":10,"name":"codecov","status":"completed","conclusion":"failure"}]}'
run norun CHECKS="$q"
[ "$RC" = 0 ] && [ "$OUT" = "base dev is red at $S9: codecov ended failure" ] && /usr/bin/grep -qx "red2 [0-9]* codecov ended failure" "$T/st-norun/auto/basecheck-$SHA" \
  && ok "(l) a failed check that names no run: red, not released" || ko "(l): rc $RC out [$OUT] cache [$(cat "$T/st-norun/auto/basecheck-$SHA" 2>/dev/null)]"

[ "$fail" = 0 ] && echo "PASS test-basecheck" || { echo "FAIL test-basecheck"; exit 1; }
