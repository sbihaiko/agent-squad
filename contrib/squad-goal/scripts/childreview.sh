#!/bin/bash
export SG_GH_PASS=${SG_GH_PASS:-childreview}
# usage: childreview.sh <run> <issue>   review the child of a run, in its own worktree, before the entry into its wave (#448)
# A child's branch reaches no remote at all (#375: its commit reaches the remote only through the wave branch), so the review
# lane that lists pushed `fix/` and `gui/` branches never sees it, and wave.sh's entry -- which waits for the review of the
# child's own diff against the wave branch (#376) -- held every child of every run for good. The review is read here, in the
# worktree the engine made for the child, against its merge-base with the run's wave branch, or with the landing branch when
# that one is newer (#655): the child's own commits, never the whole wave and never dev's. The shape is branchreview.sh's: the verdict in $SG_STATE/review-<branch with / as _>.md beside the reviewed
# head and, in review-<key>.base, the base it was read against, which the entry reads before it lets the child in.
# Prints one line: `<branch> <sha9> <verdict>`.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"
run=$1 iss=$2
{ [ -n "$run" ] && [ -n "$iss" ]; } || { echo "usage: childreview.sh <run> <issue>" >&2; exit 2; }
w="wave/$run"; rref="refs/remotes/origin/$w"
br=$(git symbolic-ref --quiet --short HEAD) || { echo "CHILDREVIEW $PWD is on no branch (#$iss)" >&2; exit 2; }
key=$(printf '%s' "$br" | tr / _)
# The wave branch as this worktree sees it: fetched from the remote the wave lives on, the ref the entry itself reads.
git fetch -q "${SG_LAND_REMOTE:-origin}" "+refs/heads/$w:$rref" 2>/dev/null
git rev-parse -q --verify "$rref" >/dev/null || { echo "CHILDREVIEW no $w on ${SG_LAND_REMOTE:-origin}: nothing to read the child against (#$iss)" >&2; exit 2; }
sha=$(git rev-parse HEAD)
# #655: the diff base is the newer of two merge-bases, the child's with the wave branch and the child's with the landing branch.
# A fix run can rebase the child onto a dev tip newer than the wave's base: the merge-base with the wave is then that old base,
# and dev's later commits read as the child's own. The review asked to change them, and the fix runs reverted landed work.
# A commit in dev is never the child's. When neither merge-base holds the other, the wave's stands, as before.
lb=${SG_LAND_BRANCH:-dev}; lref="refs/remotes/origin/$lb"
git fetch -q "${SG_LAND_REMOTE:-origin}" "+refs/heads/$lb:$lref" 2>/dev/null
rb=$(git merge-base HEAD "$rref") || { echo "CHILDREVIEW $br shares no history with $w (#$iss)" >&2; exit 2; }
db=$(git merge-base HEAD "$lref" 2>/dev/null)
[ -n "$db" ] && [ "$db" != "$rb" ] && git merge-base --is-ancestor "$rb" "$db" && rb=$db
# #769: the coder's model names the branch author, so review.sh skips it while another reviewer is reachable (#762).
# It comes from the run record above this worktree: the last outcome whose workspace path ends this worktree's path.
reason=$(python3 - "$PWD" "$SG_STATE/author-$key" <<'PY'
import json, os, sys
wt, out = sys.argv[1], sys.argv[2]
d, rec = wt, None
for _ in range(4):
    d = os.path.dirname(d)
    if os.path.isfile(os.path.join(d, "record.jsonl")): rec = os.path.join(d, "record.jsonl"); break
m = reason = None
for line in open(rec) if rec else []:
    try: e = json.loads(line)
    except ValueError: continue
    p = (e.get("workspace") or {}).get("path") if isinstance(e.get("workspace"), dict) else None
    if e.get("event") == "outcome" and p and (wt == p or wt.endswith("/" + p.strip("/"))) and isinstance(e.get("model"), str):
        m = e.get("model_id") or e["model"]
    if e.get("event") == "outcome" and p and (wt == p or wt.endswith("/" + p.strip("/"))) and e.get("outcome") == "pass" and e.get("reasons"):
        reason = " ".join(str(e["reasons"][0]).split())
if m:
    with open(out + ".tmp", "w") as f: f.write(m + "\n")
    os.replace(out + ".tmp", out)
print(reason or "")
PY
)
# #1406: a child whose tree equals its base has nothing to review. review.sh would end that as BLOCKED (the diff read is
# empty, #846), and the run would retry a review whose input never changes. Exit 6 says so: no review, no model call.
if git diff --quiet "$rb" HEAD; then
  dirty=$(git status --porcelain | sed 's/^...//' | head -5 | tr '\n' ' ')
  printf '%s\n' "${reason:-the child made no change}" > "$SG_STATE/reason-$key.tmp" && mv "$SG_STATE/reason-$key.tmp" "$SG_STATE/reason-$key"
  echo "CHILDREVIEW $br ${sha:0:9}: no change past ${rb:0:9}, nothing to review (#$iss)${dirty:+; uncommitted files, never landed: $dirty}" >&2
  exit 6
fi
out=$(SG_REVIEW_BASE=$w bash "$(dirname "$0")/review.sh" --worktree "$rb" "$iss"); rc=$?
rm=$(printf '%s\n' "$out" | head -1 | sed -n 's/^model=\([^ ]*\).*/\1/p')
printf '%s\n' "$w" > "$SG_STATE/review-$key.base"   # the base beside the reviewed head, so the entry can require the wave branch
[ -s "$SG_STATE/review-$key.md" ] || { echo "CHILDREVIEW $br: no review was written (exit $rc)" >&2; exit 1; }
# review.sh exits 0 only with a verdict for this head. A superseded review, or a chain where every model failed, writes a
# BLOCKED line and keeps no reviewed head: that is no review, so the caller drops its once-per-head claim and asks again (#483).
[ "$rc" = 0 ] || { echo "CHILDREVIEW $br ${sha:0:9}: no verdict for this head ($(head -1 "$SG_STATE/review-$key.md" | tr -d '*'), exit $rc)" >&2; exit 1; }
v=$(head -1 "$SG_STATE/review-$key.md" | tr -d '*')
{ echo "Status update — child $br @ ${sha:0:9} (run $run): review${rm:+ ($rm)} against $w: $v."; echo; tail -n +2 "$SG_STATE/review-$key.md"; } > "$(sg_cq_status "$iss" "cr$$")"
echo "$br ${sha:0:9} $v"
