#!/bin/bash
# Rule Q: a card In Progress behind a dispatch marker, with no run working the issue, is released -- however the run ended.
# Rules E and O read a run's verdict. A run that ends with no verdict or that left
# active-runs by any other road never reaches either rule, so its card stays In Progress behind its auto/disp-<n> marker,
# autofrontier.sh skips the issue, and health.sh reports runs-board-drift for hours.
#  (A) a zombie run (no verdict, idle, no process) that C reaps this pass: card -> Todo, marker dropped, comment queued;
#  (B) a marker whose run is in no list at all: same;
#  (C) a bug marker: the loop card goes to Todo and the bug card to To triage;
#  (D) a live run works the issue: card and marker stay;
#  (E) a run in its launch window (no record.jsonl yet) names the issue: card and marker stay;
#  (F) a work branch names the issue (#382): card and marker stay;
#  (G) a marker younger than the grace stays;
#  (H) a card another step moved past In Progress stays where it is;
#  (I) a run that landed work and ended is reconciled to Code Complete (#239: a local landing is not Done);
#  (J) a GitHub refusal is not an answer: nothing is touched, and the next pass (the API back) releases the card;
#  (K) #771: a failed run left active-runs before rule O released it, and rule R put its card back in Todo: the marker is
#      dropped with no board write, so the frontier dispatches the issue again instead of reading it as claimed for good.
#  (L) #794: a dangling marker (its file gone) is no live marker: the pass never stats or reads it, and its card is released.
#  (N) #794: unheld reads a marker once; the hook counts the hit, UNHELD #1014 is printed and the marker dropped;
#  (N2) #794: a marker removed at unheld's first read is no marker: the pass reads it as none, no UNHELD line;
#  (M) #794, #798: a marker removed mid-pass in rule K, and the rule after Q still prints;
#  (P) #798, #799: a fresh seen record removed at rule Q's first stat reads as none, and rule Q releases its card;
# Stubbed gh/board; a temp state dir, so the live watch.log, markers and board are never read or written.
# usage: bash test-janitor-stale-card.sh [janitor.py]
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
export SG_TAKEOVER_SECONDS=1000000000000   # these runs test other rules; their fixed record stamps are past the takeover bound (ADR-0166)
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/board_facts.py" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) printf '{\n  "message": "Not Found"\n}\n'; exit 1;; esac
    case "$a" in *"Refs #1006"*|*"Refs #1011"*) echo 1;; *) echo 0;; esac;;
  # a work branch names the issue (#382: the janitor reads branches, never pull requests): #1006 and #1011 each have one
  *repos/x/y/branches*) printf 'fix/1006-x sha1006\nfix/1011-x sha1011\n';;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) { for n in 1007; do printf '%s\tCode Complete\n' "$n"; done; printf '1012\tTodo\n'; for n in 1001 1002 1003 1004 1005 1006 1008 1009 1010 1011 1013; do printf '%s\tIn Progress\n' "$n"; done; awk '/^o 4 /{v=substr($0,5); n=v; sub(/.* /,"",n); s=v; sub(/ [0-9]+$/,"",s); print n "\t" s}' "TMPDIR/board.calls" 2>/dev/null; } | awk -F'\t' '{m[$1]=$2} END{for (k in m) printf "%s\t%s\n", k, m[k]}';;   # one read, every card; the janitor's own status writes are read back
  *projectsV2/1/items*) for n in 1003; do printf '%s\tDoing\n' "$n"; done;;
  *issues/1010*) if [ -e "TMPDIR/refuse" ]; then echo "API rate limit exceeded for user"; else echo open; fi;;
  *issues/10*) echo open;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"; }
old() { touch -t 202001010000 "$@"; }
mark() { echo "$2" > "$T/st/auto/$1"; [ "${3:-old}" = old ] && old "$T/st/auto/$1"; }
# A: a zombie run: no verdict, record idle for hours, no process inside its folder
mk run-zombie 1001; printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #1001 on fix/1001-x"}\n' > "$T/co/runs/run-zombie/record.jsonl"; old "$T/co/runs/run-zombie/record.jsonl"; mark disp-1001 run-zombie
# B: a marker whose run is in no list (dropped from active-runs earlier)
mark disp-1002 run-gone
# C: a bug marker with both cards in the dispatch's column
mark bug-1003 run-gone
# D: a live run works the issue (record moving now)
mk run-live 1004; printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #1004 on fix/1004-x"}\n' > "$T/co/runs/run-live/record.jsonl"; mark disp-1004 run-live
# E: a run in its launch window: request.txt is fresh, no record.jsonl yet
mk run-young 1005; mark disp-1005 run-young
# F: a work branch names the issue
mark disp-1006 run-gone
# G: the marker is fresh
mark disp-1008 run-gone fresh
# H: the card moved past In Progress
mark disp-1007 run-gone
# I: a run that landed and ended
mk run-landed 1009; printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #1009 on fix/1009-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "a7df8f1ecff77bdb5833721121a7a50be4066053"}}\n{"event": "verdict", "verdict": "pass", "reasons": [], "exit_code": 0}\n' > "$T/co/runs/run-landed/record.jsonl"; old "$T/co/runs/run-landed/record.jsonl"; mark disp-1009 run-landed
# K: a failed run that left active-runs unreleased; rule R put its card in Todo
mark disp-1012 run-gone
# J: the API refuses the first pass
mark disp-1010 run-gone; touch "$T/refuse"
# L: a dangling marker (its target is gone): rule Q's stat raises unless the pass treats it as no live marker (#794)
ln -s "$T/st/auto/gone" "$T/st/auto/disp-1013"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }
old "$T/co/runs/run-zombie" "$T/co/runs/run-landed"; touch -t 202001010000 "$T/co/runs/run-zombie/request.txt" "$T/co/runs/run-landed/request.txt"
o=$(pass); bc() { cat "$T/board.calls" 2>/dev/null; }
/usr/bin/grep -q '^o 4 Todo 1001$' "$T/board.calls" 2>/dev/null && ok "A: a zombie run's card goes back to Todo" || ko "A #1001 not released: $(bc) $o"
[ ! -e "$T/st/auto/disp-1001" ] && ok "A: its marker is dropped" || ko "A disp-1001 kept: $o"
/usr/bin/grep -q 'STALE-CARD #1001' "$T/st/cycle.log" 2>/dev/null && ok "A: reported" || ko "A cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'In Progress' "$T/st/cq/"*1001* 2>/dev/null && ok "A: a comment is queued on the issue" || ko "A comment: $(ls "$T/st/cq")"
/usr/bin/grep -q '^o 4 Todo 1002$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/disp-1002" ] && ok "B: a marker with no run anywhere is released" || ko "B #1002: $(bc)"
/usr/bin/grep -q '^o 4 Todo 1003$' "$T/board.calls" 2>/dev/null && /usr/bin/grep -q '^o 1 To triage 1003$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/bug-1003" ] && ok "C: a bug marker releases the loop card and the bug card" || ko "C #1003: $(bc)"
[ -e "$T/st/auto/disp-1004" ] && ! /usr/bin/grep -q ' 1004$' "$T/board.calls" 2>/dev/null && ok "D: a live run keeps its card and marker" || ko "D #1004 touched: $(bc)"
[ -e "$T/st/auto/disp-1005" ] && ! /usr/bin/grep -q ' 1005$' "$T/board.calls" 2>/dev/null && ok "E: a run in its launch window keeps its card and marker" || ko "E #1005 touched: $(bc)"
[ -e "$T/st/auto/disp-1006" ] && ! /usr/bin/grep -q ' 1006$' "$T/board.calls" 2>/dev/null && ok "F: an issue a work branch names keeps its card and marker" || ko "F #1006 touched: $(bc)"
[ -e "$T/st/auto/disp-1008" ] && ! /usr/bin/grep -q ' 1008$' "$T/board.calls" 2>/dev/null && ok "G: a fresh marker stays" || ko "G #1008 touched: $(bc)"
! /usr/bin/grep -q ' 1007$' "$T/board.calls" 2>/dev/null && ok "H: a card past In Progress is never downgraded" || ko "H #1007 moved: $(bc)"
/usr/bin/grep -q '^o 4 Code Complete 1009$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Done 1009' "$T/board.calls" 2>/dev/null && ok "I: a run that landed is reconciled to Code Complete, never Done" || ko "I #1009 not reconciled to Code Complete: $(bc)"
/usr/bin/grep -q '^o 4 Landing 1009$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Done 1009' "$T/board.calls" 2>/dev/null && ok "I: a run that landed is reconciled to Landing, never Done" || ko "I #1009 not reconciled to Landing: $(bc)"
[ -e "$T/st/auto/disp-1010" ] && ! /usr/bin/grep -q ' 1010$' "$T/board.calls" 2>/dev/null && ok "J: a refused read touches nothing" || ko "J #1010 touched on a refusal: $(bc)"
[ ! -e "$T/st/auto/disp-1012" ] && ! /usr/bin/grep -q ' 1012$' "$T/board.calls" 2>/dev/null && ok "K: a Todo card behind a marker no run holds is released, the card untouched (#771)" || ko "K #1012 stays claimed: $(bc) $o"
[ ! -L "$T/st/auto/disp-1013" ] && /usr/bin/grep -q '^o 4 Todo 1013$' "$T/board.calls" 2>/dev/null && ! grep -q Traceback <<< "$o" && ok "L: a dangling marker is dropped, its card released, the pass reaches the card pass (#794)" || ko "L #1013: $(bc) $o"
rm -f "$T/refuse"; o=$(pass)
/usr/bin/grep -q '^o 4 Todo 1010$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/disp-1010" ] && ok "J: the next pass, API back, releases it (a refusal leaves no once-only marker behind)" || ko "J #1010 not released after the refusal: $(bc) $o"
n=$(/usr/bin/grep -c '^o 4 .* 1001$' "$T/board.calls"); [ "$n" = 1 ] && ok "the card of #1001 is moved once" || ko "#1001 moved $n times"
# M: a marker removed mid-pass (#794), in a second state dir so the cases above stay as they were. #798: the race fires in
# rule K's age read (the first stat of disp-1021), not in rule Q, so these lines pin rule K.
# The regression is the race on disp-1021: the disp-/bug- glob lists it, then it is removed before its first stat (rule K), so the
# stat must read it as no marker. race.py removes it at that first stat, and the fork pass after it must still print.
# DISPATCH-MISSING #1020 is context only: rule O drops disp-1020 (its run folder is missing) before the race.
S2="$T/st2"; C2="$T/co2"; mkdir -p "$S2/auto" "$S2/cq" "$C2/runs" "$T/bin2" "$T/fork"
cat > "$T/bin2/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*) echo 0;;
  *projectsV2/4/items*) printf '1020\tIn Progress\n1021\tIn Progress\n';;
  *issues/10*) echo open;;
esac
J
chmod +x "$T/bin2/gh"
cat > "$T/race.py" <<'J'
# runs janitor.py; the marker named by RACE_MARKER is removed at its first os.stat, after the glob listed it
import os, runpy, sys
orig = os.stat
def racing(p, *a, **k):
    if os.path.basename(os.fspath(p)) == os.environ.get("RACE_MARKER"):
        with open(os.environ["RACE_LOG"], "a") as log: log.write("hit\n")
        if os.path.lexists(p):
            os.unlink(p)
            with open(os.environ["RACE_LOG"], "a") as log: log.write("removed\n")
    return orig(p, *a, **k)
os.stat = racing
sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); runpy.run_path(sys.argv[0], run_name="__main__")
J
echo run-missing > "$S2/active-runs.txt"; echo run-missing > "$S2/auto/disp-1020"; echo run-gone > "$S2/auto/bug-1020"; echo run-gone > "$S2/auto/disp-1021"
git -C "$T/fork" init -q && GIT_AUTHOR_DATE=2020-01-01T00:00:00Z GIT_COMMITTER_DATE=2020-01-01T00:00:00Z git -C "$T/fork" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "fix: an old change (#1030)"
o2=$(RACE_LOG="$T/race-m.log" RACE_MARKER=disp-1021 PATH="$T/bin2:$PATH" SG_STATE="$S2" SG_CHECKOUT="$C2" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$S2" SG_FORK_DIR="$T/fork" SG_FORK_BASE=HEAD JANITOR_NOTIFY="$T/notify" python3 -I "$T/race.py" "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^removed$' "$T/race-m.log" 2>/dev/null && ok "M: the race removed disp-1021 at its stat ($(/usr/bin/grep -c '^hit' "$T/race-m.log") hits)" || ko "M: the removal never fired: $(cat "$T/race-m.log" 2>/dev/null)"
! grep -q Traceback <<< "$o2" && ok "M: rule O and rule K meet one pass, a marker removed mid-pass: no Traceback" || ko "M traceback: $o2"
/usr/bin/grep -q 'DISPATCH-MISSING #1020' "$S2/cycle.log" 2>/dev/null && [ ! -e "$S2/auto/disp-1020" ] && ok "M: rule O drops the marker of the missing run" || ko "M: rule O did not run: $(cat "$S2/cycle.log" 2>/dev/null)"
[ ! -e "$S2/auto/disp-1021" ] && ok "M: rule K: the race removed disp-1021 at its stat, and the pass read it as no marker" || ko "M: disp-1021 kept"
/usr/bin/grep -q 'FIXED-NOT-CLOSED #1030' "$S2/cycle.log" 2>/dev/null && ok "M: the rule after Q still prints" || ko "M: no output after Q: $(cat "$S2/cycle.log" 2>/dev/null)"
# N: #794, #798, #799: unheld() reads the marker once, through marker_mtime. The hook counts that one read (RACE_LOG hit lines);
# the marker stands at the read, UNHELD #1014 is printed, the marker is dropped, and the pass goes on. N2 and P own the removals.
S3="$T/st3"; C3="$T/co3"; mkdir -p "$S3/auto" "$S3/cq" "$C3/runs/run-unheld"
echo run-unheld > "$S3/active-runs.txt"; echo "CHILD — GitHub issue(s) 1014 (one PR):" > "$C3/runs/run-unheld/request.txt"
printf '{"event": "spawn_accepted", "ts": 1700000000, "child": "c1", "definition": "GitHub issue #1040 on fix/1040-x"}\n{"event": "node_done", "ts": 1700000001, "node": "route.router.1"}\n' > "$C3/runs/run-unheld/record.jsonl"
echo run-unheld > "$S3/auto/disp-1014"; touch -t 202001010000 "$S3/auto/disp-1014"
cat > "$T/race_unheld.py" <<'J'
# runs janitor.py; inside unheld(), each stat of the marker named by RACE_MARKER is a hit, logged to RACE_LOG
import os, runpy, sys
orig = os.stat
def in_unheld():
    f = sys._getframe(1)
    while f is not None:
        if f.f_code.co_name == "unheld": return True
        f = f.f_back
    return False
def racing(p, *a, **k):
    if os.path.basename(os.fspath(p)) == os.environ.get("RACE_MARKER") and in_unheld():
        with open(os.environ["RACE_LOG"], "a") as log: log.write("hit\n")
    return orig(p, *a, **k)
os.stat = racing
sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); runpy.run_path(sys.argv[0], run_name="__main__")
J
o3=$(RACE_LOG="$T/race-n.log" RACE_MARKER=disp-1014 PATH="$T/bin2:$PATH" SG_STATE="$S3" SG_CHECKOUT="$C3" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$S3" JANITOR_NOTIFY="$T/notify" python3 -I "$T/race_unheld.py" "$T/s/janitor.py" 2>&1)
[ "$(/usr/bin/grep -c '^hit$' "$T/race-n.log" 2>/dev/null)" = 1 ] && ok "N: unheld reads the marker once (1 hit)" || ko "N: hits $(cat "$T/race-n.log" 2>/dev/null)"
! grep -q Traceback <<< "$o3" && ok "N: no Traceback" || ko "N traceback: $o3"
/usr/bin/grep -q 'UNHELD #1014' "$S3/cycle.log" 2>/dev/null && [ ! -e "$S3/auto/disp-1014" ] && ok "N: unheld ran for #1014: UNHELD printed, the marker dropped" || ko "N: unheld did not run for #1014: $(cat "$S3/cycle.log" 2>/dev/null) $o3"
# N2: #794: a marker removed before unheld's first read is no marker: no UNHELD line, no Traceback.
S5="$T/st5"; C5="$T/co5"; mkdir -p "$S5/auto" "$S5/cq" "$C5/runs/run-unheld"
echo run-unheld > "$S5/active-runs.txt"; echo "CHILD — GitHub issue(s) 1014 (one PR):" > "$C5/runs/run-unheld/request.txt"
cp "$C3/runs/run-unheld/record.jsonl" "$C5/runs/run-unheld/"; echo run-unheld > "$S5/auto/disp-1014"; touch -t 202001010000 "$S5/auto/disp-1014"
cat > "$T/race_first_unheld.py" <<'J'
# runs janitor.py; inside unheld(), the marker named by RACE_MARKER is removed before its first stat; hits and the removal go to RACE_LOG
import os, runpy, sys
orig = os.stat
def in_unheld():
    f = sys._getframe(1)
    while f is not None:
        if f.f_code.co_name == "unheld": return True
        f = f.f_back
    return False
def racing(p, *a, **k):
    if os.path.basename(os.fspath(p)) == os.environ.get("RACE_MARKER") and in_unheld():
        with open(os.environ["RACE_LOG"], "a") as log: log.write("hit\n")
        if os.path.lexists(p):
            os.unlink(p)
            with open(os.environ["RACE_LOG"], "a") as log: log.write("removed\n")
    return orig(p, *a, **k)
os.stat = racing
sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); runpy.run_path(sys.argv[0], run_name="__main__")
J
o5=$(RACE_LOG="$T/race-n2.log" RACE_MARKER=disp-1014 PATH="$T/bin2:$PATH" SG_STATE="$S5" SG_CHECKOUT="$C5" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$S5" JANITOR_NOTIFY="$T/notify" python3 -I "$T/race_first_unheld.py" "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^removed$' "$T/race-n2.log" 2>/dev/null && ok "N2: the race removed disp-1014 at unheld's first read" || ko "N2: the removal never fired: $(cat "$T/race-n2.log" 2>/dev/null)"
! grep -q Traceback <<< "$o5" && ! /usr/bin/grep -q 'UNHELD #1014' "$S5/cycle.log" 2>/dev/null && ok "N2: a marker gone at unheld's first read is no UNHELD, no Traceback" || ko "N2: $(cat "$S5/cycle.log" 2>/dev/null) $o5"
# P: #798: rule Q's seen-record read. janitor-q-seen-<n> is removed between its exists() and its stat() (the second stat of
# that file): the record is gone, so the pass reads it as none and rule Q releases the card.
S4="$T/st4"; C4="$T/co4"; mkdir -p "$S4/auto" "$S4/cq" "$C4/runs" "$T/bin4"
echo run-gone > "$S4/auto/disp-1031"; touch -t 202001010000 "$S4/auto/disp-1031"; echo x > "$S4/auto/janitor-q-seen-1031"
cat > "$T/bin4/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*) echo 0;;
  *projectsV2/4/items*) printf '1031\tIn Progress\n';;
  *issues/10*) echo open;;
esac
J
chmod +x "$T/bin4/gh"
cat > "$T/race_nth.py" <<'J'
# runs janitor.py; the file named by RACE_MARKER is removed at its RACE_NTH stat call; every hit and the removal go to RACE_LOG
import os, runpy, sys
orig = os.stat; hits = [0]
def racing(p, *a, **k):
    if os.path.basename(os.fspath(p)) == os.environ.get("RACE_MARKER"):
        hits[0] += 1
        with open(os.environ["RACE_LOG"], "a") as log: log.write(f"hit {hits[0]}\n")
        if hits[0] == int(os.environ.get("RACE_NTH", "1")) and os.path.lexists(p):
            os.unlink(p)
            with open(os.environ["RACE_LOG"], "a") as log: log.write("removed\n")
    return orig(p, *a, **k)
os.stat = racing
sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); runpy.run_path(sys.argv[0], run_name="__main__")
J
o4=$(RACE_LOG="$T/race-p.log" RACE_MARKER=janitor-q-seen-1031 RACE_NTH=1 PATH="$T/bin4:$PATH" SG_STATE="$S4" SG_CHECKOUT="$C4" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$S4" JANITOR_NOTIFY="$T/notify" python3 -I "$T/race_nth.py" "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^removed$' "$T/race-p.log" 2>/dev/null && ok "P: the race removed the seen record at stat $(/usr/bin/grep -c '^hit' "$T/race-p.log")" || ko "P: the removal never fired: $(cat "$T/race-p.log" 2>/dev/null)"
! grep -q Traceback <<< "$o4" && ok "P: a seen record removed at rule Q's stat: no Traceback" || ko "P traceback: $o4"
/usr/bin/grep -q '^o 4 Todo 1031$' "$T/board.calls" 2>/dev/null && [ ! -e "$S4/auto/disp-1031" ] && ok "P: rule Q reads the gone record as none and releases #1031" || ko "P #1031 not released: $(cat "$T/board.calls" 2>/dev/null) $o4"
rm -rf "$T"; exit $fail
