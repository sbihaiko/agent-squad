#!/bin/bash
# The shared fixture of the split wave suites (#2244): the header, the bare local remote, the stub gh and board, the
# state dir, the child and review helpers, and the two cases that prove the remote and its push hook. A part suite
# sources this file and holds its own cases; `# mirrors: wave.sh wave-fixture.sh` makes a change to either run every part.
# Regression test for wave.sh: the run's wave branch and the child that enters it by a fast-forward (#374), and the wave's
# landing pass: the rebase on the moved base, the one push, the push that repeats from the fetch, and the closes (#375).
# A child enters only after its review approved its own diff against the wave branch (#376): a review that asked for changes,
# one read against another base or an older tip, and no review at all each hold the child out, with nothing requeued.
# A bare local remote, a stub gh, a stub board, a temp state dir: every case runs on this machine, with no GitHub call at all.
# A pre-receive hook on the bare remote logs every ref a push writes, so one push and "no child branch" are read, not assumed.
# usage: sourced by each test-wave-<part>.sh; the part resolves the scripts dir and runs the shared setup
unset REV REV_BASE REV_SHA   # each case decides the review; an ambient one from a live loop must not
# The machine-wide gate this suite is often run under exports its own settings into every command it starts (gate.sh, serial.sh;
# #392, #393): the promotion gate runs the whole battery inside the gate lock. This suite takes its own lock and writes its own
# status line, so none of them may reach it -- an inherited SG_SERIAL_HELD re-enters the lock (case I is never proved), an
# inherited SG_GATE_STATUS=self stops the status line being written (case B reads a file that is not there), and an inherited
# SG_GATE_KIND or SERIAL_* decides a lock and a deadline the suite did not take. gate.sh exports SERIAL_WAIT_SECONDS and
# SERIAL_HOLD_SECONDS; serial.sh exports SG_SERIAL_HELD and reads SERIAL_POLL_SECONDS; a gate caller sets SG_GATE_KIND and
# SG_GATE_STATUS. All of them go, beside REV.
unset SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
# The loop's own profile reaches a child session, and env.sh below reads its wave settings with `:=`, so a name that is
# already set wins over the case's default: an inherited SG_WAVE_ENTRY_GATE=off enters every child ungated (case B reads a
# gate-status that is never written, case C's failing gates are skipped) and an inherited SG_WAVE_TIP_GATES decides a tip
# check the case asked for. Each case names its own, so neither ambient value may reach it.
unset SG_WAVE_ENTRY_GATE SG_WAVE_TIP_GATES SG_WAVE_ENTRY_WAIT_SECONDS
# gate.sh exports GATE_REUSE_FILE to the command it runs (its --run), so this suite run under a gate inherits it: a memo hit of
# an entry then marks the outer gate's row instead of writing its own reuse row, and the row this suite reads is missing (#1798).
unset GATE_REUSE_FILE
# The clones and the wave's rebase replay commits, and a rebase needs a committer. A runner has none set and cannot
# auto-detect one, so the suite names its own identity and never reads the machine's (#1062).
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
# The scripts a case runs: one copy of this checkout's scripts, by a list.
for f in wave.sh issue_ledger.py ghread.py wavepause.py child_issues.py address.sh caps.sh serial.sh  \
  ghcall.sh drainpolicy.py base-battery.sh basecheck.sh fast-gate.sh quality-reading.py siblings.py  \
  cluster.py autohunt.sh revformat.py gate.sh columnlimit.sh issue_state.py; do cp "$d/$f" "$T/s/"; done 2>/dev/null
# The board read of the wave's Testing bound (#2439) is the shipped one: the real issue_state.py beside the scripts, over the
# hub cache the loop writes (SG_BOARD_CACHE). A case writes that cache in the hub's own shape (rows), so the wave's seam is the
# one the loop runs, never a stub of it, and a case that writes none reads a board that carries no card.
printf '{"t": %s, "repo":"x/y","items":[]}\n' "$(date +%s)" > "$T/st/hub-items.json"
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
# A capped run here is an ended run: the retirement an age-cap requeue waits for (#1970) is test-wave-retire.sh's, and here
# a stub proves the end at once, so no process table is read and no process is signalled.
export SG_RETIRE_CMD=/usr/bin/true
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf ': "${SG_LAND_REMOTE:=%s/origin.git}"; export SG_LAND_REMOTE\n' "$T"   # a case may set its own remote (K3: one that fails on the network)
  printf ': "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n'   # the tip's check (#377): `true` unless a case replaces it
  printf ': "${SG_WAVE_ENTRY_GATE:=on}"; export SG_WAVE_ENTRY_GATE\n'   # the entry cases prove the entry gate, so it is on here (#1802); Z10 names its own
  printf 'export SG_BOARD_VOCAB=shipped\n'   # the vocabulary every wave profile runs on (#468): its columns are the ones the landing writes
  printf 'export SG_BOARD_CACHE=%s/st/hub-items.json\n' "$T"   # the board read a case writes (rows): the hub cache issue_state.py reads (#2439)
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
# The seam notes each items read on stderr (issue_state.py); the stub does too, so a write that worked is never read as refused (#526).
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "${SG_MOVE_REASON:-none} $*" >> "%s/board-reason.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"; W=wave/run-1
# Every ref a push writes is logged by the remote itself (<old> <new> <ref>, one line per ref): the landing pass pushes
# exactly one ref, and no child branch ever reaches the remote (#375).
mkdir -p "$T/origin.git/hooks"
printf '#!/bin/bash\nwhile read -r old new ref; do printf "%%s\\t%%s\\t%%s\\n" "$old" "$new" "$ref" >> "%s/pushes.log"; done\n' "$T" > "$T/origin.git/hooks/pre-receive"
chmod +x "$T/origin.git/hooks/pre-receive"
pushes() { awk -F'\t' -v r="${1:-}" '$3==r || r=="" {print $1, $2, $3}' "$T/pushes.log" 2>/dev/null; }
# The stub gh: every argv is logged, and a close answers GitHub's own shape. SWEEPS resets the log between cases.
# RED_BASE=1 answers a red completed check on every check-runs read (the base's own check, #786); otherwise the read is not a check-run list.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nfor a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "%s/gh-bodies.log";; esac; done\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *"/actions/runs/"*"/jobs"*) echo "{\\"total_count\\":1,\\"jobs\\":[{\\"conclusion\\":\\"failure\\",\\"steps\\":[{\\"name\\":\\"run\\"}]}]}";; *"/check-runs"*) if [ -n "${RED_BASE:-}" ]; then echo "{\\"check_runs\\":[{\\"id\\":1,\\"name\\":\\"check (3.11)\\",\\"status\\":\\"completed\\",\\"conclusion\\":\\"failure\\",\\"details_url\\":\\"https://github.com/x/y/actions/runs/7801/job/1\\"}]}"; else echo "{}"; fi;; *) echo "{}";; esac\n' "$T" "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The remote is a bare repository on this machine: the whole suite runs against it, never against GitHub.
[ "$($O rev-parse --is-bare-repository)" = true ] && ok "the suite runs against a bare local remote" || ko "the remote is not a bare repository"
[ -x "$T/origin.git/hooks/pre-receive" ] && ok "the remote logs every pushed ref" || ko "the remote has no push hook"
start() { (cd "$T/co" && bash "$T/s/wave.sh" start run-1); }
# A child worktree: a clone cut from the wave branch, on branch gui/<issue>, one commit that writes <file>. A machine under
# load can fail a clone or a checkout, and a test that swallows it reads a worktree that is not there for every case behind it
# (#377): the failure is named here, at the child it belongs to, never discarded. No quiet machine is assumed.
child() { local n=$1 f=$2 tip=${3:-origin/$W} o
  rm -rf "$T/c$n"
  o=$($G clone -q "$T/origin.git" "$T/c$n" 2>&1) || { ko "child #$n: the clone of the wave branch failed: $o"; return 1; }
  o=$((cd "$T/c$n" && $G checkout -q -b "gui/$n" "$tip" && echo "$n" > "$f" && $G add "$f" && $G -c user.email=a@b -c user.name=t commit -q -m "work $n (Refs #$n)") 2>&1) \
    || { ko "child #$n: the worktree on $tip is not ready: $o"; return 1; }; }
# The review of the child's branch, read against the wave branch (#376): branchreview.sh keeps the verdict (review-<key>.md),
# the sha it read (review-<key>.sha) and the base it was taken against (review-<key>.base). REV, REV_BASE and REV_SHA let one
# case hand in a verdict that asked for changes, a review read against another base, or a review of an older tip.
rev() { local n=$1 sha; sha=$($G -C "$T/c$n" rev-parse HEAD); rm -f "$T/st/review-gui_$n".*
  [ "${REV:-APPROVE}" = none ] && return 0
  printf 'VERDICT: %s\n' "${REV:-APPROVE}" > "$T/st/review-gui_$n.md"
  printf '%s\n' "${REV_SHA-$sha}" > "$T/st/review-gui_$n.sha"; printf '%s\n' "${REV_BASE-$W}" > "$T/st/review-gui_$n.base"; }
# enter runs where the child works. GATES is the local gate command the profile sets (SG_WAVE_GATES).
en() { local n=$1; rev "$n"; (cd "$T/c$n" && SG_WAVE_GATES=${GATES-true} bash "$T/s/wave.sh" enter run-1 "$n"); }
# wfact <issue> <run>: the wave fact the issue's ledger holds for the run, "<state> <cause>" (ADR-0149: wave events write it).
wfact() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#$1" wave 2>/dev/null | python3 -c 'import json,sys; r=json.load(sys.stdin).get(sys.argv[1]) or {}; print(r.get("state",""), r.get("cause",""))' "$2" 2>/dev/null; }
tries() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#$1" wave 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get(sys.argv[1]) or {}).get("tries",""))' "$2" 2>/dev/null; }
# obl_for <tree sha>: the obligation record the gate wrote for that tree, "" when there is none (spec 1163).
obl_for() { local f; for f in "$T/st/obligations"/*; do [ -f "$f" ] || continue; [ "$(cut -f2 "$f")" = "$1" ] && { printf '%s\n' "$f"; return 0; }; done; return 1; }
entered() { $O merge-base --is-ancestor "$($G -C "$T/c$1" rev-parse HEAD)" $W 2>/dev/null && echo yes || echo no; }
# The wave's landing pass runs beside the run, never in the owner's checkout (it works in a throwaway clone).
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$@"); }
# One commit lands on dev while the wave is open: the base a wave lands on has moved under it.
devmoved() { rm -rf "$T/w2"; $G clone -q "$T/origin.git" "$T/w2" 2>/dev/null
  (cd "$T/w2" && $G checkout -q dev && echo other > other && $G add other && $G -c user.email=a@b -c user.name=t commit -q -m "other work" && $G push -q origin HEAD:dev); }


# The shared base battery a tip-check case builds its command from (the R case proves it; the tip-check cases read it).
. "$d/base-battery.sh" 2>/dev/null

# The gh stub that keeps the labels and comments a case writes when LABEL_STORE=1 (#2217), and refuses a label write for
# LABEL_REFUSE's issue. It replaces the plain stub above; every part starts with it.
gh_store_stub() {
cat > "$T/bin/gh.new" <<'GHEOF'
#!/bin/bash
T=__T__
echo "$*" >> "$T/gh.log"
n=""; prev=""
for a in "$@"; do
  case "$a" in body=@*) cat "${a#body=@}" >> "$T/gh-bodies.log";; esac
  case "$a" in repos/*/issues/[0-9]*) n=$(printf '%s' "$a" | /usr/bin/sed -n 's#.*/issues/\([0-9]*\).*#\1#p');; esac
  if [ "${LABEL_STORE:-}" = 1 ] && { [ "$prev" = -f ] || [ "$prev" = --raw-field ]; }; then
    case "$a" in labels[]=*) printf '%s\n' "${a#labels[]=}" >> "$T/lab-$n";; esac
  fi
  prev=$a
done
if [ -n "${LABEL_REFUSE:-}" ] && printf '%s' "$*" | /usr/bin/grep -q "issues/$LABEL_REFUSE/labels"; then
  echo '{"message":"the label write was refused","status":500}'; exit 1
fi
case "$*" in
  *"issues/"*)
    case "$*" in
      *"/comments"*)
        if [ "${LABEL_STORE:-}" = 1 ] && [ -n "$n" ] && [ -f "$T/com-$n" ]; then
          jq -R -s 'split("\n") | map(select(length > 0)) | map({body: .})' "$T/com-$n"
        else echo '{"number":1,"state":"closed"}'; fi;;
      *"/labels"*)
        if [ "${LABEL_STORE:-}" = 1 ] && [ -n "$n" ] && [ -f "$T/lab-$n" ]; then
          printf '['
          awk 'NR > 1 { printf "," } { printf "{\"name\":\"%s\"}", $0 }' "$T/lab-$n"
          printf ']\n'
        else echo '[]'; fi;;
      *)
        echo '{"number":1,"state":"closed"}';;
    esac;;
  *) echo "{}";;
esac
GHEOF
/usr/bin/sed "s|__T__|$T|" "$T/bin/gh.new" > "$T/bin/gh"; chmod +x "$T/bin/gh"
}

# Helpers a part calls but another part defines: one copy here, so every part holds them.
start2() { local o; o=$((cd "$T/co" && bash "$T/s/wave.sh" start "$1") 2>&1) || { ko "start2 $1: the wave branch was not cut: $o"; return 1; }; }


en2() { local r=$1 n=$2 o; REV_BASE=wave/$r rev "$n"; o=$((cd "$T/c$n" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter "$r" "$n") 2>&1) \
  || { ko "en2 $r #$n: the child did not enter its wave: $o"; return 1; }; printf '%s\n' "$o"; }

land2() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1"); }


flaky() { printf '%s\n' "n=\$(( \$(/usr/bin/grep -c . '$1' 2>/dev/null || echo 0) + 1 )); echo call >> '$1'; [ \$n -le $2 ] || exit 0; echo 'FAILED tests/test_flaky.py::test_flaky - assert 0'; exit 1"; }
calls() { /usr/bin/grep -c . "$1" 2>/dev/null || echo 0; }

posts() { /usr/bin/grep -cE '(^| )-X POST ' "$T/gh.log" 2>/dev/null || echo 0; }
freezes() { /usr/bin/grep -c "api -X POST repos/x/y/issues/$1/labels" "$T/gh.log" 2>/dev/null || echo 0; }
