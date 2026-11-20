#!/bin/bash
# usage: wtclean.sh   (from watch.sh) Removes squad child worktrees under $SG_CHECKOUT/runs/*/ws once their run has a
# verdict (or the run is a zombie: no verdict, idle 20 min, no process inside), the tree is clean and HEAD is on a remote branch. The run folder and its dashboard server are left alone (the hub lists runs from live servers). A finished worktree
# whose HEAD is on no remote branch is SALVAGED, never kept for good (#2020): the pass writes the local ref
# refs/salvage/wt/<tree path> at its HEAD, then removes the tree, and logs the ref and the sha. The ref is the preservation and
# it pushes nothing; a person, or the next run, lands the work from it. The ref name is the tree's path under the checkout,
# because a tree's own name repeats (`w`, the router's child name) and two runs' trees must never share one ref. A tree a live
# process works in, a tree with uncommitted work and a tree that holds another registered worktree stay. A run whose wave has
# not landed is held whatever the tree holds (WT HELD wave): the wave entry reads the child's worktree after the verdict
# (ADR-0145 rule 9).
# A tree that holds a checked out submodule is its own class: git refuses to remove such a tree whole (`fatal: working trees
# containing submodules cannot be moved or removed`, a line with no run id in it), so every removal here carries the flag that
# case needs, and the line names the run or the tree. One the flag still cannot free is reported once as WT KEPT submodule.
# Every tree an ended run keeps is named once, with its reason, as WT KEPT (#676): a live process, a tree inside it, uncommitted
# work or commits outside the base. An ended run with no open wave leaves active-runs.txt, whatever keeps its tree (rule A below).
# #2574: uncommitted work or commits outside the base keep a tree only while one of its issues is open. A tree whose issues are
# all closed, that nothing moved for SG_WT_ARCHIVE_SECONDS and that no active run names goes once its HEAD and its tracked dirt
# are archived on the remote (archive below). The archived refs keep every commit, so the removal deletes no work but untracked files.
sdir=$(cd "$(dirname "$0")" && pwd)
. "$(dirname "$0")/env.sh"; G=/usr/bin/git; seen="$SG_STATE/wt-unpushed.txt"; touch "$seen"
# The tally the hub's footer reads as `wt 10/21`: `held` per child tree this pass saw under the two child directories
# (the set the footer counts), `free` per one it removed. A `while read` behind a pipe is a subshell, so the count lives
# in a file and never in a variable.
tally="$SG_STATE/wt-tally.$$"; : > "$tally"
# in_use <path>: a live process is working in this tree or below it. Two rules the liveness tests need and a plain
# `grep -F "$w/"` does not keep: a process whose cwd IS the tree carries no trailing slash, and a substring test reads
# `wt-1` inside `wt-10`. `lsof` answers with resolved paths, so the caller resolves $SG_CHECKOUT first.
in_use() { printf '%s\n' "$cwds" | awk -v p="$1" '$0==p || index($0,p"/")==1 {f=1} END{exit !f}'; }
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# wave_hold <run>: 0 while the run's wave has not landed: `wave-<run>` exists from the cut to the landing (wave.sh), wherever
# the run folder sits. The hold has an age cap, SG_WAVE_HOLD_MAX_SECONDS (a day by default), read from the wave's own start
# marker (the marker's mtime when that is missing): a marker no landing will clear -- a run that left active-runs.txt -- would
# hold the trees for good. A lapsed hold is said once, and the trees then follow the rules below, so no unlanded work is lost.
wave_hold() { local m="$SG_STATE/auto/wave-$1" t; [ -e "$m" ] || return 1
  t=$(cat "$SG_STATE/auto/wave-start-$1" 2>/dev/null); case "$t" in ''|*[!0-9]*) t=$(mtime "$m");; esac
  [ $(( now - t )) -lt "${SG_WAVE_HOLD_MAX_SECONDS:-86400}" ] && return 0
  grep -qx "lapsed $1" "$seen" || { echo "WT HOLD LAPSED $1: its wave started $(( now - t )) s ago, past the hold cap (${SG_WAVE_HOLD_MAX_SECONDS:-86400} s); its trees follow the other rules"; echo "lapsed $1" >> "$seen"; }
  return 1; }
# disp_hold <tree>: 0 while an in-flight dispatch names this tree (#1637). autobug writes auto/bug-<n> (autofrontier
# auto/disp-<n>) before the wave cuts the tree, and the run folder that would name it exists only after launch.py writes it, so
# a tree cut at the base tip reads as landed and unused for the one moment that matters. The issue number is the one the tree's
# name carries (gui-<n>); the hold lapses after SG_WT_DISP_HOLD_SECONDS so a marker no pass frees never keeps a tree for good.
disp_hold() { local n=${1##*/} f; n=${n#*-}; case $n in ''|*[!0-9]*) return 1;; esac
  for f in "$SG_STATE/auto/bug-$n" "$SG_STATE/auto/disp-$n"; do [ -e "$f" ] || continue
    [ $(( now - $(mtime "$f") )) -lt "${SG_WT_DISP_HOLD_SECONDS:-3600}" ] && { disp=$n; return 0; }; done
  return 1; }
# in_base <tree>: every commit of the tree's HEAD is in origin/$SG_BASE, by its sha or by its patch. A wave rebases each child
# on its tip and on the base, so a child that landed carries a sha no remote has; `git cherry` reads its patch in the base
# (a merge commit has no patch and is skipped, so a target's `squad: land` merges do not hold it). A commit with no diff
# has no patch of its own: it matches any empty commit of the base, so only its sha can say it landed.
in_base() { [ -n "$SG_BASE" ] && $G -C "$1" rev-parse -q --verify "origin/$SG_BASE" >/dev/null 2>&1 || return 1
  $G -C "$1" merge-base --is-ancestor HEAD "origin/$SG_BASE" 2>/dev/null && return 0
  local c; for c in $($G -C "$1" rev-list --no-merges "origin/$SG_BASE..HEAD" 2>/dev/null); do
    $G -C "$1" diff-tree --quiet "$c^" "$c" 2>/dev/null && return 1; done
  c=$($G -C "$1" cherry "origin/$SG_BASE" HEAD 2>/dev/null) || return 1
  ! printf '%s\n' "$c" | grep -q '^+'; }
# has_sub <tree>: the tree holds a submodule git has checked out — the one state in which `git worktree remove` refuses the
# whole tree whatever else is true of it: `fatal: working trees containing submodules cannot be moved or removed`.
# `git submodule status` marks an uninitialized one with a leading `-`, a populated one with anything else (a space at the
# recorded commit, `+` or `U` otherwise).
has_sub() { $G -C "$1" submodule status 2>/dev/null | grep -qE '^[ +U]'; }
# wt_remove <repo> <tree>: the one worktree removal, so that refusal is answered in one place. The caller has already proved
# the tree clean, unused and landed, and a submodule's own dirt reads as ` M sub` in that same status check, so the flag the
# populated case needs loses nothing. The class is read BEFORE the attempt: a removal that fails can take the worktree's own
# registration with it, and then the tree can no longer say what it is. Returns 0 when the tree is gone, 2 when a tree
# holding a checked out submodule would not go even with that flag, 1 otherwise; wt_forced says the flag was used.
# scratch: a tool's own files at the top of a tree, which no commit carries and which are no work: the Codex CLI writes
# `.codex/config.toml` into each tree it works in. Read as dirt, it kept every run tree of 10-18 for good, silently (#676).
# dirt <tree>: the tree's `git status --porcelain`, less an untracked scratch entry. Empty means nothing can be lost.
scratch=${SG_WT_SCRATCH:-.codex/}
dirt() { $G -C "$1" status --porcelain 2>/dev/null | awk -v s=" $scratch " '!(substr($0, 1, 3) == "?? " && index(s, " " substr($0, 4) " "))'; }
# inner <tree>: another registered worktree lies inside the tree (a run folder's ws/ trees). `git worktree remove` deletes the
# whole directory, the inner tree and its uncommitted work with it, and says nothing: the outer tree waits for the inner one.
inner() { $G -C "$SG_CHECKOUT" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | awk -v p="$1/" 'index($0, p) == 1 {f=1} END {exit !f}'; }
# kept <class> <tree> <tag> <reason>: a tree an ended run keeps, named once per tree and reason (#676).
kept() { grep -qxF "keep $3 $2" "$seen" && return 0; echo "WT KEPT $1 ${2##*/}: $4; kept"; echo "keep $3 $2" >> "$seen"; }
# ended <run folder>: 0 when the run has ended for good, read as the dashboard reads it (the last verdict after the last run_start
# or resume): `completed`, `failed` with nothing in `remains`, or `failed` with remains and a record still for
# SG_RUN_RESUME_HOLD_SECONDS (a day by default). The loop never resumes a run, so past that no resume is coming, and the tree
# held for one stayed for good (#676). Else 1, and $why names the hold when there is one to name: a run with no verdict is live.
# liftable (#2574): the hold is one a closed issue lifts: needs-human, stopped, or failed with remains.
ended() { local f="$1/record.jsonl" last; why=; liftable=
  wave_hold "${1##*/}" && return 1
  last=$(awk '/"event": *"(run_start|resume)"/{v=""} /"event": *"verdict"/{v=$0} END{print v}' "$f" 2>/dev/null)
  case $last in
    *'"verdict": "completed"'*) return 0;;
    *'"verdict": "failed"'*) printf '%s' "$last" | grep -qE '"remains": *\[ *\]' && return 0
      [ $(( now - $(mtime "$f") )) -ge "${SG_RUN_RESUME_HOLD_SECONDS:-86400}" ] && return 0
      why="${1##*/} failed with remains a resume may still take"; liftable=1;;
    *'"verdict": "needs-human"'*) why="${1##*/} needs a human: the owner decides"; liftable=1;;
    *'"verdict": "stopped"'*) why="${1##*/} is paused: the owner resumes it"; liftable=1;;
    '') ;;
    *) why="${1##*/} ended with a verdict a resume may take";; esac
  return 1; }
# run_issues <run folder>: the issues one run worked: its ws/issue-<n> tree names, its request's `--- issue #N`, `GitHub issue
# #N` and CHILD `GitHub issue(s) a, b` lines, and the run's wave entries in the issue ledger.
run_issues() { local r=${1%/} x
  for x in "$r"/ws/issue-*; do x=${x##*/issue-}; case $x in ''|*[!0-9]*) ;; *) echo "$x";; esac; done
  if [ -f "$r/request.txt" ]; then
    grep -oE '^--- issue #[0-9]+|GitHub issue #[0-9]+' "$r/request.txt" | grep -oE '[0-9]+$'
    grep '^CHILD' "$r/request.txt" | grep -oE 'GitHub issue\(s\) #?[0-9][0-9, ]*' | sed 's/^[^)]*) #*//' | tr ', ' '\n\n' | grep -E '^[0-9]+$'
  fi
  [ -z "$ledger" ] || printf '%s\n' "$ledger" | awk -F '\t' -v r="${r##*/}" '$1 == r || "run-" $1 == r {print $2}'; }
# tree_issues <top> [run folder]: the issues of a tree: its gui-<n> name and the issues of every run folder inside it. A run of
# the checkout's own runs/ has no tree of its own, so it reads that run alone.
tree_issues() { local t=$1 n r
  { if [ "$t" = "$SG_CHECKOUT" ]; then run_issues "$2"
    else n=${t##*/}; case $n in gui-*) n=${n#gui-}; case $n in ''|*[!0-9]*) ;; *) echo "$n";; esac;; esac
      for r in "$t"/runs/*/; do [ -d "$r" ] && run_issues "$r"; done; fi; } | sort -un | tr '\n' ' '; }
# all_closed <issue>...: 0 when the list is not empty and the facts file says each issue is closed. Else 1: $cwhy names why
# and $ctag is its short tag. An issue the file does not hold reads as not closed.
all_closed() { local x; cwhy=; ctag=
  [ "$facts" = 1 ] || { cwhy="the issue facts file is missing, stale or of another repository"; ctag=facts; return 1; }
  [ $# -gt 0 ] || { cwhy="no issue names it"; ctag=none; return 1; }
  for x; do case " $closed " in *" $x "*) ;; *) cwhy="issue #$x is not closed"; ctag="open$x"; return 1;; esac; done; }
# quiet <tree> [run folder]...: nothing moved the tree's HEAD reflog or a run record for SG_WT_ARCHIVE_SECONDS. The directory
# mtime is never read: a pass, a prune or a scratch file moves it.
quiet() { local w=$1 f t m=0 r; shift
  for f in "$($G -C "$w" rev-parse --path-format=absolute --git-path logs/HEAD 2>/dev/null)" "$w"/runs/*/record.jsonl; do
    [ -f "$f" ] || continue; t=$(mtime "$f"); [ "$t" -gt $m ] && m=$t; done
  for r; do f="${r%/}/record.jsonl"; [ -f "$f" ] || continue; t=$(mtime "$f"); [ "$t" -gt $m ] && m=$t; done
  [ $m -gt 0 ] && [ $(( now - m )) -ge "${SG_WT_ARCHIVE_SECONDS:-7200}" ]; }   # no reflog and no record prove nothing: the tree stays
# active_in <dir>: a line of active-runs.txt names this folder or one inside it. Such a run keeps its tree, verdict or not.
active_in() { local r d p; [ -f "$SG_STATE/active-runs.txt" ] || return 1
  p=$(cd "$1" 2>/dev/null && pwd -P) || p=$1   # the lines are compared resolved, as lsof answers
  while read -r r; do case "$r" in '') continue;; /*) d=$r;; *) d="$SG_CHECKOUT/runs/$r";; esac
    d=$(cd "$d" 2>/dev/null && pwd -P) || continue
    case $d in "$p"|"$p"/*) return 0;; esac; done < "$SG_STATE/active-runs.txt"; return 1; }
# archive <class> <tree>: 0 when the removal loses no tracked work. No push when HEAD is in the base or on a remote branch and no
# tracked file changed. Else one push writes HEAD to refs/archive/wt/<tree path>/<sha9>/head and the tracked dirt, as a `git
# stash create` commit, to .../dirty. An untracked file is never archived. At most SG_WT_ARCHIVE_MAX pushes a pass. A refused
# push keeps the tree, named once. $aref names the refs the push wrote.
archive() { local c=$1 w=$2 sha td d rel ref spec
  aref=; sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || return 1
  td=$($G -C "$w" status --porcelain --untracked-files=no 2>/dev/null)
  [ -z "$td" ] && { in_base "$w" || [ -n "$($G -C "$w" branch -r --contains "$sha" 2>/dev/null | head -1)" ]; } && return 0
  [ "$(grep -c . "$arch")" -lt "${SG_WT_ARCHIVE_MAX:-10}" ] || { kept "$c" "$w" archcap "the pass reached its cap of ${SG_WT_ARCHIVE_MAX:-10} archive pushes"; return 1; }
  echo push >> "$arch"
  rel=${w#"$SG_CHECKOUT"/}; [ "$rel" = "$w" ] && rel=${w#/}
  ref="refs/archive/wt/$(printf '%s' "$rel" | tr '/' '_')/${sha:0:9}"; spec="$sha:$ref/head"
  if [ -n "$td" ]; then d=$($G -C "$w" stash create 2>/dev/null)
    [ -n "$d" ] || { kept "$c" "$w" archdirt "git stash create made no commit of the tracked dirt"; return 1; }
    spec="$spec +$d:$ref/dirty"; fi   # a stash commit is new each pass: an earlier dirty ref is replaced, never a refused push
  GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=60 $G -C "$w" push -q "${SG_LAND_REMOTE:-origin}" $spec >/dev/null 2>&1 \
    || { kept "$c" "$w" archpush "archive push refused ($ref)"; return 1; }
  aref=$ref; }
# free_gate <class> <tree> <top> <tag> <reason> [run folder]: a tree kept for <reason> (uncommitted work, commits outside the
# base) may go when every issue of <top> is closed, no active run line names it, it is quiet and its work is archived. Else it
# stays, and the kept line names the reason and what holds the tree.
free_gate() { local c=$1 w=$2 top=$3 tag=$4 txt=$5 ad=${6:-$2}
  all_closed $(tree_issues "$top" "$ad") || { kept "$c" "$w" "$tag-$ctag" "$txt; $cwhy"; return 1; }
  active_in "$ad" && { kept "$c" "$w" "$tag-act" "$txt; its run is still in active-runs.txt"; return 1; }
  quiet "$w" ${6:+"$6"} || { kept "$c" "$w" "$tag-quiet" "$txt; it moved in the last ${SG_WT_ARCHIVE_SECONDS:-7200} s"; return 1; }
  archive "$c" "$w"; }
# free_archived <class> <tree> <run folder> <label>: the removal after free_gate, with one --force (uncommitted work goes, its
# tracked part archived). $fb is the branch the tree had, for the caller's branch rule.
free_archived() { local c=$1 w=$2 sha rc
  fb=$($G -C "$w" branch --show-current); sha=$($G -C "$w" rev-parse --short=9 HEAD 2>/dev/null)
  wt_remove "$SG_CHECKOUT" "$w" force "$3"; rc=$?
  if [ $rc = 0 ]; then echo "WT CLEAN $4 ${fb:-detached} ${sha:-} (its issues are closed${aref:+; archived at $aref}; removed with --force)"; return 0; fi
  kept "$c" "$w" archrm "${wt_why:-git refused to remove it}${aref:+; its work is at $aref}"; return 1; }
wt_remove() { wt_forced=; wt_sub=0; wt_why=; has_sub "$2" && wt_sub=1
  local s; for s in $scratch; do $G -C "$2" status --porcelain -- "$s" 2>/dev/null | grep -qxF "?? $s" && rm -rf "${2:?}/$s"; done   # the caller proved it is all the dirt
  if [ "$3" = force ]; then   # #2574: after an archive only; the tests that keep a tree run again right before the one --force
    cwds=$("$SG_LSOF" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    in_use "$2" && { wt_why="a live process works in it"; return 1; }
    inner "$2" && { wt_why="another worktree lies inside it"; return 1; }
    active_in "${4:-$2}" && { wt_why="its run is still in active-runs.txt"; return 1; }
    $G -C "$1" worktree remove --force "$2" 2>/dev/null; return; fi
  $G -C "$1" worktree remove "$2" 2>/dev/null && return 0
  [ $wt_sub = 1 ] || return 1
  $G -C "$1" worktree remove --force "$2" 2>/dev/null || return 2
  wt_forced=1; return 0; }   # empty unless the flag was used: the lines test it with ${wt_forced:+...}
# T (#1581): the pytest temp trees a killed gate left behind. pytest keeps a tree with a .lock for three days
# (_pytest.pathlib.LOCK_TIMEOUT), and the loop kills gates on purpose (the proof bound, a takeover, a tree kill), so those trees
# filled the disk. The root is resolved as pytest does: PYTEST_DEBUG_TEMPROOT, else the temp directory plus pytest-of-<user>.
# A tree goes when its .lock names a pid that is gone (`kill -0`; a reused pid reads as live, so the tree stays), or when it has
# no lock and is a day old (SG_PYTEST_TMP_DAYS). garbage-* trees follow the same rule. A removal that fails is one line and the
# pass goes on. One TMP PRUNED line names the count and the size.
tmp_prune() {
  troot=${PYTEST_DEBUG_TEMPROOT:-}
  [ -n "$troot" ] || troot=$(python3 -c 'import getpass,os,tempfile;print(os.path.join(tempfile.gettempdir(),"pytest-of-"+getpass.getuser()))' 2>/dev/null)
  [ -n "$troot" ] || troot="${TMPDIR:-/tmp}/pytest-of-${USER:-$(id -un)}"
  [ -n "${PYTEST_DEBUG_TEMPROOT:-}" ] && troot="$troot/pytest-of-${USER:-$(id -un)}"
  pn=0; pk=0
  if [ -d "$troot" ]; then
    for t in "$troot"/pytest-[0-9]* "$troot"/garbage-*; do
      [ -d "$t" ] && [ ! -L "$t" ] || continue
      if [ -e "$t/.lock" ]; then
        lp=$(tr -dc '0-9' < "$t/.lock" 2>/dev/null)
        # no digits (a gate killed before it wrote its pid) ages out like an unlocked tree; a pid is dead only when kill says "No such process" (EPERM is a live process of another user)
        if [ -n "$lp" ]; then
          case "$(kill -0 "$lp" 2>&1)" in *"No such process"*) ;; *) continue;; esac
        else
          [ $(( now - $(mtime "$t") )) -ge $(( ${SG_PYTEST_TMP_DAYS:-1} * 86400 )) ] || continue
        fi
      else
        [ $(( now - $(mtime "$t") )) -ge $(( ${SG_PYTEST_TMP_DAYS:-1} * 86400 )) ] || continue
      fi
      sk=$(du -sk "$t" 2>/dev/null | awk '{print $1+0}')
      if rm -rf "$t" 2>/dev/null && [ ! -e "$t" ]; then pn=$((pn + 1)); pk=$((pk + ${sk:-0})); else echo "TMP KEPT ${t##*/}: could not remove it"; fi
    done
  fi
  [ $pn -eq 0 ] || echo "TMP PRUNED $pn trees, $(awk -v k=$pk 'BEGIN{ if (k >= 1048576) printf "%.1f GiB", k/1048576; else printf "%.1f MiB", k/1024 }')"
}
now=$(date +%s)
tmp_prune   # before the cd: the prune needs no checkout
cd "$SG_CHECKOUT" || exit 0
SG_CHECKOUT=$(pwd -P)   # resolved: every liveness test below compares a path with a process cwd, and lsof answers with the resolved one. Under a symlinked checkout (/var -> /private/var, a linked home) the unresolved form never matched, so a tree a live process was working in read as unused.
cwds=$("$SG_LSOF" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')   # every live process's cwd, for the zombie test
# #2574: the closed issues, read once a pass from the file the janitor writes (_facts_sync), never from GitHub. The file is proof
# only when it names $SG_REPO and its last sync is under SG_WT_FACTS_MAX_SECONDS old; else facts=0 and every tree stays.
facts=0; closed=$(python3 -I -c '
import calendar, json, sys, time
c = json.load(open(sys.argv[1])); t = calendar.timegm(time.strptime(c["synced"], "%Y-%m-%dT%H:%M:%SZ"))
if not sys.argv[2] or c.get("repo") != sys.argv[2] or not time.time() - t < float(sys.argv[3]): sys.exit(1)
print(" ".join(k for k, v in c["issues"].items() if isinstance(v, dict) and isinstance(v.get("f"), list) and v["f"][:1] == ["closed"]))
' "$SG_STATE/issue-facts.json" "${SG_REPO:-}" "${SG_WT_FACTS_MAX_SECONDS:-1800}" 2>/dev/null) && facts=1
ledger=; [ -f "$sdir/issue_ledger.py" ] && [ -n "${SG_REPO:-}" ] && ledger=$(python3 "$sdir/issue_ledger.py" entered "$SG_REPO" 2>/dev/null)
arch="$SG_STATE/wt-arch.$$"; : > "$arch"   # one line per archive push this pass: the ws loop is a subshell
$G worktree list --porcelain | sed -n 's#^worktree \(.*/runs/run-[^/]*/ws/.*\)#\1#p' | while read -r w; do
  run=${w%%/ws/*}   # only after the RUN's verdict: the run's land step reads every child workspace (removing one early fails it with land_workspace_missing)
  # no verdict yet: only a ZOMBIE run (record.jsonl idle >20 min and no process whose cwd is inside the run folder) is cleaned, under the same clean-tree / HEAD-on-a-remote-branch rules below
  grep -q '"event": *"verdict"' "$run/record.jsonl" 2>/dev/null || { [ -n "$(find "$run/record.jsonl" -mmin +20 2>/dev/null)" ] && ! printf '%s\n' "$cwds" | grep -qF "$run/" || continue; }
  # V (#464): a run whose wave has not landed keeps its child trees. The wave entry reads the child's worktree AFTER the
  # verdict -- it reviews the child's own diff there and enters its commits into the wave branch (ADR-0145 rule 9) -- so a
  # tree removed here left the entry with nothing to read ("WAVE no entry for #N: the child's worktree ... is not there"),
  # every wave landed empty and no card left the queue. `wave-<run>` exists from the cut to the landing; `waved-<run>` is
  # the durable record that the entry is done, and a wave that reached its age cap lands and clears the marker (rule 13).
  if wave_hold "${run##*/}"; then
    grep -qx "wave $w" "$seen" || { echo "WT HELD wave ${run##*/} ${w##*/}: the run's wave has not landed; kept"; echo "wave $w" >> "$seen"; }
    continue; fi
  inner "$w" && { kept "${run##*/}" "$w" inner "another worktree lies inside it"; continue; }
  in_use "$w" && { kept "${run##*/}" "$w" use "a live process works in it"; continue; }
  # #2574: uncommitted work keeps the tree while an issue of its top tree is open; else it is archived and the tree goes.
  if [ -n "$(dirt "$w")" ]; then
    top=$(cd "${run%/runs/*}" 2>/dev/null && pwd -P) || top=${run%/runs/*}   # the tree that holds the run folder, or the checkout
    free_gate "${run##*/}" "$w" "$top" dirt "uncommitted work in it" "$run" && free_archived "${run##*/}" "$w" "$run" "${run##*/} ${w##*/}" || continue
    case $fb in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$fb" >/dev/null 2>&1;; esac
    continue; fi
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  own=$($G -C "$w" branch --show-current)
  # #2020: a clean tree of an ended run whose commits are on no remote branch is salvaged, never kept for good. The ref holds the
  # work once the tree and its branch are gone, so nothing is lost and nothing is pushed.
  if ! in_base "$w" && [ -z "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ]; then
    # #767: the branch passes and the frontier see only `fix/<n>-…` or `gui/<n>-…` (workbranches.sh), so the line names the
    # push under such a name: the tree's own name when it is one and the push is a fast-forward, else a fresh one to fill in
    to='fix/<n>-<slug>'
    if printf '%s' "$own" | grep -qE '^(fix|gui)/[0-9]+([^0-9]|$)'; then
      { ! $G rev-parse -q --verify "refs/remotes/origin/$own" >/dev/null || $G merge-base --is-ancestor "origin/$own" "$sha"; } && to=$own; fi
    rel=${w#"$SG_CHECKOUT"/}; [ "$rel" = "$w" ] && rel=${w#/}   # a registered path the resolved checkout does not prefix
    ref="refs/salvage/wt/$(printf '%s' "$rel" | tr '/' '_')"
    if $G -C "$w" update-ref "$ref" "$sha" 2>/dev/null; then
      wt_remove "$SG_CHECKOUT" "$w"; rc=$?
      if [ $rc = 0 ]; then echo "WT SALVAGED ${run##*/} ${w##*/} ${sha:0:9} $ref; a person lands it: git push origin ${sha:0:9}:refs/heads/$to"
        case $own in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$own" >/dev/null 2>&1;; esac   # its work is in the ref: the branch goes with the tree
      elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule ${run##*/} ${sha:0:9} ${w}: a checked out submodule git refuses to remove, even with --force; its work is at $ref; kept"; echo "sub $w" >> "$seen"; }
      else grep -qx "unpushed $w" "$seen" || { echo "WT KEPT unpushed ${run##*/} ${w##*/}: git refused to remove the tree; its work is at $ref; kept"; echo "unpushed $w" >> "$seen"; }; fi
    else grep -qx "unpushed $w" "$seen" || { echo "WT KEPT unpushed ${run##*/} ${w##*/}: the pass could not write $ref; the tree stays; kept"; echo "unpushed $w" >> "$seen"; }; fi
    continue
  fi
  b=$($G -C "$w" branch --show-current)
  wt_remove "$SG_CHECKOUT" "$w"; rc=$?
  if [ $rc = 0 ]; then echo "WT CLEAN ${run##*/} ${b:-detached} ${sha:0:9}${wt_forced:+ (submodule tree: removed with --force)}"
    case $b in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$b" >/dev/null 2>&1;; esac   # throwaway local names only, and only once the tree is gone
  # the tree a populated submodule kept alive: reported once with its own reason, never as git's own nameless fatal
  elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule ${run##*/} ${sha:0:9} ${w}: a checked out submodule git refuses to remove, even with --force; kept"; echo "sub $w" >> "$seen"; }; fi
done
# C: the child trees the loop holds at $SG_CHECKOUT/worktrees/child/* (and .worktrees/*) — the set the hub's footer counts
# as `wt N` and the one the reader is looking at when he asks what can be freed. No pass touched them until now, so a landed
# issue left its tree behind for good (measured 2026-10-13: 20 trees, 10 of them already in origin/main). A tree goes only
# when nothing can be lost and nothing is using it: no live process whose cwd is inside it, a clean tree, and HEAD in the
# base (its work landed, by sha or by patch), and no run folder inside it whose wave has not landed. Anything else is held,
# and the tally below carries the ratio the footer shows.
for rel in worktrees/child .worktrees; do
  [ -d "$SG_CHECKOUT/$rel" ] || continue
  for w in "$SG_CHECKOUT/$rel"/*; do
    [ -e "$w/.git" ] || continue                 # what `git worktree add` writes: a stray directory is not a worktree
    echo held >> "$tally"
    in_use "$w" && continue
    inner "$w" && { kept child "$w" inner "another worktree lies inside it"; continue; }
    ar=; [ -z "$(dirt "$w")" ] || ar="dirt uncommitted work in it"   # #2574: a keep reason a closed issue may lift (below)
    # A run folder inside the tree whose wave has not landed holds it (#477). The run folder is ignored, so the tree reads
    # clean, and its HEAD may already be in the base: between the run's verdict and its wave's landing only the marker keeps it.
    hold=; for r in "$w"/runs/*/; do [ -d "$r" ] && wave_hold "$(basename "$r")" && { hold=$(basename "$r"); break; }; done
    if [ -n "$hold" ]; then grep -qx "wave $w" "$seen" || { echo "WT HELD wave $hold ${w##*/}: the run's wave has not landed; kept"; echo "wave $w" >> "$seen"; }
      continue; fi
    # #1637: a tree an in-flight dispatch cut is held before the run folder can name it.
    if disp_hold "$w"; then grep -qx "disp $w" "$seen" || { echo "WT HELD dispatch #$disp ${w##*/}: the dispatch of #$disp has not started its run; kept"; echo "disp $w" >> "$seen"; }
      continue; fi
    [ -n "$ar" ] || in_base "$w" || ar="ahead commits not in origin/$SG_BASE"
    # An active run folder inside the child itself owns this tree, even before it starts a process. The janitor drops a
    # missing folder from active-runs, so this pass only preserves a folder that still exists and has no verdict.
    live=0
    if [ -f "$SG_STATE/active-runs.txt" ]; then while read -r r; do
      rf=$(cd "$r" 2>/dev/null && pwd -P) || continue
      case $rf in "$w"/*) f="$rf/record.jsonl"; [ -f "$f" ] && ! grep -q '"event": *"verdict"' "$f" 2>/dev/null && { live=1; break; };; esac
    done < "$SG_STATE/active-runs.txt"; fi
    # A run in flight that names this tree is the other case a merged tree is still in use: the child may not have started, so
    # no process sits inside it yet. Same test as the fork rule below, and the same lesson (gui/1227).
    if [ $live = 0 ]; then for r in "$SG_CHECKOUT"/runs/run-* "$SG_CHECKOUT"/worktrees/run/*/runs/run-* "$SG_CHECKOUT"/worktrees/child/*/runs/run-*; do   # a worktree run too (#585)
      f="$r/record.jsonl"; [ -f "$f" ] || continue
      grep -q '"event": *"verdict"' "$f" 2>/dev/null && continue
      [ $(( now - $(mtime "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
      grep -qF "${w##*/}" "$f" "$r/request.txt" 2>/dev/null && { live=1; break; }
    done; fi
    [ $live = 0 ] || continue
    if [ -n "$ar" ]; then   # #2574: kept while an issue is open; else archived, then removed
      free_gate child "$w" "$w" "${ar%% *}" "${ar#* }" && free_archived child "$w" "$w" "child ${w##*/}" || continue
      echo free >> "$tally"; case $fb in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$fb" >/dev/null 2>&1;; esac
      continue; fi
    b=$($G -C "$w" branch --show-current); sha=$($G -C "$w" rev-parse --short=9 HEAD 2>/dev/null)
    wt_remove "$SG_CHECKOUT" "$w"; rc=$?
    if [ $rc = 0 ]; then echo free >> "$tally"; echo "WT CLEAN child ${w##*/} ${b:-detached} ${sha:-}${wt_forced:+ (submodule tree: removed with --force)}"
      case $b in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$b" >/dev/null 2>&1;; esac   # its work is in the base: the branch goes with the tree
    elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule child ${w##*/} ${sha:-} ${w}: a checked out submodule git refuses to remove, even with --force; kept"; echo "sub $w" >> "$seen"; }; fi
  done
done
# R (#581): the run's own tree, worktrees/run/<key> on squad/run/<key> (launch.py, #580). One launch makes one, and nothing else
# removed it. It goes when every run folder inside it has ended for good, read from the record as the dashboard reads it (the last
# verdict after the last run_start or resume): `completed`, or `failed` with nothing in `remains`. A run with no verdict, a paused
# one (`stopped`), `needs-human`, `conflict` or a failure that still has remains can be resumed, so it keeps the tree. The same
# rules as every tree above hold: no live process inside, a held wave keeps it, a clean tree, and HEAD in the base. The branch
# goes with the tree, and only once the tree is gone.
for w in "$SG_CHECKOUT"/worktrees/run/*; do
  [ -e "$w/.git" ] || continue
  n=0; end=1; why=; lift=
  for r in "$w"/runs/*/; do [ -f "$r/record.jsonl" ] || continue; n=$((n + 1))
    ended "${r%/}" && continue
    [ -n "$liftable" ] && ! active_in "$r" && { lift=$why; continue; }   # #2574: a closed issue may lift this hold
    end=0; break
  done
  [ -z "$why" ] || [ $end = 1 ] || kept run "$w" hold "$why"
  [ $n -gt 0 ] && [ $end = 1 ] || continue
  if [ -n "$lift" ]; then all_closed $(tree_issues "$w") && quiet "$w" || { kept run "$w" hold "$lift${cwhy:+; $cwhy}"; continue; }; fi
  in_use "$w" && { kept run "$w" use "a live process works in it"; continue; }
  inner "$w" && { kept run "$w" inner "another worktree lies inside it"; continue; }
  ar=; [ -z "$(dirt "$w")" ] || ar="dirt uncommitted work in it"
  [ -n "$ar" ] || in_base "$w" || ar="ahead commits not in origin/$SG_BASE"
  if [ -n "$ar" ]; then   # #2574: kept while an issue is open; else archived, then removed
    free_gate run "$w" "$w" "${ar%% *}" "${ar#* }" && free_archived run "$w" "$w" "run ${w##*/}" || continue
    case $fb in squad/run/*) $G branch -D "$fb" >/dev/null 2>&1;; esac
    continue; fi
  b=$($G -C "$w" branch --show-current); sha=$($G -C "$w" rev-parse --short=9 HEAD 2>/dev/null)
  wt_remove "$SG_CHECKOUT" "$w"; rc=$?
  if [ $rc = 0 ]; then echo "WT CLEAN run ${w##*/} ${b:-detached} ${sha:-}${wt_forced:+ (submodule tree: removed with --force)}"
    case $b in squad/run/*) $G branch -D "$b" >/dev/null 2>&1;; esac
  elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule run ${w##*/} ${sha:-} ${w}: a checked out submodule git refuses to remove, even with --force; kept"; echo "sub $w" >> "$seen"; }; fi
done
# Q: a finished tree under .claude/worktrees, the one each workflow run's agent tools make. It goes once it is clean, unlocked,
# not in use, holds no tree inside it, its HEAD is in the base, and nothing moved that HEAD for SG_WT_FRESH_MINUTES (a tree made a
# moment ago reads as landed, #676). A locked tree is a live agent's and stays. Only the checkout is freed; its branch stays.
locked_tree() { $G -C "$SG_CHECKOUT" worktree list --porcelain | awk -v p="$1" '/^worktree /{cur=substr($0, 10)} /^locked/{if (cur == p) f=1} END {exit !f}'; }
for w in "$SG_CHECKOUT"/.claude/worktrees/*; do
  [ -e "$w/.git" ] || continue
  locked_tree "$w" && continue
  in_use "$w" && continue
  inner "$w" && continue
  [ -z "$(dirt "$w")" ] || continue
  in_base "$w" || continue
  hl=$($G -C "$w" rev-parse --path-format=absolute --git-path logs/HEAD 2>/dev/null); [ -e "$hl" ] || hl=$w
  [ $(( now - $(mtime "$hl") )) -ge $(( ${SG_WT_FRESH_MINUTES:-30} * 60 )) ] || continue
  sha=$($G -C "$w" rev-parse --short=9 HEAD 2>/dev/null)
  wt_remove "$SG_CHECKOUT" "$w" && echo "WT CLEAN claude ${w##*/} ${sha:-}${wt_forced:+ (submodule tree: removed with --force)}"
done
# acceptance-command trees (acpr.sh) left by a killed gate: drop any older than 2 h
$G worktree list --porcelain | sed -n 's#^worktree \(.*/acpr\.[^/]*\)$#\1#p' | while read -r w; do
  [ -n "$(find "$w" -maxdepth 0 -mmin +120 2>/dev/null)" ] && $G worktree remove --force "$w" && echo "WT CLEAN acpr ${w##*/}"; done
# L: loose detached trees (acpr.* gate trees, ~/wt-* scratch trees; never the e2e trees): drop one that is clean, 10 min old, has no process inside and whose HEAD is on a remote branch (nothing is lost); a dirty or unpushed one is only reported once as WT LOOSE
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^detached/{print w}' | while read -r w; do
  case $w in "$HOME"/wt-*|*/acpr.*) ;; *) continue;; esac
  case $w in "${SG_E2E_DIR:-//}"|"${SG_E2E_BUILD_DIR:-//}"|"$HOME"/wt-e2e*) continue;; esac   # the e2e trees are reused between verifications: an idle one is not a loose one (a run lost them mid-verification, 2026-10-10)
  [ -d "$w" ] || continue; [ -n "$(find "$w" -maxdepth 0 -mmin +10 2>/dev/null)" ] || continue
  in_use "$w" && continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  if [ -z "$(dirt "$w")" ] && [ -n "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ]; then
    wt_remove "$SG_CHECKOUT" "$w" && echo "WT CLEAN loose ${w##*/} ${sha:0:9}${wt_forced:+ (submodule tree: removed with --force)}"
  else grep -qx "loose $w" "$seen" || { echo "WT LOOSE ${w##*/} ${sha:0:9}: detached, dirty or on no remote branch; kept"; echo "loose $w" >> "$seen"; }; fi
done
# fork-only issue worktrees ($SG_FORK_DIR, ~/wt-as-<n>, ~/wt-fr-<n>): once the issue is closed, the tree clean and its branch
# merged into $SG_FORK_BASE, remove the tree and delete the branch (git branch -d: refuses an unmerged one)
if [ -n "$SG_FORK_DIR" ] && [ -d "$SG_FORK_DIR" ]; then
  $G -C "$SG_FORK_DIR" worktree list --porcelain | sed -En 's#^worktree (.*/wt-(as|fr)-[0-9]+)$#\1#p' | while read -r w; do
    n=${w##*-}; b=$($G -C "$w" branch --show-current 2>/dev/null)
    # Which run owns this tree? The tree's own name is the wrong key twice over: a run in flight never writes "wt-as-<n>" to its
    # record (that only appears when the gate writes its outcome), and "some run has no verdict yet" is true of every tree at
    # once, because one live run anywhere parks them all. What a run does carry is the branch it was launched for, so the test
    # is the landing pass's: a process inside the tree, or a run whose record moved in the orphan window and whose request names
    # gui/<n> or wt-(as|fr)-<n>. The old record-grep deleted a live run's worktree the moment a sibling issue closed, and every
    # ac_command then failed with "cd: No such file or directory" (gui/1227, run 45a682, 2026-10-10).
    live=0
    in_use "$w" && live=1
    if [ $live = 0 ]; then for r in "$SG_CHECKOUT"/runs/run-* "$SG_CHECKOUT"/worktrees/run/*/runs/run-* "$SG_CHECKOUT"/worktrees/child/*/runs/run-*; do   # a worktree run too (#585)
      f="$r/record.jsonl"; [ -f "$f" ] || continue
      grep -q '"event": *"verdict"' "$f" 2>/dev/null && continue   # a run that has ended owns nothing
      [ $(( now - $(mtime "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
      grep -qE "(^|[^0-9])gui/$n([^0-9]|$)|wt-(as|fr)-$n([^0-9]|$)" "$f" "$r/request.txt" 2>/dev/null && { live=1; break; }
    done; fi
    [ $live = 0 ] || continue
    [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
    [ "$(gh api repos/$SG_REPO/issues/$n -q .state 2>/dev/null)" = closed ] || continue
    [ -z "$b" ] || $G -C "$SG_FORK_DIR" merge-base --is-ancestor "$b" "$SG_FORK_BASE" || continue
    wt_remove "$SG_FORK_DIR" "$w" && { [ -n "$b" ] && $G -C "$SG_FORK_DIR" branch -d "$b" >/dev/null 2>&1; echo "WT CLEAN fork #$n ${b:-detached}${wt_forced:+ (submodule tree: removed with --force)}"; }
  done; $G -C "$SG_FORK_DIR" worktree prune
fi
# Loose worktrees under ~ (git worktree add ~/wt-<n>-*): drop once the branch is in the landing branch ($SG_LAND_BRANCH, default dev),
# the tree is clean and nothing is ahead of it. The fact is git's own: the branch tip is an ancestor of origin/<landing branch>, so the
# force delete loses no commit. No pull request is read.
land=${SG_LAND_BRANCH:-dev}
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^branch /{sub("refs/heads/","",$2); print w, $2}' | while read -r w b; do
  case $w in "$HOME"/wt-*) ;; *) continue;; esac
  in_use "$w" && continue
  [ -z "$(dirt "$w")" ] || continue
  $G rev-parse -q --verify "refs/remotes/origin/$land" >/dev/null 2>&1 || continue   # no landing branch to compare with
  # A tree made a moment ago at the base tip reads exactly as a landed one: a gate or an agent starting in it lost it (#676). Its
  # HEAD reflog says when HEAD last moved; a tree that has not moved for SG_WT_FRESH_MINUTES (30 by default) is not starting.
  hl=$($G -C "$w" rev-parse --path-format=absolute --git-path logs/HEAD 2>/dev/null); [ -e "$hl" ] || hl=$w
  if $G merge-base --is-ancestor "$b" "origin/$land" 2>/dev/null; then
    [ $(( now - $(mtime "$hl") )) -ge $(( ${SG_WT_FRESH_MINUTES:-30} * 60 )) ] || continue
    wt_remove "$SG_CHECKOUT" "$w" && $G branch -D "$b" >/dev/null 2>&1 && echo "WT CLEAN landed $b (in $land at $($G rev-parse --short=9 "origin/$land"))${wt_forced:+ (submodule tree: removed with --force)}"
  else grep -qx "kept $w" "$seen" || { echo "WT KEPT $b: commits not in $land yet; nothing removed"; echo "kept $w" >> "$seen"; }; fi
done
# A (#676, #708): an ended run leaves active-runs.txt, whatever keeps its tree. Every reader of the file (childcc, the janitor,
# the token reader) reads each line on every pass, so a line an ended run keeps doubles the cycle for nothing. The line goes when
# the run has a verdict after its last run_start or resume (a resumed run is live again), its wave is not open (no wave-<run>:
# that marker stays from the cut to the landing, so a wave still landing keeps the line), its record has not moved for
# SG_RUN_LINE_SECONDS (an hour by default: the janitor's per-run rules have read it) and no process is inside its folder. The
# tree stays for a resume or for the owner: a held, unpushed, dirty or inner tree, a needs-human, paused or failed verdict with
# remains keep the tree and never the line. This holds #693's rule: a failed run whose wave is over leaves too. A line whose run
# folder is gone leaves at once, whatever its verdict (#1433): the folder held its record, so no rule can read the line, and a
# listed folder that is gone holds no slot (ADR-0167). A line whose folder stands and has no record yet stays: that is the
# janitor's launch window (rule J), not this pass's. The file is read again just before the write, so a line appended meanwhile stays.
verdict_last() { awk '/"event": *"(run_start|resume)"/{v=""} /"event": *"verdict"/{v=$0} END{print v}' "$1/record.jsonl" 2>/dev/null; }
act="$SG_STATE/active-runs.txt"; drop="$SG_STATE/active-drop.$$"; : > "$drop"
[ -f "$act" ] && while read -r r; do
  case "$r" in '') continue;; /*) d=$r;; *) d="$SG_CHECKOUT/runs/$r";; esac
  # #1433: a line whose run folder is gone names no run at all. Its record went with the folder, so no rule can read it, and
  # ADR-0167 reads a listed folder that is gone as holding no slot: the line goes and the pass says which. The rest of this rule
  # reads a line that has a folder: a launch whose folder stands and has no record yet keeps its line, as the janitor's launch
  # window does (rule J), and a finished run keeps its line until SG_RUN_LINE_SECONDS have passed.
  if ! d=$(cd "$d" 2>/dev/null && pwd -P); then
    echo "$r" >> "$drop"; echo "RUN GONE ${r##*/}: its run folder is gone and its record with it; dropped from active-runs.txt"; continue; fi
  [ -f "$d/record.jsonl" ] || continue
  [ $(( now - $(mtime "$d/record.jsonl") )) -ge "${SG_RUN_LINE_SECONDS:-3600}" ] || continue
  [ -n "$(verdict_last "$d")" ] || continue
  [ -e "$SG_STATE/auto/wave-${d##*/}" ] && continue
  in_use "$d" && continue
  echo "$r" >> "$drop"; echo "RUN ENDED ${d##*/}: its run ended and its wave is not open; dropped from active-runs.txt (its tree stays)"
done < "$act"
if [ -s "$drop" ]; then grep -vxF -f "$drop" "$act" > "$act.$$" ; mv -f "$act.$$" "$act"; fi
rm -f "$drop"
$G worktree prune
# What the hub's footer reads: the tree count this pass saw in the child directories and how many of them it freed. The file
# is written last, so a pass killed halfway leaves the previous reading in place instead of a half one.
printf '{"total": %s, "cleanable": %s, "ts": %s}\n' \
  "$(grep -c '^held$' "$tally" 2>/dev/null || echo 0)" "$(grep -c '^free$' "$tally" 2>/dev/null || echo 0)" "$now" > "$SG_STATE/wt.json"
rm -f "$tally" "$arch"
