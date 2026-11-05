#!/usr/bin/env python3
"""usage: siblings.py <issues.json> <candidate> [<picked>...]   (the open-issue list the frontier pass read, ghread shape)
The sibling filter of one run (ADR-0150): exit 1 and print the reason when the candidate would be a conflicting sibling of an
issue the run already picked, exit 0 when it may join. Deterministic: it reads the spec link and the issue bodies, no model.
  spec   the candidate and a picked issue are tickets of one spec: the spec's native sub-issues (sub_issues of an open issue
         in the list), a "SPEC #N" / "Parent: #N" line in the ticket body, or a "## Parent" heading whose next
         non-blank line is "#N" (the to-tickets shape; the REST open-issue list has no sub_issues field). One run takes one ticket of a spec.
  files  issues whose bodies name one repository-relative file (normal form, cluster.paths) are ONE child (cluster.py,
         CL_MODE=files), never two siblings; so tickets of one spec that name a common file share a child and pass.
A file no body names is checked at the entry, on the two diffs (wave.sh, #1434); landing still handles what neither reads."""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster import paths  # noqa: E402

SPEC = re.compile(r"(?i)\b(?:spec|parent)\b[ \t:]*#(\d+)|^#+[ \t]*parent[ \t:]*\s*?\n(?:[ \t]*\n)*[ \t]*#(\d+)\b", re.M)


def specs(n, by):
    """The specs issue n is a ticket of."""
    out = {s for s, it in by.items() if n in (it.get("sub_issues") or [])}
    out |= {int(a or b) for a, b in SPEC.findall(by.get(n, {}).get("body") or "")}
    out.discard(n)
    return out


def children(nums, by):
    """The run's children: the issues grouped by a common file, the rule cluster.py applies."""
    par = {n: n for n in nums}
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    f = {n: paths(by.get(n, {}).get("body")) for n in nums}
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if f[a] & f[b]:
                par[find(a)] = find(b)
    return find


def conflict(cand, picked, by):
    child = children(picked + [cand], by)
    mine = specs(cand, by)
    for p in picked:
        if child(p) == child(cand):
            continue
        shared = mine & specs(p, by)
        if shared:
            return f"a sibling of #{p} in spec #{min(shared)}"
    return None


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr); return 2
    by = {int(it["number"]): it for it in json.load(open(argv[0])) if isinstance(it, dict) and "number" in it}
    why = conflict(int(argv[1]), [int(x) for x in argv[2:]], by)
    if why:
        print(why); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
