#!/bin/bash
# seconds: 42.69
# The promotion line (ADR-0145, #439): promote-stg.sh moves stg to the dev tip only when the promotion proof is green on that
# tip, and never otherwise. SG_PROMOTE_PROOF names the proof.
#   ci (the default): the dev tip's CI. Green promotes; red, running or a missing run promotes nothing.
#   local: the promotion gate's full-result line, $SG_STATE/gate-status-full, which the run that earns it writes through the existing
#     seam: `SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'` (gate.sh's header names the line and the kind). stg
#     moves only when that line says `full`, `pass`, and names the dev tip exactly.
# The owner fast-forwards main to the stg tip with one command, and it refuses when main is not an ancestor of stg
# (promote-main.sh). No loop script pushes to main.
# Offline: a bare local remote, a stub CI seam and a hand-written status line, no gh. usage: bash test-promote.sh
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so its wait is proved
unset SG_CHILD_SESSION   # the merge door refuses a child session; the merge here is the gh stub's, into a local bare remote
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
# SQUAD_CAPS_DIR is a throwaway: the promotion takes the machine-wide gate budget, and a box whose slots other gates hold
# would refuse this suite's promotions with no defect to find (#1171). test-promote-pass.sh takes the same budget for itself.
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SQUAD_CAPS_DIR=$T/caps; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# ci <green|red|running>: the stub CI seam. It logs the sha it was read for, so a test proves which proof the script used.
# #1644: the complete tier is the jobs the workflow starts on the `promote` label alone, and the proof reads them beside the
# class. This fixture names two of them, and every ci() answer writes the check runs of the tip: the complete tier green on a
# green answer, so the two halves of the proof agree.
printf "jobs:\n  ruff:\n    name: ruff\n  fast-battery:\n    name: fast-battery\n  complete-guard:\n    name: complete-guard\n    if: github.head_ref == 'dev' && github.event.label.name == 'promote'\n  full-xdist:\n    name: full-xdist\n    if: github.head_ref == 'dev' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n  gui-e2e:\n    name: gui-e2e\n    if: github.head_ref == 'dev' && github.event.label.name == 'promote'\n    needs: [complete-guard]\n" > "$T/ci.yml"
export SG_CI_WORKFLOW=$T/ci.yml
case "$(bash "$d/promote-pass.sh" --ci-complete-job-names "$T/ci.yml" | tr '\n' ' ')" in
  "full-xdist gui-e2e ") ok "the complete tier names the jobs that wait on the guard, never the guard itself (#1644)";;
  *) ko "complete tier names the guard or misses a job: '$(bash "$d/promote-pass.sh" --ci-complete-job-names "$T/ci.yml" | tr '\n' ' ')'";;
esac
checks() {  # <fast> <complete>: the check runs of the tip. A half is a conclusion, `running` or `none` (no run at all).
  local f=$1 c=$2 n out=""
  for n in ruff fast-battery; do
    case $f in none) ;; running) out="$out{\"name\":\"$n\",\"status\":\"in_progress\",\"conclusion\":null},";;
      *) out="$out{\"name\":\"$n\",\"status\":\"completed\",\"conclusion\":\"$f\"},";; esac
  done
  for n in full-xdist gui-e2e; do
    case $c in none) ;; running) out="$out{\"name\":\"$n\",\"status\":\"in_progress\",\"conclusion\":null},";;
      *) out="$out{\"name\":\"$n\",\"status\":\"completed\",\"conclusion\":\"$c\"},";; esac
  done
  printf '{"check_runs":[%s]}\n' "${out%,}" > "$T/checks.json"
}
ci() { local c=$1; case "$c" in green) c=success;; red) c=failure;; esac   # the class word is not a conclusion
  printf '#!/bin/bash\necho "ci read %s" >> "%s/ci.log"\necho "%s"\n' "$1" "$T" "$1" > "$T/ci"; chmod +x "$T/ci"; export SG_CI_STATE_CMD=$T/ci; checks "$c" "$c"; }
# line <kind> <result> <sha>: gate.sh keeps the full result apart from the hub's latest status.
line() { printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$1" "$2" "$3" > "$SG_STATE/gate-status"; [ "$1" != full ] || cp "$SG_STATE/gate-status" "$SG_STATE/gate-status-full"; }
# The stub GitHub (#743): the open dev -> stg pull request lives in $T/pr, and merging it moves stg through the same bare
# remote a real merge moves. It logs every call, so a test names the route the promotion read or wrote, and proves no push.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *graphql*) if [ -e "$T/pr" ]; then printf '{"data":{"repository":{"pullRequests":{"nodes":[{"number":%s,"headRefName":"dev","baseRefName":"stg","isDraft":false,"labels":{"nodes":[]}}],"pageInfo":{"hasNextPage":false}}}}}\n' "\$(cat "$T/pr")"; else echo '{"data":{"repository":{"pullRequests":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}'; fi;;
  *"-X POST repos/x/emu/pulls"*) echo 7 > "$T/pr"; echo '{"number":7}';;
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/stg; then rm -f "$T/pr"; echo '{"merged":true,"message":"Pull Request successfully merged"}'; else echo '{"merged":false,"message":"Pull Request is not mergeable"}'; fi;;
  *"/actions/runs/"*"/jobs"*)
    id=\$(printf '%s' "\$*" | sed -n 's#.*/actions/runs/\([0-9][0-9]*\)/jobs.*#\1#p')
    if [ -f "$T/jobs-\$id.json" ]; then cat "$T/jobs-\$id.json"; else echo '{"total_count":1,"jobs":[{"conclusion":"failure","steps":[{"name":"run"}]}]}'; fi;;
  *"/check-runs"*) cat "$T/checks.json";;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The promotion proof is a profile setting (#439): the default lives in env.sh after the profile, so a profile that names it
# wins. The real env.sh is sourced under a throwaway profile directory, so the value is read from the code the loop runs.
env_proof() {  # $1: the profile's own lines. Prints the SG_PROMOTE_PROOF the real env.sh resolves.
  local pd=$T/prof
  rm -rf "$pd"; mkdir -p "$pd"
  { printf '%s\n' ': "${SG_REPO:=x/emu}"' ': "${SG_OWNER:=x}"' ': "${SG_PROJECT:=1}"' ': "${SG_CHECKOUT:=$HOME/co}"' ': "${SG_BASE:=dev}"'
    [ -n "${1:-}" ] && printf '%s\n' "$1"; } > "$pd/agent-squad.sh"
  env -i PATH="$PATH" HOME="$HOME" SG_PROFILES_DIR="$pd" SG_PROFILE=agent-squad SG_STATE_BASE="$T/pcache" SG_SQUAD="$T/squad" \
    bash -c ". '$d/env.sh' >/dev/null 2>&1 || exit 2; printf '%s\n' \"\${SG_PROMOTE_PROOF:-unset}\""
}
out=$(env_proof ': "${SG_PROMOTE_PROOF:=local}"')
[ "$out" = local ] && ok "a profile's SG_PROMOTE_PROOF wins over the env.sh default" || ko "the profile's proof did not win (got '$out')"
out=$(env_proof '')
[ "$out" = ci ] && ok "with no profile setting it, the promotion proof is ci" || ko "the default promotion proof is not ci (got '$out')"
base=$(tip main)

# The default proof is `ci`: with no SG_PROMOTE_PROOF set the CI seam decides, and a green CI promotes.
unset SG_PROMOTE_PROOF
adv dev one; ci green; rm -f "$T/ci.log" "$T/pr"; : > "$T/gh.log"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ -s "$T/ci.log" ] && ok "the default proof is ci: the CI seam is read with SG_PROMOTE_PROOF unset" || ko "the default proof did not read the CI seam"
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a dev tip whose CI is green is promoted to stg" || ko "green CI: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ok "the promotion opens the dev -> stg pull request (#743)" || ko "no pull request opened: $(cat "$T/gh.log")"
/usr/bin/grep -q -- "-X PUT repos/x/emu/pulls/7/merge" "$T/gh.log" && ok "the promotion merges the pull request once the proof is green" || ko "no merge: $(cat "$T/gh.log")"
/usr/bin/grep -q "refs/heads/stg" "$T/gh.log" && ko "the promotion still names the stg ref on GitHub" || ok "the promotion never writes refs/heads/stg"
# One pull request at a time: an open one is reused, never opened twice.
: > "$T/gh.log"; echo 4 > "$T/pr"; adv dev reuse; ci green; out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] || ko "reuse: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ko "the pass opened a second pull request" || ok "an open dev -> stg pull request is reused"
/usr/bin/grep -q -- "-X PUT repos/x/emu/pulls/4/merge" "$T/gh.log" && ok "the reused pull request is the one merged" || ko "the reused pull request was not merged: $(cat "$T/gh.log")"
[ "$(tip main)" = "$base" ] && ok "the loop left main alone" || ko "the loop moved main"

# #1723: the complete tier is no part of the dev -> stg promotion, so the open mode opens or reuses the pull request and asks for
# nothing: no `promote` label, no request record, no proof, no merge.
adv dev request; req=$(tip dev); stgb=$(tip stg); : > "$T/gh.log"; rm -f "$T/pr" "$SG_STATE/promote-requested"
SG_PROMOTE_OPEN_ONLY=1 SG_PROMOTE_TIP=$req bash "$d/promote-stg.sh" >/dev/null 2>&1
/usr/bin/grep -q -- "-X POST repos/x/emu/pulls" "$T/gh.log" && ok "the open mode opens the dev -> stg pull request" || ko "no pull request opened: $(cat "$T/gh.log")"
/usr/bin/grep -q -- "/labels" "$T/gh.log" && ko "the open mode asked for the complete tier on the dev -> stg pull request: $(cat "$T/gh.log")" \
  || ok "the open mode never labels the dev -> stg pull request (#1723)"
[ -e "$SG_STATE/promote-requested" ] && ko "the open mode recorded a complete-tier request" || ok "the open mode records no complete-tier request"
[ "$(tip stg)" = "$stgb" ] && ok "the open mode proves nothing and merges nothing" || ko "the open mode moved stg"
rm -f "$T/pr"

# A missing CI run (no check runs) reads as running: nothing is promoted.
adv dev one-absent; before=$(tip stg); ci running
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "a missing CI run promotes nothing" || ko "running CI: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *running*) ok "the pending line says the CI is running";; *) ko "the pending line does not say running: '$out'";; esac
[ $rc = 75 ] && ok "a CI that has not reported is the distinct pending exit (#1495)" || ko "running CI exit: $rc"

# (#790) The real CI path (no SG_CI_STATE_CMD): the tip's check runs are read through gh and classified by the base check's own
# classifier. Red, unknown and running promote nothing; green promotes.
unset SG_CI_STATE_CMD SG_PROMOTE_PROOF
RED_P='{"check_runs":[{"id":1,"name":"check (3.11)","status":"completed","conclusion":"failure","details_url":"https://github.com/x/emu/actions/runs/7901/job/1"}]}'
QUIET_P='{"check_runs":[{"id":1,"name":"check (3.11)","status":"completed","conclusion":"failure","details_url":"https://github.com/x/emu/actions/runs/7902/job/1"}]}'
GREEN_P='{"check_runs":[{"id":1,"name":"ruff","status":"completed","conclusion":"success"},{"id":2,"name":"full-xdist","status":"completed","conclusion":"success"},{"id":3,"name":"gui-e2e","status":"completed","conclusion":"success"}]}'
adv dev real-red; before=$(tip stg); printf '%s' "$RED_P" > "$T/checks.json"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"CI is red"*) ok "real CI: a red tip promotes nothing and says red";; *) ko "real CI red: out '$out'";; esac || ko "real CI red: rc $rc stg moved or out '$out'"
# (#1629) A failure check whose jobs all failed before their first step measured nothing: the CI proof reads it as unproven, never
# red, exactly as the base check reads the landing. It promotes nothing, and the refusal names unproven.
adv dev real-quiet; before=$(tip stg); printf '%s' "$QUIET_P" > "$T/checks.json"
printf '{"total_count":1,"jobs":[{"status":"completed","conclusion":"failure","steps":[]}]}' > "$T/jobs-7902.json"   # the API always names the status
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"CI is unproven"*) ok "real CI: a red check whose jobs never started is unproven, not red, and promotes nothing";; *) ko "real CI quiet: out '$out'";; esac || ko "real CI quiet: rc $rc stg moved or out '$out'"
rm -f "$T/jobs-7902.json"
adv dev real-unknown; before=$(tip stg); printf '%s' '{}' > "$T/checks.json"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"CI is unknown"*) ok "real CI: an answer that is not a check-run list promotes nothing";; *) ko "real CI unknown: out '$out'";; esac || ko "real CI unknown: rc $rc"
adv dev real-green; printf '%s' "$GREEN_P" > "$T/checks.json"; rm -f "$T/pr"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "real CI: a green tip is promoted" || ko "real CI green: rc $rc out '$out'"

# #1723: the dev -> stg promotion reads the fast tier alone. The complete tier belongs to the stg -> main pull request, so a tip
# whose fast tier is green and whose complete tier never ran, or was skipped by the event, is promoted.
adv dev complete-absent; checks success none; rm -f "$T/pr"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a green fast tier promotes a tip with no complete-tier run (#1723)" \
  || ko "complete tier absent: rc $rc, stg $(tip stg), out '$out'"
adv dev complete-skipped; checks success skipped; rm -f "$T/pr"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a skipped complete-tier run holds nothing" || ko "complete tier skipped: rc $rc, out '$out'"
checks success success

# (#1532) A cancelled check beside a green one is unproven, never green: the ci proof promotes nothing and names it, and it is no
# wait (a cancelled check never reports again).
adv dev real-canc; before=$(tip stg); printf '%s' '{"check_runs":[{"id":1,"name":"full-serial","status":"completed","conclusion":"cancelled"},{"id":2,"name":"ruff","status":"completed","conclusion":"success"}]}' > "$T/checks.json"; rm -f "$T/pr"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ $rc -ne 75 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"CI is unproven"*) ok "real CI: a cancelled check is unproven and promotes nothing under the ci proof";; *) ko "real CI cancelled: out '$out'";; esac || ko "real CI cancelled: rc $rc, stg $(tip stg), out '$out'"


# The local proof: the promotion gate's own status line, and nothing else.
export SG_PROMOTE_PROOF=local
adv dev two; before=$(tip stg); rm -f "$SG_STATE/gate-status"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: no status line promotes nothing" || ko "local no line: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"no local proof"*) ok "local: the refusal names the missing proof";; *) ko "local: the refusal does not name the missing proof: '$out'";; esac

other=$(tip stg)   # an older commit is not the dev tip
adv dev two-other; before=$(tip stg); line full pass "$other"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a proof for another sha promotes nothing" || ko "local other sha: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"$other"*) ok "local: the refusal names the sha the proof has";; *) ko "local: the refusal does not name the sha it has: '$out'";; esac

adv dev three; before=$(tip stg); line full fail "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a failed proof for the dev tip promotes nothing" || ko "local fail: rc $rc, stg $(tip stg), out '$out'"

adv dev four; before=$(tip stg); line full pass "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "local: a full pass for the dev tip promotes, and stg moves" || ko "local pass: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"

# A later fast gate updates the hub's latest status, but must not replace the full proof for this dev tip.
adv dev full-then-fast; before=$(tip stg)
(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" true)
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "local: a full pass still promotes after a later fast gate" || ko "local full then fast: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"

# stg holds a plain commit dev lacks (a promotion leaves merge commits only, #743): nothing moves, and the owner resolves it.
adv stg stg-only; adv dev dev-only; line full pass "$(tip dev)"; before=$(tip stg)
bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "a stg that holds a plain commit dev lacks is not promoted" || ko "stg moved over work dev lacks"
$W checkout -q -B stg origin/dev; $W push -q -f origin stg   # fixture: realign stg with dev

# The owner command.
unset SG_PROMOTE_PROOF
bash "$d/promote-main.sh" >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "the owner command promotes stg to main" || ko "owner: rc $rc, main $(tip main), stg $(tip stg)"
adv main main-only; before=$(tip main)
out=$(bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac && ok "the owner command refuses when main is not an ancestor of stg" || ko "owner divergence: rc $rc, out '$out'"

# (ADR-0173) promote-main.sh <sha>: the stg-to-main pass moves main to the sha whose complete tier is green, which may be behind
# the stg tip. The sha must be on the first-parent chain of stg, and main must be an ancestor of it.
keep=$(tip stg); git -C "$R" update-ref refs/heads/stg "$(tip main)"   # fixture: stg takes main again
adv stg sha-a; sa=$(tip stg); adv stg sha-b
checks success success   # (#2039) the sha form proves the complete tier before it pushes; test-promote-main-proof.sh owns that rule
out=$(bash "$d/promote-main.sh" "$sa" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$sa" ] && [ "$(tip stg)" != "$sa" ] && ok "promote-main.sh <sha> moves main to that sha, not to the stg tip" || ko "sha: rc $rc, main $(tip main), want $sa, out '$out'"
$W fetch -q origin; $W checkout -q -B side origin/stg; $W commit -q --allow-empty -m side-work; sp=$($W rev-parse HEAD)
$W checkout -q -B stg origin/stg; $W merge -q --no-ff -m "merge side" side; $W push -q origin stg
before=$(tip main); out=$(bash "$d/promote-main.sh" "$sp" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *first-parent*) true;; *) false;; esac \
  && ok "promote-main.sh refuses a sha that is not on the first-parent chain of stg" || ko "off chain: rc $rc, main $(tip main), out '$out'"
out=$(bash "$d/promote-main.sh" "$base" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac \
  && ok "promote-main.sh refuses a sha main is not an ancestor of" || ko "behind main: rc $rc, main $(tip main), out '$out'"
# The merge route never serves main: promote-stg.sh refuses its merge path when the base is the main branch.
: > "$T/gh.log"; before=$(tip main); out=$(SG_STG_BRANCH=main SG_PROMOTE_PROOF=ci bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && ! /usr/bin/grep -q -- "/merge" "$T/gh.log" && case "$out" in *"never by a merge"*) true;; *) false;; esac \
  && ok "promote-stg.sh refuses any merge when the base is main" || ko "merge into main: rc $rc, out '$out', gh '$(cat "$T/gh.log")'"
git -C "$R" update-ref refs/heads/stg "$keep"   # fixture: stg is where the cases below expect it

# (#1896) A promotion whose push cannot authenticate names the credential it could not reach, in one line, and never a bare
# refusal. gh keeps its token in the login keychain, and git's helper for github.com is `gh auth git-credential`, so the
# process that runs the pass with no reachable helper (the CI hook is a launchd session that carries no login keychain)
# gets `fatal: could not read Username ... terminal prompts disabled`. The fake remote below is a loopback HTTP server over
# the local bare repository: its GETs serve the dumb protocol, so the fetch succeeds as the real one does, and the
# receive-pack advertisement and every POST answer 401, so only the push demands a credential. No byte reaches github.com.
fake=https://example.invalid/x/emu.git
printf '[url "file://%s"]\n\tinsteadOf = %s\n' "$R" "$fake" > "$T/fake-url.gitconfig"
printf '[credential]\n\thelper =\n' > "$T/cred-deny.gitconfig"   # the helper answers nothing: the credential is unreachable
printf '[credential]\n\thelper = "!f() { echo username=x; echo password=y; }; f"\n' > "$T/cred-system.gitconfig"
cat > "$T/deny-push-server.py" <<'PY'
# (#1896) The loopback fake remote: GETs serve the bare repository over the dumb protocol, so the fetch succeeds; the
# receive-pack advertisement and every POST answer 401, so the push demands a credential the process must supply.
import http.server, socketserver, sys
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=sys.argv[1], **k)
    def deny(self):
        self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        if "git-receive-pack" in self.path: return self.deny()
        return super().do_GET()
    def do_POST(self): return self.deny()
    def log_message(self, *a): pass
with socketserver.TCPServer(("127.0.0.1", 0), H) as s:
    print(s.server_address[1], flush=True); s.serve_forever()
PY
servers=; stop_servers() { local p; for p in $servers; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done; }
trap stop_servers EXIT   # a killed suite never leaves a loopback server behind
# serve <document root>: a fake remote on a free loopback port; sets $port. The port arrives through a FIFO, so no poll.
serve() { local pf=$T/port.$$; rm -f "$pf"; mkfifo "$pf"
  python3 "$T/deny-push-server.py" "$1" > "$pf" & srv=$!; servers="$servers $srv"
  port=; read -r -t 20 port < "$pf"; rm -f "$pf"; }
mkeep=$(tip main)
git -C "$R" update-ref refs/heads/stg "$(tip main)"; adv stg cred-ahead; before=$(tip main)
# A helper this process can reach only through a config git itself ignores (GIT_CONFIG_NOSYSTEM) must never stop a
# promotion git can make: the script proves nothing about a credential before it uses one. The fake remote rewrites to
# the local bare repository, so git needs no credential and the move must happen.
out=$(SG_PROMOTE_REMOTE=$fake GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=$T/cred-system.gitconfig \
  GIT_CONFIG_GLOBAL=$T/fake-url.gitconfig bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "a credential git never needs is never demanded before the push" \
  || ko "a credential git never needs was demanded: rc $rc, main $(tip main), stg $(tip stg), out '$out'"
git -C "$R" update-ref refs/heads/main "$mkeep"; before=$(tip main)
if command -v python3 >/dev/null 2>&1; then
  git -C "$R" update-server-info
  serve "$T"
  out=$(SG_PROMOTE_REMOTE=http://127.0.0.1:$port/remote.git GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=$T/cred-deny.gitconfig \
    bash "$d/promote-main.sh" 2>&1); rc=$?
  stop_servers; servers=
  [ $rc = 3 ] && [ "$(tip main)" = "$before" ] && ok "a push that cannot authenticate exits 3 and moves nothing" \
    || ko "no credential: rc $rc, main $(tip main), out '$out'"
  case "$out" in *"cannot reach"*"credential"*) ok "the refusal names the credential it could not reach";; *) ko "no credential named: '$out'";; esac
  [ "$(printf '%s\n' "$out" | /usr/bin/grep -c .)" = 1 ] && ok "the refusal is one line" || ko "the refusal is $(printf '%s\n' "$out" | /usr/bin/grep -c .) lines: '$out'"
  case "$out" in *"refused"*) ko "the refusal is a bare 'refused': '$out'";; *) ok "the refusal is never a bare 'refused'";; esac
  case "$out" in *"127.0.0.1:$port"*) ok "the line names the remote whose credential was not reachable";; *) ko "the line names no remote: '$out'";; esac
else
  echo "SKIP #1896: python3 is not on PATH, so the loopback remote of the credential test cannot run"
fi
# The other half of the acceptance: a fake remote that does not deny the promotion takes it, and the ref moves.
out=$(SG_PROMOTE_REMOTE=$fake GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=$T/fake-url.gitconfig bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "a fake remote that takes the push moves main" \
  || ko "accepted: rc $rc, main $(tip main), stg $(tip stg), out '$out'"
git -C "$R" update-ref refs/heads/main "$mkeep"; git -C "$R" update-ref refs/heads/stg "$keep"   # fixture: both branches back

# No loop script pushes to main but the stg-to-main pass, through promote-main.sh: only promote-main.sh names the push, and a
# test fixture is no loop script.
bad=$(cd "$d" && grep -lE 'push[^#]*(refs/heads/(main|\$main)|origin main|HEAD:main)' *.sh *.py 2>/dev/null | grep -v -e '^test-' -e '^promote-main.sh$')
[ -z "$bad" ] && ok "no loop script pushes to main" || ko "pushes to main: $bad"
/usr/bin/grep -q 'refs/heads/\$main' "$d/promote-main.sh" && ok "promote-main.sh is the one script that pushes the main branch" || ko "promote-main.sh does not push the named main branch"
# The owner's consolidation rule (#743): stg moves by a pull request merge, never by a push from a loop script.
bad=$(cd "$d" && grep -lE '^[^#]*push[^#]*(refs/heads/stg|origin stg|HEAD:stg)' *.sh 2>/dev/null | grep -v -e '^test-')
[ -z "$bad" ] && ok "no loop script pushes stg: the consolidation is a pull request" || ko "pushes to stg: $bad"

# (#392) The promotion reads the machine-wide gate lock: with a holder it waits, says so on one line, then promotes.
export SG_PROMOTE_PROOF=local; adv dev lock; line full pass "$(tip dev)"; rm -f "$T/gl"
(bash "$d/serial.sh" gate sh -c "sleep 5; echo holder >> $T/gl") & for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
out=$(bash "$d/promote-stg.sh" 2>&1); wait
[ "$(printf '%s\n' "$out" | grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "the promotion waits for the holder, on one line" || ko "promotion wait lines: '$out'"
[ "$(tip stg)" = "$(tip dev)" ] && ok "the waiting promotion is not dropped: stg is promoted" || ko "the waiting promotion did not promote"
# #1723 (plan item): the dev-to-stg merge names the proven head sha, so a tip that moves under the proof is never merged.
adv dev mg; tmg=$(tip dev); rm -f "$T/pr"; : > "$T/gh.log"; line full pass "$tmg"; export SG_PROMOTE_PROOF=local
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
/usr/bin/grep -q -- "-X PUT repos/x/emu/pulls/7/merge.* sha=$tmg" "$T/gh.log" && ok "the merge call carries the proven head sha" \
  || ko "merge without the sha: rc $rc, out '$out', gh '$(/usr/bin/grep merge "$T/gh.log")'"
# A 409 on the merge means the head moved. The pull request now names another head, so the pass exits 75 and the next pass reads it.
mkdir -p "$T/bin409"; export GH_REAL=$T/bin/gh
cat > "$T/bin409/gh" <<'EOF'
#!/bin/bash
case "$*" in
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) echo '{"message":"Head branch was modified. Review and try the merge again.","documentation_url":"https://docs.github.com"}'; exit 1;;
  *"pulls?state=open"*) echo '[{"number":7,"head":{"ref":"dev","sha":"1111111111111111111111111111111111111111"},"base":{"ref":"stg"}}]';;
  *"repos/x/emu/pulls/7"*) echo '{"number":7,"head":{"sha":"1111111111111111111111111111111111111111"}}';;
  *) exec "$GH_REAL" "$@";;
esac
EOF
chmod +x "$T/bin409/gh"
adv dev mv; tmv=$(tip dev); before=$(tip stg); rm -f "$T/pr"; line full pass "$tmv"
out=$(PATH="$T/bin409:$PATH" bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 75 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"pending $tmv"*) true;; *) false;; esac \
  && ok "a 409 on the merge (the head moved) exits 75 and moves nothing" || ko "409 merge: rc $rc, stg $(tip stg) was $before, out '$out'"
# The fast proof word (#1723 plan item): a `fast` record for the exact tip promotes it; any other tip or word never does.
adv dev fp; tfp=$(tip dev); rm -f "$T/pr"; printf '%s\tfast\tpass\t%s\n' "$(date +%s)" "$tfp" > "$SG_STATE/gate-status-promote"
out=$(SG_PROMOTE_PROOF=fast bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$tfp" ] && ok "the fast proof word promotes the tip it names" || ko "fast proof: rc $rc, stg $(tip stg), out '$out'"
adv dev fq; tfq=$(tip dev); before=$(tip stg); rm -f "$T/pr"; printf '%s\tfast\tpass\t%s\n' "$(date +%s)" "$before" > "$SG_STATE/gate-status-promote"
out=$(SG_PROMOTE_PROOF=fast bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 75 ] && [ "$(tip stg)" = "$before" ] && ok "a fast record for another tip waits and moves nothing" || ko "fast record for another tip: rc $rc, out '$out'"
adv dev fw; tfw=$(tip dev); before=$(tip stg); rm -f "$T/pr"; printf '%s\tfull\tpass\t%s\n' "$(date +%s)" "$tfw" > "$SG_STATE/gate-status-promote"
out=$(SG_PROMOTE_PROOF=fast bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 1 ] && [ "$(tip stg)" = "$before" ] && case "$out" in *"not the fast proof"*) true;; *) false;; esac \
  && ok "a full word in the fast record never promotes" || ko "full word: rc $rc, out '$out'"
# (#2061) A fast or ci proof runs no local suite, so the promotion takes no gate lock: it runs while another holder keeps it.
adv dev nl; tnl=$(tip dev); rm -f "$T/pr"; printf '%s\tfast\tpass\t%s\n' "$(date +%s)" "$tnl" > "$SG_STATE/gate-status-promote"
(bash "$d/serial.sh" gate sh -c "sleep 4") & hp=$!; for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
s0=$(date +%s); out=$(SG_PROMOTE_PROOF=fast bash "$d/promote-stg.sh" 2>&1); rc=$?; s1=$(date +%s); wait $hp
[ $rc = 0 ] && [ "$(tip stg)" = "$tnl" ] && ! printf '%s' "$out" | grep -q 'waiting for lock gate' && [ $((s1 - s0)) -lt 4 ] \
  && ok "a fast-proof promotion runs while another holder keeps the gate lock" || ko "fast proof under a held gate lock: rc $rc, $((s1 - s0)) s, out '$out'"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
